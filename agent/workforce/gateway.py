"""The AI Model Gateway: every model call on this server (agent runs and mission planning) passes through here.

It does not call models itself; the providers do (AnthropicProvider, OpenAICompatProvider). The gateway decides
whether a call may start and records what it cost:

    AI_DAILY_BUDGET_USD       stop new model calls once today's spend (UTC) reaches this; unset or 0 = no cap
    AI_MONTHLY_BUDGET_USD     the same for the calendar month
    AI_MAX_CONCURRENT_TASKS   model calls in flight at once across all agents (default 5); the rest wait their turn
    AI_MAX_AGENT_STEPS        model calls per agent run: the default and the ceiling (default 8)
    AI_MAX_OUTPUT_TOKENS      output tokens per model call: the default and the ceiling
    AI_REQUEST_TIMEOUT        seconds per model call: the default and the ceiling
    AI_BREAKER_FAILURES       consecutive failed calls to one provider that open its circuit breaker (default 5)
    AI_BREAKER_COOLDOWN       seconds the breaker stays open before one call is let through again (default 60)

Spend is the usage each API returned times the model's published price, kept per UTC day in wf_ai_usage, so a
restart does not reset it. A call with no known price is counted as unpriced, and the budget check says so instead
of pretending the spend is zero.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterator

from agent.store import _now

from .chain import Cancelled

SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_ai_usage (
    day           TEXT NOT NULL,                     -- UTC date, YYYY-MM-DD
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    calls         INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,
    unpriced      INTEGER NOT NULL DEFAULT 0,        -- calls whose model had no known price
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      TEXT NOT NULL DEFAULT '0',
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (day, provider, model)
);
"""


def _decimal(v: str | None, name: str) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation:
        raise ValueError(f"{name} 应该是数字（美元），现在是 {v!r}") from None
    if d < 0:
        raise ValueError(f"{name} 不能是负数")
    return d or None  # 0 = no cap


def _int(v: str | None, name: str, default: int | None, lo: int, hi: int) -> int | None:
    if v in (None, ""):
        return default
    try:
        n = int(str(v).strip())
    except ValueError:
        raise ValueError(f"{name} 应该是整数，现在是 {v!r}") from None
    if not lo <= n <= hi:
        raise ValueError(f"{name} 应该在 {lo} 到 {hi} 之间")
    return n


class Gateway:
    def __init__(self, db, *, daily_budget: Decimal | None = None, monthly_budget: Decimal | None = None, max_concurrent: int = 5,
                 max_steps: int | None = None, max_output_tokens: int | None = None, timeout_s: int | None = None,
                 breaker_failures: int = 5, breaker_cooldown_s: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")) -> None:
        self._db, self._lock = db._db, db._lock
        with self._lock:
            self._db.executescript(SCHEMA)
        self.daily_budget = daily_budget
        self.monthly_budget = monthly_budget
        self.max_concurrent = max_concurrent
        self.max_steps = max_steps
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.breaker_failures = breaker_failures
        self.breaker_cooldown_s = breaker_cooldown_s
        self.clock = clock
        self.today = today
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._in_flight = 0
        self._state_lock = threading.Lock()
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._last_error: dict[str, str] = {}

    @classmethod
    def from_env(cls, db, env: dict, **kw) -> "Gateway":
        return cls(db, daily_budget=_decimal(env.get("AI_DAILY_BUDGET_USD"), "AI_DAILY_BUDGET_USD"),
                   monthly_budget=_decimal(env.get("AI_MONTHLY_BUDGET_USD"), "AI_MONTHLY_BUDGET_USD"),
                   max_concurrent=_int(env.get("AI_MAX_CONCURRENT_TASKS"), "AI_MAX_CONCURRENT_TASKS", 5, 1, 100),
                   max_steps=_int(env.get("AI_MAX_AGENT_STEPS"), "AI_MAX_AGENT_STEPS", None, 1, 30),
                   max_output_tokens=_int(env.get("AI_MAX_OUTPUT_TOKENS"), "AI_MAX_OUTPUT_TOKENS", None, 256, 64000),
                   timeout_s=_int(env.get("AI_REQUEST_TIMEOUT"), "AI_REQUEST_TIMEOUT", None, 5, 600),
                   breaker_failures=_int(env.get("AI_BREAKER_FAILURES"), "AI_BREAKER_FAILURES", 5, 1, 100),
                   breaker_cooldown_s=_int(env.get("AI_BREAKER_COOLDOWN"), "AI_BREAKER_COOLDOWN", 60, 1, 86400), **kw)

    # ---- limits applied to every run ----

    def cap(self, limits) -> None:
        """Server ceilings over an agent's own limits (an agent can ask for less, never more)."""
        for attr, cap in (("max_model_calls", self.max_steps), ("max_tokens", self.max_output_tokens), ("call_timeout_s", self.timeout_s)):
            if cap is not None:
                setattr(limits, attr, type(getattr(limits, attr))(min(getattr(limits, attr), cap)))

    # ---- may a call start? ----

    def spent(self, period: str) -> tuple[Decimal, int]:
        """(USD, unpriced calls) for 'day' (today, UTC) or 'month'."""
        day = self.today()
        where, arg = ("day = ?", day) if period == "day" else ("day LIKE ?", day[:7] + "-%")
        with self._lock:
            rows = self._db.execute(f"SELECT cost_usd, unpriced FROM wf_ai_usage WHERE {where}", (arg,)).fetchall()
        return sum((Decimal(r["cost_usd"]) for r in rows), Decimal(0)), sum(int(r["unpriced"]) for r in rows)

    def blocked(self, provider: str) -> str | None:
        """None if a new model call may start, else the reason it may not."""
        for period, cap, label in (("day", self.daily_budget, "今日"), ("month", self.monthly_budget, "本月")):
            if cap is None:
                continue
            spent, _ = self.spent(period)
            if spent >= cap:
                return f"AI 预算已用完：{label}已花 ${spent.quantize(Decimal('0.0001'))}，上限 ${cap}（{'AI_DAILY_BUDGET_USD' if period == 'day' else 'AI_MONTHLY_BUDGET_USD'}）。新的模型调用已停止"
        with self._state_lock:
            until = self._open_until.get(provider)
            if until is not None:
                left = until - self.clock()
                if left > 0:
                    return (f"模型服务 {provider} 连续失败 {self._fails.get(provider, 0)} 次，熔断中，{int(left) + 1} 秒后再试"
                            f"（最后一次错误：{self._last_error.get(provider, '')[:160]}）")
        return None

    @contextmanager
    def slot(self, should_stop: Callable[[], bool] = lambda: False) -> Iterator[None]:
        """Wait for one of AI_MAX_CONCURRENT_TASKS call slots; a cancelled task stops waiting."""
        while not self._slots.acquire(timeout=0.2):
            if should_stop():
                raise Cancelled("任务已取消")
        with self._state_lock:
            self._in_flight += 1
        try:
            yield
        finally:
            with self._state_lock:
                self._in_flight -= 1
            self._slots.release()

    # ---- after a call ----

    def succeeded(self, provider: str) -> None:
        with self._state_lock:
            self._fails[provider] = 0
            self._open_until.pop(provider, None)

    def failed(self, provider: str, model: str, error: BaseException) -> None:
        """A call that ended in an error after the provider's own retries; enough in a row open the breaker."""
        self._add(provider, model, failures=1)
        with self._state_lock:
            n = self._fails.get(provider, 0) + 1
            self._fails[provider] = n
            self._last_error[provider] = f"{type(error).__name__}: {error}"
            if n >= self.breaker_failures:
                self._open_until[provider] = self.clock() + self.breaker_cooldown_s

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int, cost: Decimal | None) -> None:
        self._add(provider, model or "?", calls=1, unpriced=int(cost is None), input_tokens=input_tokens, output_tokens=output_tokens,
                  cost=cost or Decimal(0))

    def _add(self, provider: str, model: str, *, calls: int = 0, failures: int = 0, unpriced: int = 0, input_tokens: int = 0,
             output_tokens: int = 0, cost: Decimal = Decimal(0)) -> None:
        day = self.today()
        with self._lock:
            r = self._db.execute("SELECT cost_usd FROM wf_ai_usage WHERE day = ? AND provider = ? AND model = ?", (day, provider, model)).fetchone()
            if r is None:
                self._db.execute("INSERT INTO wf_ai_usage (day, provider, model, calls, failures, unpriced, input_tokens, output_tokens, cost_usd, updated_at)"
                                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (day, provider, model, calls, failures, unpriced, input_tokens, output_tokens, str(cost), _now()))
            else:
                self._db.execute("UPDATE wf_ai_usage SET calls = calls + ?, failures = failures + ?, unpriced = unpriced + ?,"
                                 " input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, cost_usd = ?, updated_at = ?"
                                 " WHERE day = ? AND provider = ? AND model = ?",
                                 (calls, failures, unpriced, input_tokens, output_tokens, str(Decimal(r["cost_usd"]) + cost), _now(), day, provider, model))

    # ---- reporting ----

    def usage(self, days: int = 31) -> dict:
        with self._lock:
            rows = [dict(r) for r in self._db.execute(
                "SELECT day, provider, model, calls, failures, unpriced, input_tokens, output_tokens, cost_usd FROM wf_ai_usage"
                " ORDER BY day DESC, provider, model LIMIT ?", (days * 10,)).fetchall()]
        today, _ = self.spent("day")
        month, unpriced = self.spent("month")
        with self._state_lock:
            breakers = {p: {"failures": self._fails.get(p, 0), "open_for_s": max(0, round(u - self.clock()))}
                        for p, u in self._open_until.items() if u > self.clock()}
            in_flight = self._in_flight
        return {"today_usd": str(today), "month_usd": str(month), "unpriced_calls_this_month": unpriced,
                "daily_budget_usd": str(self.daily_budget) if self.daily_budget else None,
                "monthly_budget_usd": str(self.monthly_budget) if self.monthly_budget else None,
                "max_concurrent": self.max_concurrent, "in_flight": in_flight, "max_agent_steps": self.max_steps,
                "max_output_tokens": self.max_output_tokens, "request_timeout_s": self.timeout_s,
                "breaker": {"failures": self.breaker_failures, "cooldown_s": self.breaker_cooldown_s, "open": breakers},
                "days": rows}
