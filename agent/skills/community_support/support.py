"""The AI community support employee.

Answers only from the FAQ the project wrote (word for word, nothing made up), looks up
transactions, warns about scams, and hands anything else to a human admin as a ticket.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from agent.store import Store

from .scam import HIGH, Finding, detect, severity, warning_text

ROLE = "community_support"
TX_RE = re.compile(r"0x[0-9a-fA-F]{64}")
QUESTION = re.compile(r"[?？]|吗|么|怎么|如何|什么|哪|几时|多少|何时|能不能|可以.{0,6}吗|\b(?:how|what|when|where|why|which|wen|is there|can i|does|do i)\b", re.I)
HUMAN = re.compile(r"人工|真人|找管理员|联系管理员|投诉|\b(?:human|real person|talk to (?:an? )?admin)\b", re.I)
DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")
HANDLE_RE = re.compile(r"^[a-z0-9_]{3,32}$")
ANSWER_MODES = ("mention", "questions")
SCAM_ACTIONS = ("warn", "delete", "off")

TxLookup = Callable[[str], dict]


@dataclass
class SupportSettings:
    project_name: str = ""
    admins: list[str] = field(default_factory=list)
    official_domains: list[str] = field(default_factory=list)
    faq: list[dict] = field(default_factory=list)  # [{"q": ..., "keywords": [...], "a": ...}]
    answer_mode: str = "mention"  # mention: only when @-ed or replied to; questions: also unprompted questions that match the FAQ
    scam_action: str = "warn"  # warn | delete (needs the bot to be a group admin) | off
    escalate_unknown: bool = True
    welcome: str = ""

    KEY = "support_settings"

    @classmethod
    def load(cls, store: Store) -> "SupportSettings":
        raw = store.get_setting(cls.KEY)
        if not raw:
            return cls()
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def validate(cls, data: dict) -> "SupportSettings":
        s = cls()
        if "project_name" in data:
            s.project_name = str(data["project_name"]).strip()[:60]
        if "admins" in data:
            s.admins = []
            for a in _list(data["admins"], "admins"):
                h = str(a).strip().lstrip("@").lower()
                if h and not HANDLE_RE.match(h):
                    raise ValueError(f"管理员用户名格式不对：{a}")
                if h and h not in s.admins:
                    s.admins.append(h)
        if "official_domains" in data:
            s.official_domains = []
            for d in _list(data["official_domains"], "official_domains"):
                host = re.sub(r"^https?://", "", str(d).strip().lower()).split("/")[0].removeprefix("www.")
                if host and not DOMAIN_RE.match(host):
                    raise ValueError(f"官方域名格式不对：{d}")
                if host and host not in s.official_domains:
                    s.official_domains.append(host)
        if "faq" in data:
            items = _list(data["faq"], "faq")
            if len(items) > 200:
                raise ValueError("FAQ 最多 200 条")
            s.faq = []
            for i, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    raise ValueError(f"第 {i} 条 FAQ 格式不对")
                q, a = str(item.get("q", "")).strip(), str(item.get("a", "")).strip()
                if not q or not a:
                    raise ValueError(f"第 {i} 条 FAQ 的问题和回答都要填")
                kws = [str(k).strip() for k in _list(item.get("keywords", []), "keywords") if str(k).strip()]
                s.faq.append({"q": q[:200], "keywords": kws[:20], "a": a[:2000]})
        if "answer_mode" in data:
            if data["answer_mode"] not in ANSWER_MODES:
                raise ValueError("answer_mode 只能是 mention 或 questions")
            s.answer_mode = data["answer_mode"]
        if "scam_action" in data:
            if data["scam_action"] not in SCAM_ACTIONS:
                raise ValueError("scam_action 只能是 warn、delete 或 off")
            s.scam_action = data["scam_action"]
        if "escalate_unknown" in data:
            if not isinstance(data["escalate_unknown"], bool):
                raise ValueError("escalate_unknown 必须是开或关")
            s.escalate_unknown = data["escalate_unknown"]
        if "welcome" in data:
            s.welcome = str(data["welcome"]).strip()[:1000]
        return s

    def save(self, store: Store) -> None:
        store.set_setting(self.KEY, json.dumps(asdict(self), ensure_ascii=False))


def _list(v, name: str) -> list:
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是列表")
    return v


def match_faq(text: str, faq: list[dict]) -> tuple[int, dict] | None:
    """Best FAQ entry for a message: keyword hits, plus a bonus when the question itself appears."""
    t = text.lower()
    best: tuple[int, int, dict] | None = None
    for i, item in enumerate(faq):
        score = sum(1 for k in item.get("keywords", ()) if k.lower() in t)
        if item["q"].lower().rstrip("?？ ") in t:
            score += 2
        if score and (best is None or score > best[0]):
            best = (score, i, item)
    return (best[1], best[2]) if best else None


def _short(h: str) -> str:
    return f"{h[:10]}…{h[-6:]}"


@dataclass
class SupportReply:
    text: str | None
    action: str  # none | scam | faq | tx | ticket | unknown
    delete: bool = False
    findings: list[Finding] = field(default_factory=list)
    faq_index: int | None = None
    ticket_id: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = [asdict(f) for f in self.findings]
        return d


class CommunitySupport:
    def __init__(self, store: Store, tx_lookup: TxLookup | None = None, explorer_tx_url: str | None = None) -> None:
        self.store = store
        self.tx_lookup = tx_lookup
        self.explorer_tx_url = explorer_tx_url

    @property
    def settings(self) -> SupportSettings:
        return SupportSettings.load(self.store)

    def is_admin(self, username: str | None) -> bool:
        return bool(username) and username.lstrip("@").lower() in self.settings.admins

    # ---- the one entry point the bot calls for ordinary group messages ----

    def scan(self, text: str, username: str = "", display_name: str = "") -> list[Finding]:
        s = self.settings
        if s.scam_action == "off":
            return []
        return detect(text, username, display_name, s.admins, s.official_domains)

    def handle(self, text: str, username: str = "", display_name: str = "", mentioned: bool = False,
               chat: str = "", simulate: bool = False) -> SupportReply:
        s = self.settings
        who = f"@{username.lstrip('@')}" if username else (display_name or "unknown")
        findings = self.scan(text, username, display_name)
        if findings:
            reply = SupportReply(warning_text(findings, s.official_domains), "scam",
                                 delete=s.scam_action == "delete" and severity(findings) == HIGH, findings=findings)
            if not simulate:
                self._log(f"拦截可疑消息（{who}）：" + "；".join(f.reason for f in findings), who,
                          {"type": "scam", "kinds": [f.kind for f in findings], "severity": severity(findings),
                           "deleted": reply.delete, "message": text[:500], "chat": chat})
            return reply

        tx = TX_RE.search(text)
        if tx and (mentioned or QUESTION.search(text) or text.strip() == tx.group(0)):
            reply = SupportReply(self.tx_text(tx.group(0)), "tx")
            if not simulate:
                self._log(f"查询交易 {_short(tx.group(0))}（{who}）", who, {"type": "tx", "hash": tx.group(0), "chat": chat})
            return reply

        if mentioned and HUMAN.search(text):
            return self._ticket(text, who, "用户要求转人工", chat, simulate)

        asked = mentioned or (s.answer_mode == "questions" and QUESTION.search(text) is not None)
        if not asked:
            return SupportReply(None, "none")
        hit = match_faq(text, s.faq)
        if hit:
            i, item = hit
            if not simulate:
                self._log(f"回答「{item['q']}」（{who}）", who, {"type": "faq", "faq": i, "q": item["q"], "chat": chat})
            return SupportReply(item["a"], "faq", faq_index=i)
        if not mentioned:
            return SupportReply(None, "none")  # an unprompted question we can't answer: stay quiet
        if s.escalate_unknown and s.admins:
            return self._ticket(text, who, "FAQ 里没有答案", chat, simulate)
        if not simulate:
            self._log(f"没能回答（{who}）：{text[:80]}", who, {"type": "unknown", "message": text[:500], "chat": chat})
        return SupportReply("这个问题我还回答不了。发 /faq 看看常见问题，或者等管理员来回复。", "unknown")

    def _ticket(self, text: str, who: str, reason: str, chat: str, simulate: bool) -> SupportReply:
        admins = " ".join("@" + a for a in self.settings.admins)
        tid = None
        if not simulate:
            tid = self.store.open_ticket(who, text, reason, chat)
            self._log(f"开工单 #{tid}（{who}）：{reason}", who, {"type": "ticket", "ticket": tid, "chat": chat})
        no = f" #{tid}" if tid else ""
        return SupportReply(f"已经转给管理员{no}，请稍等。{admins}".strip(), "ticket", ticket_id=tid)

    def _log(self, text: str, actor: str, data: dict) -> None:
        self.store.log_event("support", text, actor, data, employee=ROLE)

    # ---- commands ----

    def faq_text(self) -> str:
        s = self.settings
        if not s.faq:
            return "还没有配置常见问题。"
        title = f"{s.project_name} 常见问题" if s.project_name else "常见问题"
        return title + "：\n" + "\n".join(f"{i}. {item['q']}" for i, item in enumerate(s.faq, 1)) + "\n\n直接 @我 问就行。"

    def tx_text(self, tx_hash: str) -> str:
        if self.tx_lookup is None:
            return "暂时查不了链上数据（部署时没有配置 RPC）。"
        try:
            info = self.tx_lookup(tx_hash)
        except Exception:  # noqa: BLE001 - a flaky RPC should not crash the chat
            return f"交易 {_short(tx_hash)} 暂时查不到，节点没有响应，稍后再试。"
        link = f"\n{self.explorer_tx_url}{tx_hash}" if self.explorer_tx_url else ""
        status = info.get("status")
        if status == "not_found":
            return f"交易 {_short(tx_hash)} 在链上找不到。确认哈希是否完整、网络是否选对（这里查的是本项目所在的链）。"
        if status == "pending":
            return f"⏳ 交易 {_short(tx_hash)} 还在等待打包，通常几秒到几分钟。{link}"
        if status == "failed":
            return f"❌ 交易 {_short(tx_hash)} 执行失败（区块 {info.get('block')}），资产没有转出，只扣了手续费。{link}"
        return f"✅ 交易 {_short(tx_hash)} 已成功，区块 {info.get('block')}。{link}"

    def human(self, text: str, username: str = "", chat: str = "") -> str:
        who = f"@{username.lstrip('@')}" if username else "unknown"
        if not self.settings.admins:
            return "还没有设置管理员，暂时没法转人工。"
        return self._ticket(text or "（没有说明问题）", who, "用户发了 /human", chat, False).text or ""

    def tickets_text(self) -> str:
        open_ = self.store.tickets("open", 20)
        if not open_:
            return "没有待处理的工单。"
        return f"待处理工单 {len(open_)} 个：\n" + "\n".join(f"#{t['id']} {t['user']}：{t['text'][:60]}" for t in open_)

    def welcome_text(self, name: str) -> str | None:
        w = self.settings.welcome
        return w.replace("{name}", name) if w else None

    # ---- console ----

    def stats(self, now: datetime | None = None, days: int = 7) -> dict:
        now = now or datetime.now(timezone.utc)
        events = self.store.events(("support",), limit=5000, employee=ROLE, since=now - timedelta(days=days))
        counts = {"faq": 0, "tx": 0, "scam": 0, "ticket": 0, "unknown": 0}
        faq_hits: dict[str, int] = {}
        scam_kinds: dict[str, int] = {}
        for e in events:
            d = e["data"] or {}
            t = d.get("type")
            if t in counts:
                counts[t] += 1
            if t == "faq":
                faq_hits[d.get("q", "")] = faq_hits.get(d.get("q", ""), 0) + 1
            if t == "scam":
                for k in d.get("kinds", []):
                    scam_kinds[k] = scam_kinds.get(k, 0) + 1
        handled = counts["faq"] + counts["tx"]
        asked = handled + counts["ticket"] + counts["unknown"]
        return {
            "days": days,
            "counts": counts,
            "answer_rate": round(handled / asked, 3) if asked else None,
            "top_faq": sorted(({"q": q, "n": n} for q, n in faq_hits.items()), key=lambda x: -x["n"])[:8],
            "scam_kinds": scam_kinds,
            "tickets_open": len(self.store.tickets("open", 500)),
        }
