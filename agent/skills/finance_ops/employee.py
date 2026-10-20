"""The AI finance ops employee, independent of any chat platform.

Flow for every message: parse (suggestion) -> rule engine (decision) -> executor
(only for AUTO_PAY) -> ledger -> reply text. The bot layer only forwards text in
and replies out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from agent.rules.engine import day_bounds
from agent.rules import (
    Decision,
    InMemoryLedger,
    Outcome,
    Payee,
    PaymentRequest,
    Policy,
    evaluate,
)

from .parser import ParsedRequest, ParseError, parse


class Executor(Protocol):
    def pay(self, req: PaymentRequest) -> str:
        """Send the payment and return a transaction hash."""


class DryRunExecutor:
    """Stand-in until the wallet is wired up: moves no money, says so in every reply."""

    live = False

    def pay(self, req: PaymentRequest) -> str:
        return f"dry-run:{req.request_id}"


@dataclass(frozen=True)
class Reply:
    text: str
    decision: Decision | None = None
    request: PaymentRequest | None = None


def _fmt(amount: Decimal) -> str:
    return f"{amount.normalize():,f}"


def load_policy(path: str | Path) -> tuple[Policy, frozenset[str]]:
    """Read a policy JSON file. Returns (policy, owner handles/ids)."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    payees = [
        Payee(p["address"], p["name"], tuple(p.get("aliases", ())), bool(p.get("needs_approval", False)))
        for p in cfg["payees"]
    ]
    policy = Policy.build(
        payees,
        single_limit=cfg["single_limit"],
        daily_limit=cfg["daily_limit"],
        approval_categories=frozenset(cfg.get("approval_categories", ())),
        risk_addresses=cfg.get("risk_addresses", ()),
        duplicate_window=timedelta(days=cfg.get("duplicate_window_days", 30)),
        timezone=cfg.get("timezone", "UTC"),
        read_only=bool(cfg.get("read_only", False)),
    )
    owners = frozenset(str(o).lstrip("@").lower() for o in cfg.get("owners", ()))
    return policy, owners


class FinanceOpsEmployee:
    def __init__(
        self,
        policy: Policy,
        owners: frozenset[str],
        ledger: InMemoryLedger | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.policy = policy
        self.owners = owners
        self.ledger = ledger or InMemoryLedger()
        self.executor = executor or DryRunExecutor()
        self._requests: list[tuple[PaymentRequest, Decision, str | None]] = []

    # ---- payment requests ----

    def handle_request(self, text: str, requester: str, request_id: str, at: datetime | None = None) -> Reply:
        at = at or datetime.now(timezone.utc)
        parsed = parse(text, self.policy.whitelist.values())
        if isinstance(parsed, ParseError):
            return Reply(parsed.question)

        req = parsed.to_payment_request(request_id, requester, at)
        decision = evaluate(req, self.policy, self.ledger)
        tx = None
        if decision.outcome is Outcome.AUTO_PAY:
            tx = self.executor.pay(req)
        # Record after execution so a crash mid-payment can't leave an unpaid "paid" entry.
        # Duplicate request ids are already BLOCKed by the engine and never re-recorded.
        if not self.ledger.has_request(req.request_id):
            self.ledger.record(req, decision)
        self._requests.append((req, decision, tx))
        return Reply(self._describe(parsed, req, decision, tx), decision, req)

    def _describe(self, parsed: ParsedRequest, req: PaymentRequest, d: Decision, tx: str | None) -> str:
        who = parsed.payee_name or req.payee
        line = f"{who} · {_fmt(req.amount)} {req.token}"
        if req.memo:
            line += f" · {req.memo}"
        notes = []
        if parsed.token_assumed:
            notes.append(f"没写币种，按 {req.token} 处理")
        reasons = "\n".join(f"• {m}" for m in d.messages)

        if d.outcome is Outcome.AUTO_PAY:
            spent = self._spent_today(req.token, req.created_at)
            daily = self.policy.daily_limit.get(req.token, Decimal(0))
            body = f"✅ {req.request_id} 已付款\n{line}\n交易：{tx}\n今日已自动付 {_fmt(spent)} / {_fmt(daily)} {req.token}"
            if not getattr(self.executor, "live", True):
                notes.append("测试模式：没有真的上链")
        elif d.outcome is Outcome.NEEDS_APPROVAL:
            body = f"✍️ {req.request_id} 需要老板签字\n{line}\n原因：\n{reasons}"
            notes.append("Safe 提案功能接入前，请老板手动付款")
        else:
            body = f"🛑 {req.request_id} 已拦截，没有发出任何交易\n{line}\n原因：\n{reasons}"
        if notes:
            body += "\n（" + "；".join(notes) + "）"
        return body

    # ---- owner commands ----

    def is_owner(self, *identities: str | int | None) -> bool:
        return any(i is not None and str(i).lstrip("@").lower() in self.owners for i in identities)

    def pause(self, *identities) -> str:
        if not self.is_owner(*identities):
            return "只有老板可以暂停付款。"
        self.policy = replace(self.policy, paused=True)
        return "⏸ 已暂停所有付款。之后的请求都会被拦截，直到老板发 /resume。"

    def resume(self, *identities) -> str:
        if not self.is_owner(*identities):
            return "只有老板可以恢复付款。"
        self.policy = replace(self.policy, paused=False)
        return "▶️ 已恢复付款，规则照旧生效。"

    def rules_text(self) -> str:
        p = self.policy
        lines = ["📋 当前规则"]
        if p.paused:
            lines.append("⏸ 付款已暂停")
        if p.read_only:
            lines.append("试用期只读：所有付款都要老板确认")
        for token in sorted(set(p.single_limit) | set(p.daily_limit)):
            s = p.single_limit.get(token, Decimal(0))
            d = p.daily_limit.get(token, Decimal(0))
            lines.append(f"{token}：单笔 ≤ {_fmt(s)}，每日自动付 ≤ {_fmt(d)}")
        if p.approval_categories:
            lines.append("需审批类别：" + "、".join(sorted(p.approval_categories)))
        lines.append(f"重复检测：{p.duplicate_window.days} 天内同收款方同金额")
        lines.append("白名单：")
        for payee in p.whitelist.values():
            flag = "（需审批）" if payee.needs_approval else ""
            lines.append(f"• {payee.name}{flag} {payee.address[:6]}…{payee.address[-4:]}")
        return "\n".join(lines)

    def today_text(self, at: datetime | None = None) -> str:
        at = at or datetime.now(timezone.utc)
        tz = ZoneInfo(self.policy.timezone)
        day = at.astimezone(tz).date()
        rows = [(r, d) for r, d, _ in self._requests if r.created_at.astimezone(tz).date() == day]
        if not rows:
            return f"📒 {day} 还没有付款请求。"
        label = {Outcome.AUTO_PAY: "✅ 已付", Outcome.NEEDS_APPROVAL: "✍️ 待签", Outcome.BLOCK: "🛑 拦截"}
        lines = [f"📒 {day} 账本"]
        for r, d in rows:
            lines.append(f"{label[d.outcome]} {r.request_id} · {_fmt(r.amount)} {r.token} · {r.memo or '-'}")
        counts = {o: sum(1 for _, d in rows if d.outcome is o) for o in Outcome}
        lines.append(
            f"共 {len(rows)} 笔：自动付 {counts[Outcome.AUTO_PAY]}，待签 {counts[Outcome.NEEDS_APPROVAL]}，拦截 {counts[Outcome.BLOCK]}"
        )
        return "\n".join(lines)

    def _spent_today(self, token: str, at: datetime) -> Decimal:
        start, end = day_bounds(at, self.policy.timezone)
        return self.ledger.auto_paid_total(token, start, end)
