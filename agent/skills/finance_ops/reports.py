"""Weekly finance report built from the ledger."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from agent.rules import Outcome
from agent.store import APPROVED, EXECUTING, FAILED, PAID, PaymentRecord

# Money that actually left (or was signed off to leave).
_MOVED = (PAID, APPROVED, EXECUTING)
WEEKS_PER_MONTH = Decimal(52) / Decimal(12)


def week_bounds(any_day: date, tz: str) -> tuple[datetime, datetime]:
    """Monday 00:00 to next Monday 00:00 in the owner's timezone."""
    monday = any_day - timedelta(days=any_day.weekday())
    start = datetime.combine(monday, time.min, tzinfo=ZoneInfo(tz))
    return start, start + timedelta(days=7)


def last_full_week(now: datetime, tz: str) -> tuple[datetime, datetime]:
    return week_bounds(now.astimezone(ZoneInfo(tz)).date() - timedelta(days=7), tz)


def _fmt(a: Decimal) -> str:
    return f"{a.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,f}".rstrip("0").rstrip(".")


def outcome_counts(records: Iterable[PaymentRecord], approval: str = "待签") -> str:
    """"自动付 9，失败 1，待签 1，拦截 1": a failed auto-payment moved no money, so it isn't counted as paid."""
    records = list(records)
    count = {o: sum(1 for r in records if r.outcome is o) for o in Outcome}
    failed = sum(1 for r in records if r.status == FAILED)
    parts = [f"自动付 {count[Outcome.AUTO_PAY] - failed}"]
    if failed:
        parts.append(f"失败 {failed}")
    parts += [f"{approval} {count[Outcome.NEEDS_APPROVAL]}", f"拦截 {count[Outcome.BLOCK]}"]
    return "，".join(parts)


def weekly_report(
    records: Iterable[PaymentRecord],
    start: datetime,
    end: datetime,
    payee_names: Mapping[str, str] | None = None,
    treasury_balance: Mapping[str, Decimal] | None = None,
) -> str:
    records = list(records)
    names = {k.lower(): v for k, v in (payee_names or {}).items()}
    out: dict[str, Decimal] = defaultdict(Decimal)
    by_cat: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for r in records:
        if r.status in _MOVED:
            out[r.request.token] += r.request.amount
            by_cat[(r.request.token, r.request.category or "其他")] += r.request.amount

    lines = [f"📊 周报 {start:%m-%d} 至 {(end - timedelta(days=1)):%m-%d}"]
    if not records:
        lines.append("本周没有付款请求。")
    else:
        lines.append(
            f"付款请求 {len(records)} 笔：" + outcome_counts(records, approval="老板审批")
        )

    if out:
        lines.append("")
        lines.append("本周支出")
        for token in sorted(out):
            lines.append(f"• {token}：{_fmt(out[token])}")
            for (t, cat), amt in sorted(by_cat.items(), key=lambda kv: -kv[1]):
                if t == token:
                    lines.append(f"    {cat} {_fmt(amt)}")

    if treasury_balance:
        lines.append("")
        lines.append("国库余额")
        for token, bal in sorted(treasury_balance.items()):
            line = f"• {token}：{_fmt(bal)}"
            spent = out.get(token)
            if spent:
                months = bal / (spent * WEEKS_PER_MONTH)
                line += f"，按本周速度约可撑 {months.quantize(Decimal('0.1'))} 个月"
            lines.append(line)

    blocked = [r for r in records if r.outcome is Outcome.BLOCK]
    if blocked:
        lines.append("")
        lines.append("被拦截的请求")
        for r in blocked:
            who = names.get(r.request.payee.lower(), r.request.payee[:6] + "…" + r.request.payee[-4:])
            why = "；".join(r.messages) or "-"
            lines.append(f"• {r.request.request_id} {who} {_fmt(r.request.amount)} {r.request.token}：{why}")

    pending = [r for r in records if r.status == "pending_approval"]
    if pending:
        lines.append("")
        lines.append(f"还有 {len(pending)} 笔等老板签字：" + "、".join(r.request.request_id for r in pending))
    return "\n".join(lines)
