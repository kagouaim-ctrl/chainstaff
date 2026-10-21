"""One Telegram bot, several AI employees: decides who handles each message and command.

Order for an ordinary group message:
  1. community support scans it for scams first (a scammer's "payment request" is still a scam)
  2. a payment request goes to the finance ops employee
  3. anything else goes to community support (FAQ, transaction lookups, hand-off to a human)
An employee that is switched off in the console simply stops answering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from agent.skills.airdrop_sybil.sybil import SybilChecker
from agent.skills.campaign_payouts.payouts import CampaignPayouts
from agent.skills.chain_monitor.monitor import ChainMonitor
from agent.skills.community_support.support import CommunitySupport
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.skills.finance_ops.schedule import due_messages
from agent.skills.translator.translator import TranslationError, Translator
from agent.team import ROLE_BY_ID, Team

PAYMENT_PREFIX = re.compile(r"^\s*(付|给|转|pay\b|send\b|transfer\b)", re.I)

FINANCE, SUPPORT, MONITOR, TRANSLATOR = "finance_ops", "community_support", "chain_monitor", "announcement_translator"
PAYOUTS, SYBIL = "campaign_payouts", "airdrop_sybil"
COMMAND_ROLES = {
    "rules": FINANCE, "today": FINANCE, "pending": FINANCE, "report": FINANCE,
    "approve": FINANCE, "reject": FINANCE, "pause": FINANCE, "resume": FINANCE,
    "faq": SUPPORT, "tx": SUPPORT, "human": SUPPORT, "tickets": SUPPORT,
    "watch": MONITOR, "alerts": MONITOR, "brief": MONITOR,
    "translate": TRANSLATOR, "post": TRANSLATOR, "discard": TRANSLATOR,
    "payouts": PAYOUTS, "sybil": SYBIL,
}
# Commands that change something; the console's chat simulator shows them but never runs them.
MUTATING = {"approve", "reject", "pause", "resume", "human", "translate", "post", "discard"}


@dataclass
class Incoming:
    text: str
    username: str | None = None
    user_id: int | None = None
    display_name: str = ""
    chat: str = ""
    message_id: int | None = None
    date: datetime | None = None
    mentioned: bool = False


@dataclass
class Outgoing:
    reply: str | None
    employee: str = ""
    action: str = ""
    delete: bool = False

    def to_dict(self) -> dict:
        name = ROLE_BY_ID[self.employee].name if self.employee in ROLE_BY_ID else ""
        return {"reply": self.reply, "employee": self.employee, "employee_name": name, "action": self.action, "delete": self.delete}


class FrontDesk:
    def __init__(
        self,
        finance: FinanceOpsEmployee,
        support: CommunitySupport | None = None,
        monitor: ChainMonitor | None = None,
        translator: Translator | None = None,
        team: Team | None = None,
        payouts: CampaignPayouts | None = None,
        sybil: SybilChecker | None = None,
    ) -> None:
        self.finance = finance
        self.store = finance.store
        self.team = team or Team(self.store)
        self.support = support or CommunitySupport(self.store)
        self.monitor = monitor or ChainMonitor(self.store)
        self.translator = translator or Translator(self.store)
        self.payouts = payouts or CampaignPayouts(self.store, lambda: finance.policy)
        self.sybil = sybil or SybilChecker(self.store, risk=lambda: finance.policy.risk_addresses)

    def on(self, role: str) -> bool:
        return self.team.is_on(role)

    def _who(self, m: Incoming) -> str:
        return f"@{m.username}" if m.username else str(m.user_id or "unknown")

    # ---- ordinary messages ----

    def on_message(self, m: Incoming, simulate: bool = False) -> Outgoing:
        text = m.text or ""
        if self.on(SUPPORT) and self.support.scan(text, m.username or "", m.display_name):
            r = self.support.handle(text, m.username or "", m.display_name, m.mentioned, m.chat, simulate)
            return Outgoing(r.text, SUPPORT, r.action, r.delete)
        if self.on(FINANCE) and PAYMENT_PREFIX.match(text):
            if simulate:
                return Outgoing(self.finance.simulate(text, self._who(m))["reply"], FINANCE, "payment")
            # chat id + message id: Telegram may redeliver an update, and a request id
            # that is already in the ledger is never executed again.
            rid = f"tg-{m.chat}-{m.message_id}"
            reply = self.finance.handle_request(text, self._who(m), rid, m.date or datetime.now(timezone.utc))
            return Outgoing(reply.text, FINANCE, "payment")
        if self.on(SUPPORT):
            r = self.support.handle(text, m.username or "", m.display_name, m.mentioned, m.chat, simulate)
            if r.text:
                return Outgoing(r.text, SUPPORT, r.action, r.delete)
        if m.mentioned:
            return Outgoing("这句我没看懂。发 /help 看看我们团队能帮你做什么。", "", "unknown")
        return Outgoing(None)

    def welcome(self, name: str) -> str | None:
        return self.support.welcome_text(name) if self.on(SUPPORT) else None

    # ---- commands ----

    def command(self, cmd: str, args: str, username: str | None = None, user_id: int | None = None,
                chat: str = "", simulate: bool = False) -> Outgoing | None:
        cmd = cmd.lower().lstrip("/").split("@")[0]
        args = (args or "").strip()
        if cmd in ("start", "help"):
            return Outgoing(self.team.help_text(), "", "help")
        if cmd == "team":
            return Outgoing(self.team.roster_text(), "", "team")
        role = COMMAND_ROLES.get(cmd)
        if role is None:
            return None  # not ours: leave other bots' commands alone
        if not self.on(role):
            return Outgoing(f"{ROLE_BY_ID[role].name}现在没上岗，老板可以在控制台的「团队」里打开。", role, "off")
        if simulate and cmd in MUTATING:
            return Outgoing(f"（模拟器不会真的执行 /{cmd}，在群里发才生效）", role, "simulated")
        ids = (username, user_id)
        f, s, mon = self.finance, self.support, self.monitor
        owner = f.is_owner(*ids)
        handlers = {
            "rules": f.rules_text,
            "today": f.today_text,
            "pending": f.pending_text,
            "report": f.weekly_report_text,
            "pause": lambda: f.pause(*ids),
            "resume": lambda: f.resume(*ids),
            "approve": lambda: f.approve(args, *ids) if args else "用法：/approve 编号，例如 /approve tg-123-45。发 /pending 查看编号。",
            "reject": lambda: f.reject(args, *ids) if args else "用法：/reject 编号。发 /pending 查看编号。",
            "faq": s.faq_text,
            "tx": lambda: s.tx_text(args.split()[0]) if args else "用法：/tx 交易哈希",
            "human": lambda: s.human(args, username or "", chat),
            "tickets": lambda: s.tickets_text() if owner or s.is_admin(username) else "只有老板和管理员可以看工单。",
            "watch": mon.watch_text,
            "alerts": mon.alerts_text,
            "brief": mon.brief_text,
            "translate": lambda: self._translate(args, owner, username),
            "post": lambda: self._post(args, owner, username, chat),
            "discard": lambda: self._discard(args, owner, username),
            "payouts": lambda: self.payouts.payouts_text() if owner else "只有老板可以看结算批次。",
            "sybil": lambda: self.sybil.sybil_text() if owner else "只有老板可以看反女巫检查。",
        }
        return Outgoing(handlers[cmd](), role, cmd)

    def _translate(self, args: str, owner: bool, username: str | None) -> str:
        if not owner:
            return "只有老板可以发起翻译。"
        if not args:
            return "用法：/translate 后面跟公告原文（可以换行）。"
        try:
            return self.translator.preview_text(self.translator.draft(args, f"@{username}" if username else "owner"))
        except TranslationError as e:
            return f"翻译失败：{e}"

    def _draft_id(self, args: str) -> int | None:
        a = args.lstrip("#").strip()
        return int(a) if a.isdigit() else None

    def _post(self, args: str, owner: bool, username: str | None, chat: str) -> str:
        if not owner:
            return "只有老板可以确认发出。"
        tid = self._draft_id(args)
        if tid is None:
            return "用法：/post 草稿编号"
        main = self.store.get_setting("telegram_chat") or chat or None
        try:
            n = len(self.translator.post(tid, f"@{username}" if username else "owner", main))
        except TranslationError as e:
            return str(e)
        return f"✅ 翻译 #{tid} 已确认，{n} 条消息几秒内发出。"

    def _discard(self, args: str, owner: bool, username: str | None) -> str:
        if not owner:
            return "只有老板可以作废草稿。"
        tid = self._draft_id(args)
        if tid is None:
            return "用法：/discard 草稿编号"
        try:
            self.translator.discard(tid, f"@{username}" if username else "owner")
        except TranslationError as e:
            return str(e)
        return f"🗑 翻译草稿 #{tid} 已作废。"

    # ---- the console's chat simulator ----

    def simulate(self, text: str, username: str = "member", mentioned: bool = False, display_name: str = "") -> dict:
        text = text.strip()
        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            out = self.command(cmd, args, username, None, "", simulate=True) or Outgoing(None, "", "ignored")
        else:
            out = self.on_message(Incoming(text, username, None, display_name, mentioned=mentioned), simulate=True)
        d = out.to_dict()
        m = self.team.get(out.employee)
        if m:
            d["employee_name"] = m.name  # the name the owner gave this employee
        return d

    # ---- what the bot posts on its own ----

    def scheduled(self, now: datetime) -> list[tuple[str, str]]:
        out = due_messages(self.finance, now) if self.on(FINANCE) else []
        if self.on(MONITOR):
            brief = self.monitor.due_brief(now)
            if brief:
                out.append(("monitor_brief", brief))
        return out
