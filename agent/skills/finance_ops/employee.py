"""The AI finance ops employee, independent of any chat platform.

Flow for every request: parse (suggestion) -> rule engine (decision) -> ledger
row written -> executor (AUTO_PAY) or Safe proposer (NEEDS_APPROVAL) -> reply.
The bot and the console only forward input and show output.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from agent.config import load_policy  # noqa: F401  (re-exported for callers)
from agent.execution import DryRunExecutor, Executor, SafeProposer
from agent.rules import Decision, Outcome, PaymentRequest, Policy, Reason, evaluate
from agent.rules.engine import day_bounds
from agent.store import PENDING, PaymentRecord, Store

from .insights import TelegramSettings
from .parser import ParsedRequest, ParseError, parse
from .reports import last_full_week, outcome_counts, weekly_report

PolicySource = Callable[[], tuple[Policy, frozenset[str]]]


@dataclass(frozen=True)
class Reply:
    text: str
    decision: Decision | None = None
    request: PaymentRequest | None = None


def _fmt(amount: Decimal) -> str:
    return f"{amount.normalize():,f}"


class FinanceOpsEmployee:
    def __init__(
        self,
        policy_source: PolicySource,
        store: Store | None = None,
        executor: Executor | None = None,
        proposer: SafeProposer | None = None,
        explorer_tx_url: str | None = None,
        treasury_balance: Callable[[], dict[str, Decimal]] | None = None,
    ) -> None:
        self._policy_source = policy_source
        self.store = store or Store()
        self.executor = executor or DryRunExecutor()
        self.proposer = proposer
        self.explorer_tx_url = explorer_tx_url
        self.treasury_balance = treasury_balance

    @classmethod
    def fixed(cls, policy: Policy, owners: frozenset[str], **kw) -> "FinanceOpsEmployee":
        return cls(lambda: (policy, owners), **kw)

    # ---- policy ----

    @property
    def policy(self) -> Policy:
        policy, _ = self._policy_source()
        # The kill switch lives in the store, so /pause and the console button both work.
        return replace(policy, paused=policy.paused or self.store.paused) if self.store.paused else policy

    @property
    def owners(self) -> frozenset[str]:
        return self._policy_source()[1]

    def is_owner(self, *identities: str | int | None) -> bool:
        owners = self.owners
        return any(i is not None and str(i).lstrip("@").lower() in owners for i in identities)

    def _name(self, address: str) -> str:
        p = self.policy.whitelist.get(address.lower())
        return p.name if p else f"{address[:6]}…{address[-4:]}"

    # ---- payment requests ----

    def handle_request(self, text: str, requester: str, request_id: str, at: datetime | None = None) -> Reply:
        at = at or datetime.now(timezone.utc)
        policy = self.policy
        parsed = parse(text, policy.whitelist.values())
        if isinstance(parsed, ParseError):
            return Reply(parsed.question)

        req = parsed.to_payment_request(request_id, requester, at)
        decision = evaluate(req, policy, self.store)
        if Reason.ALREADY_PROCESSED in decision.reasons:
            # Redelivered message: never record or execute it again.
            return Reply(f"{req.request_id} 已经处理过，不会重复付款。", decision, req)
        try:
            self.store.record(req, decision)  # written BEFORE any money moves
        except ValueError:
            return Reply(f"{req.request_id} 已经处理过，不会重复付款。", decision, req)

        tx = error = safe_hash = None
        if decision.outcome is Outcome.AUTO_PAY:
            try:
                tx = self.executor.pay(req)
                self.store.mark_paid(req.request_id, tx)
            except Exception as e:  # noqa: BLE001 - any wallet/RPC failure ends this request
                error = str(e) or type(e).__name__
                self.store.mark_failed(req.request_id, error)
        elif decision.outcome is Outcome.NEEDS_APPROVAL and self.proposer is not None:
            try:
                safe_hash = self.proposer.propose(req).safe_tx_hash
                self.store.set_safe_tx_hash(req.request_id, safe_hash)
            except Exception as e:  # noqa: BLE001
                error = str(e) or type(e).__name__
        return Reply(self._describe(parsed, req, decision, tx, safe_hash, error), decision, req)

    def simulate(self, text: str, requester: str = "console", at: datetime | None = None) -> dict:
        """Run a request through the parser and the rules without recording or paying anything."""
        at = at or datetime.now(timezone.utc)
        parsed = parse(text, self.policy.whitelist.values())
        if isinstance(parsed, ParseError):
            return {"parsed": False, "reply": parsed.question}
        req = parsed.to_payment_request(f"sim-{uuid.uuid4().hex[:8]}", requester, at)
        d = evaluate(req, self.policy, self.store)
        preview = "（试算，不会付款）" if d.outcome is Outcome.AUTO_PAY else None
        return {
            "parsed": True,
            "outcome": d.outcome.value,
            "reasons": [r.value for r in d.reasons],
            "messages": list(d.messages),
            "payee": req.payee,
            "payee_name": parsed.payee_name,
            "amount": _fmt(req.amount),
            "token": req.token,
            "category": req.category,
            "memo": req.memo,
            "token_assumed": parsed.token_assumed,
            "reply": self._describe(parsed, req, d, preview, None, None),
        }

    def quiet_auto_pay(self) -> bool:
        return TelegramSettings.load(self.store).quiet_auto_pay

    def _describe(self, parsed: ParsedRequest, req: PaymentRequest, d: Decision, tx, safe_hash, error) -> str:
        who = parsed.payee_name or req.payee
        line = f"{who} · {_fmt(req.amount)} {req.token}"
        if req.memo:
            line += f" · {req.memo}"
        notes = []
        if parsed.token_assumed:
            notes.append(f"没写币种，按 {req.token} 处理")
        reasons = "\n".join(f"• {m}" for m in d.messages)

        if d.outcome is Outcome.AUTO_PAY and error:
            body = f"❌ {req.request_id} 付款没有成功\n{line}\n错误：{error}\n这笔请求已作废，确认钱包状态后请重新发送。"
        elif d.outcome is Outcome.AUTO_PAY and self.quiet_auto_pay():
            return f"✅ {req.request_id} 已付 {line}"
        elif d.outcome is Outcome.AUTO_PAY:
            spent = self._spent_today(req.token, req.created_at)
            daily = self.policy.daily_limit.get(req.token, Decimal(0))
            link = f"{self.explorer_tx_url}{tx}" if self.explorer_tx_url and self.executor.live else tx
            body = f"✅ {req.request_id} 已付款\n{line}\n交易：{link}\n今日已自动付 {_fmt(spent)} / {_fmt(daily)} {req.token}"
            if not self.executor.live:
                notes.append("测试模式：没有真的上链")
        elif d.outcome is Outcome.NEEDS_APPROVAL:
            body = f"✍️ {req.request_id} 需要老板签字\n{line}\n原因：\n{reasons}"
            if safe_hash:
                body += f"\nSafe 交易哈希：{safe_hash}"
                notes.append("提案草稿已生成，老板在 Safe 里签字执行后发 /approve " + req.request_id)
            else:
                if error:
                    notes.append(f"Safe 提案生成失败：{error}")
                notes.append(f"请老板手动付款后发 /approve {req.request_id}，不付就发 /reject {req.request_id}")
        else:
            body = f"🛑 {req.request_id} 已拦截，没有发出任何交易\n{line}\n原因：\n{reasons}"
        if notes:
            body += "\n（" + "；".join(notes) + "）"
        return body

    def _spent_today(self, token: str, at: datetime) -> Decimal:
        start, end = day_bounds(at, self.policy.timezone)
        return self.store.auto_paid_total(token, start, end)

    # ---- owner commands ----

    def _actor(self, identities) -> str:
        return next((str(i) for i in identities if i is not None), "owner")

    def _denied(self, action: str, identities) -> str:
        self.store.log_event("audit", f"非老板尝试{action}，已拒绝", self._actor(identities))
        return f"只有老板可以{action}。"

    def pause(self, *identities) -> str:
        if not self.is_owner(*identities):
            return self._denied("暂停付款", identities)
        self.store.set_paused(True)
        self.store.log_event("audit", "暂停所有付款", self._actor(identities))
        return "⏸ 已暂停所有付款。之后的请求都会被拦截，直到老板发 /resume。"

    def resume(self, *identities) -> str:
        if not self.is_owner(*identities):
            return self._denied("恢复付款", identities)
        self.store.set_paused(False)
        self.store.log_event("audit", "恢复付款", self._actor(identities))
        return "▶️ 已恢复付款，规则照旧生效。"

    def approve(self, request_id: str, *identities) -> str:
        if not self.is_owner(*identities):
            return self._denied("确认审批", identities)
        by = self._actor(identities)
        if self.store.approve(request_id, by):
            self.store.log_event("audit", f"签字确认 {request_id}", by, {"request_id": request_id})
            return f"✅ {request_id} 已记为老板签字付款。"
        return self._not_pending(request_id)

    def reject(self, request_id: str, *identities) -> str:
        if not self.is_owner(*identities):
            return self._denied("驳回付款", identities)
        by = self._actor(identities)
        if self.store.reject(request_id, by):
            self.store.log_event("audit", f"驳回 {request_id}", by, {"request_id": request_id})
            return f"🚫 {request_id} 已驳回，不会付款。"
        return self._not_pending(request_id)

    def _not_pending(self, request_id: str) -> str:
        rec = self.store.get(request_id)
        if rec is None:
            return f"找不到 {request_id}。发 /pending 看待签字的付款。"
        return f"{request_id} 当前状态是「{STATUS_LABEL.get(rec.status, rec.status)}」，不在待签字列表里。"

    def pending_text(self) -> str:
        rows = self.store.pending()
        if not rows:
            return "没有等待签字的付款。"
        lines = ["✍️ 等待老板签字"]
        for r in rows:
            lines.append(f"• {r.request.request_id} {self._name(r.request.payee)} {_fmt(r.request.amount)} {r.request.token} · {r.request.memo or '-'}")
        lines.append("签完发 /approve 编号，不付发 /reject 编号")
        return "\n".join(lines)

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
        if not self.executor.live:
            lines.append("当前是测试模式：绿灯付款不会真的上链")
        return "\n".join(lines)

    def today_text(self, at: datetime | None = None) -> str:
        at = at or datetime.now(timezone.utc)
        start, end = day_bounds(at, self.policy.timezone)
        rows = self.store.between(start, end)
        day = at.astimezone(ZoneInfo(self.policy.timezone)).date()
        if not rows:
            return f"📒 {day} 还没有付款请求。"
        lines = [f"📒 {day} 账本"]
        for r in rows:
            lines.append(f"{STATUS_LABEL.get(r.status, r.status)} {r.request.request_id} · {_fmt(r.request.amount)} {r.request.token} · {r.request.memo or '-'}")
        lines.append(f"共 {len(rows)} 笔：" + outcome_counts(rows))
        return "\n".join(lines)

    def weekly_report_text(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        start, end = last_full_week(now, self.policy.timezone)
        balance = None
        if self.treasury_balance:
            try:
                balance = self.treasury_balance()
            except Exception:  # noqa: BLE001 - a report without balance beats no report
                balance = None
        names = {addr: p.name for addr, p in self.policy.whitelist.items()}
        return weekly_report(self.store.between(start, end), start, end, names, balance)

    def records(self, limit: int = 100) -> list[PaymentRecord]:
        return self.store.latest(limit)


STATUS_LABEL = {
    "executing": "⏳ 执行中",
    "paid": "✅ 已付",
    "failed": "❌ 失败",
    PENDING: "✍️ 待签",
    "approved": "🖊 老板已签",
    "rejected": "🚫 已驳回",
    "blocked": "🛑 拦截",
}
