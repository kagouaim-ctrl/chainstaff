"""Deterministic payment rule engine. See docs/specs/rules_engine.md.

The model never decides whether money moves; this function does. It reads only
structured fields; free text (memo, category wording) cannot change a result.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .ledger import LedgerView
from .models import (
    ADDRESS_RE,
    RED_REASONS,
    Decision,
    Outcome,
    PaymentRequest,
    Policy,
    Reason,
    normalize_address,
)


def day_bounds(at: datetime, tz: str) -> tuple[datetime, datetime]:
    local = at.astimezone(ZoneInfo(tz))
    start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    return start, start + timedelta(days=1)


def _fmt(amount: Decimal) -> str:
    return f"{amount.normalize():f}"


def evaluate(req: PaymentRequest, policy: Policy, ledger: LedgerView) -> Decision:
    hits: list[tuple[Reason, str]] = []
    hit = lambda r, msg: hits.append((r, msg))  # noqa: E731

    token = req.token.upper()
    payee = normalize_address(req.payee)
    amount = req.amount

    # ---- red ----
    if policy.paused:
        hit(Reason.PAUSED, "付款已被一键暂停")

    decimals = policy.token_decimals.get(token)
    amount_ok = amount.is_finite() and amount > 0
    # Exponent check: more fractional digits than the token supports can't be sent exactly.
    if amount_ok and decimals is not None and amount.as_tuple().exponent < -decimals:
        amount_ok = False
    if not amount_ok:
        hit(Reason.INVALID_AMOUNT, f"金额无效：{req.amount}")

    if decimals is None:
        hit(Reason.UNSUPPORTED_TOKEN, f"不支持的币种：{req.token}")

    address_ok = bool(ADDRESS_RE.match(req.payee.strip()))
    if not address_ok:
        hit(Reason.INVALID_ADDRESS, f"收款地址格式不对：{req.payee}")

    if ledger.has_request(req.request_id):
        hit(Reason.ALREADY_PROCESSED, f"请求 {req.request_id} 已处理过")

    if payee in policy.risk_addresses:
        hit(Reason.RISK_FLAGGED, "收款地址有风险标记")

    entry = policy.whitelist.get(payee)
    if entry is None:
        hit(Reason.NOT_WHITELISTED, "收款地址不在白名单")

    if address_ok and amount_ok:
        since = req.created_at - policy.duplicate_window
        for e in ledger.recent_payments(payee, token, since):
            if e.request.amount == amount and e.request.request_id != req.request_id:
                hit(
                    Reason.DUPLICATE,
                    f"{policy.duplicate_window.days} 天内已有相同金额的付款（{e.request.request_id}）",
                )
                break

    # ---- amber ----
    if policy.read_only:
        hit(Reason.READ_ONLY_MODE, "试用期只读，所有付款需老板确认")

    if amount_ok:
        # Missing limit means 0: never default to "allow".
        single = policy.single_limit.get(token, Decimal(0))
        if amount > single:
            hit(Reason.OVER_SINGLE_LIMIT, f"超过单笔上限 {_fmt(single)} {token}")

        daily = policy.daily_limit.get(token, Decimal(0))
        start, end = day_bounds(req.created_at, policy.timezone)
        spent = ledger.auto_paid_total(token, start, end)
        if spent + amount > daily:
            hit(
                Reason.OVER_DAILY_LIMIT,
                f"今日已自动付 {_fmt(spent)}，加上本笔超过每日上限 {_fmt(daily)} {token}",
            )

    if req.category and req.category in policy.approval_categories:
        hit(Reason.CATEGORY_NEEDS_APPROVAL, f"类别「{req.category}」需要审批")

    if entry is not None and entry.needs_approval:
        hit(Reason.PAYEE_NEEDS_APPROVAL, f"收款方 {entry.name} 需要审批")

    reasons = tuple(r for r, _ in hits)
    if any(r in RED_REASONS for r in reasons):
        outcome = Outcome.BLOCK
    elif reasons:
        outcome = Outcome.NEEDS_APPROVAL
    else:
        outcome = Outcome.AUTO_PAY
    return Decision(req.request_id, outcome, reasons, tuple(m for _, m in hits))
