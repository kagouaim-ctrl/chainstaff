"""Owner console API for the whole AI team: the team roster, the finance ops employee
(overview, approvals, ledger, payees, rules, treasury, reports), community support,
the chain monitor, the announcement translator, Telegram and the audit log.

Run:
    export CHAINSTAFF_CONSOLE_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
    uvicorn backend.app:app --port 8000
Then open http://localhost:8000 and paste the token.

The console and the bot share the SQLite ledger: settings changed here (pause, Telegram
notifications, outgoing messages) are picked up by the bot without a restart.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response

from agent.config import ChainConfig
from agent.front_desk import FrontDesk
from agent.skills.chain_monitor.monitor import KIND_NAMES, MonitorSettings
from agent.skills.community_support.support import SupportSettings
from agent.skills.translator.translator import LANGS, TONES, TranslationError, TranslatorSettings
from agent.team import ROLE_BY_ID, ROLES, Team
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.skills.finance_ops.insights import TelegramSettings, overview, payee_stats, rules_diff
from agent.skills.finance_ops.schedule import HELP

WEB = Path(__file__).resolve().parent.parent / "web" / "console.html"
OWNER_ID = "console"
CSV_FIELDS = ["created_at", "request_id", "requester", "payee_name", "payee", "amount", "token", "category", "memo",
              "outcome", "status", "messages", "tx_hash", "safe_tx_hash", "error", "decided_by", "updated_at"]


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"日期格式不对：{v}") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def create_app(employee: FinanceOpsEmployee, token: str, chain: ChainConfig | None = None, env: dict | None = None,
               desk: FrontDesk | None = None) -> FastAPI:
    if not token or len(token) < 16:
        raise RuntimeError("CHAINSTAFF_CONSOLE_TOKEN must be set (at least 16 characters)")
    env = dict(os.environ if env is None else env)
    store = employee.store
    desk = desk or FrontDesk(employee)
    team: Team = desk.team
    started = datetime.now(timezone.utc)
    app = FastAPI(title="ChainStaff console", docs_url=None, redoc_url=None)

    def auth(authorization: str = Header(default="")) -> None:
        given = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "令牌不对")

    # The console acts as the owner, so it bypasses the chat-handle owner list.
    def as_owner(fn, *args):
        owners = employee.owners
        return fn(*args, next(iter(owners))) if owners else fn(*args, OWNER_ID)

    def names() -> dict[str, str]:
        return {a: p.name for a, p in employee.policy.whitelist.items()}

    def row(r, n=None) -> dict:
        d = r.to_dict()
        d["payee_name"] = (n or names()).get(d["payee"])
        return d

    def explorer(kind: str, value: str | None) -> str | None:
        if not (chain and chain.explorer_tx_url and value):
            return None
        return chain.explorer_tx_url.replace("/tx/", f"/{kind}/") + value

    def chat() -> str | None:
        return store.get_setting("telegram_chat") or env.get("CHAINSTAFF_REPORT_CHAT") or None

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB)

    # ---- status and overview ----

    @app.get("/api/status", dependencies=[Depends(auth)])
    def status() -> dict:
        return {"paused": store.paused, "live": employee.executor.live, "pending": len(store.pending())}

    @app.get("/api/overview", dependencies=[Depends(auth)])
    def get_overview(days: int = 14) -> dict:
        p = employee.policy
        out = overview(store, p.timezone, dict(p.daily_limit), datetime.now(timezone.utc), min(max(days, 7), 90))
        n = names()
        for t in out["top_payees"]:
            t["payee_name"] = n.get(t["payee"])
        out["status"] = {"paused": store.paused, "live": employee.executor.live, "read_only": p.read_only}
        out["recent"] = store.events(("audit", "alert"), limit=8)
        return out

    # ---- ledger ----

    def _search(status, token, payee, q, start, end, limit):
        return store.search(status, token, payee, q, _when(start), _when(end), min(max(limit, 1), 5000))

    @app.get("/api/payments", dependencies=[Depends(auth)])
    def payments(limit: int = 100, status: str | None = None, token: str | None = None, payee: str | None = None,
                 q: str | None = None, start: str | None = None, end: str | None = None) -> list[dict]:
        n = names()
        return [row(r, n) for r in _search(status, token, payee, q, start, end, min(limit, 500))]

    @app.get("/api/payments.csv", dependencies=[Depends(auth)])
    def payments_csv(status: str | None = None, token: str | None = None, payee: str | None = None,
                     q: str | None = None, start: str | None = None, end: str | None = None) -> Response:
        n = names()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in _search(status, token, payee, q, start, end, 5000):
            d = row(r, n)
            d["messages"] = "；".join(d["messages"])
            w.writerow(d)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="ledger-{stamp}.csv"'})

    @app.get("/api/payments/{request_id}", dependencies=[Depends(auth)])
    def payment(request_id: str) -> dict:
        r = store.get(request_id)
        if r is None:
            raise HTTPException(404, f"找不到 {request_id}")
        d = row(r)
        d["tx_url"] = explorer("tx", r.tx_hash) if employee.executor.live else None
        d["payee_url"] = explorer("address", r.request.payee)
        d["history"] = [e for e in store.events(("audit",), limit=500) if (e["data"] or {}).get("request_id") == request_id]
        return d

    @app.post("/api/payments/{request_id}/approve", dependencies=[Depends(auth)])
    def approve(request_id: str) -> dict:
        return {"message": as_owner(employee.approve, request_id)}

    @app.post("/api/payments/{request_id}/reject", dependencies=[Depends(auth)])
    def reject(request_id: str) -> dict:
        return {"message": as_owner(employee.reject, request_id)}

    # ---- payees ----

    @app.get("/api/payees", dependencies=[Depends(auth)])
    def payees() -> dict:
        p = employee.policy
        stats = {s["payee"]: s for s in payee_stats(store.search(limit=5000))}
        listed = []
        for addr, payee in p.whitelist.items():
            s = stats.pop(addr, None) or {"requests": 0, "blocked": 0, "moved": {}, "last_at": None}
            listed.append({"name": payee.name, "address": payee.address, "aliases": list(payee.aliases),
                           "needs_approval": payee.needs_approval, "risk": addr in p.risk_addresses,
                           "url": explorer("address", payee.address), **{k: s[k] for k in ("requests", "blocked", "moved", "last_at")}})
        unknown = [dict(s, risk=s["payee"] in p.risk_addresses, url=explorer("address", s["payee"])) for s in stats.values()]
        return {"whitelist": listed, "unknown": unknown, "risk_addresses": sorted(p.risk_addresses)}

    # ---- rules ----

    @app.get("/api/rules", dependencies=[Depends(auth)])
    def get_rules() -> dict:
        return employee.policy_file.raw()

    @app.put("/api/rules", dependencies=[Depends(auth)])
    def put_rules(cfg: dict) -> dict:
        before = employee.policy_file.raw()
        try:
            employee.policy_file.save(cfg)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        changes = rules_diff(before, cfg)
        if changes:
            store.log_event("audit", "修改规则：" + "；".join(changes), OWNER_ID, {"changes": changes}, employee="finance_ops")
        return {"ok": True, "changes": changes, "rules_text": employee.rules_text()}

    @app.post("/api/simulate", dependencies=[Depends(auth)])
    def simulate(body: dict = Body(...)) -> dict:
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "请输入一句付款请求，例如：付 Alice 300 USDT 设计费")
        return employee.simulate(text[:500])

    # ---- kill switch ----

    @app.post("/api/pause", dependencies=[Depends(auth)])
    def pause() -> dict:
        return {"message": as_owner(employee.pause)}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    def resume() -> dict:
        return {"message": as_owner(employee.resume)}

    # ---- reports ----

    @app.get("/api/report/weekly", dependencies=[Depends(auth)])
    def report(weeks_ago: int = 0) -> dict:
        now = datetime.now(timezone.utc) - timedelta(weeks=min(max(weeks_ago, 0), 52))
        return {"text": employee.weekly_report_text(now)}

    @app.get("/api/report/today", dependencies=[Depends(auth)])
    def report_today() -> dict:
        return {"text": employee.today_text()}

    @app.get("/api/preview/{cmd}", dependencies=[Depends(auth)])
    def preview(cmd: str) -> dict:
        """What the bot would answer to a command, for the console's chat simulator."""
        texts = {"help": lambda: HELP, "rules": employee.rules_text, "today": employee.today_text,
                 "pending": employee.pending_text, "report": employee.weekly_report_text}
        if cmd not in texts:
            raise HTTPException(404, f"没有 /{cmd} 这个命令")
        return {"text": texts[cmd]()}

    # ---- events ----

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(kind: str | None = None, limit: int = 100, before: int | None = None, employee: str | None = None) -> list[dict]:
        kinds = tuple(k for k in (kind or "").split(",") if k)
        return store.events(kinds, min(max(limit, 1), 500), before, employee)

    # ---- employee, treasury ----

    @app.get("/api/employee", dependencies=[Depends(auth)])
    def get_employee() -> dict:
        p = employee.policy
        sender = getattr(employee.executor, "sender", None)
        wallet = getattr(getattr(sender, "wallet", None), "address", None) or env.get("CHAINSTAFF_WALLET_ADDRESS")
        spent = {b["token"]: b for b in overview(store, p.timezone, dict(p.daily_limit), datetime.now(timezone.utc), 7)["today"]["budget"]}
        return {
            "role": "AI 财务运营员",
            "agent_id": env.get("CHAINSTAFF_AGENT_ID"),
            "wallet": wallet,
            "wallet_url": explorer("address", wallet),
            "wallet_kind": env.get("CHAINSTAFF_WALLET") if employee.executor.live else "test",
            "live": employee.executor.live,
            "network": chain.network if chain else None,
            "chain_id": chain.chain_id if chain else None,
            "safe_address": chain.safe_address if chain else None,
            "safe_url": explorer("address", chain.safe_address) if chain else None,
            "tokens": sorted(chain.tokens) if chain else sorted(p.single_limit),
            "paused": store.paused,
            "read_only": p.read_only,
            "limits": [{"token": t, "single": str(p.single_limit.get(t, 0)), "daily": str(p.daily_limit.get(t, 0)),
                        "spent_today": spent.get(t, {}).get("spent", "0")} for t in sorted(set(p.single_limit) | set(p.daily_limit))],
            "console_started_at": started.isoformat(),
        }

    @app.get("/api/treasury", dependencies=[Depends(auth)])
    def treasury() -> dict:
        balance, error = None, None
        if employee.treasury_balance:
            try:
                balance = {k: str(v) for k, v in employee.treasury_balance().items()}
            except Exception as e:  # noqa: BLE001 - show the error instead of failing the page
                error = str(e) or type(e).__name__
        addrs = list(chain.treasury_addresses) if chain else []
        return {
            "addresses": [{"address": a, "url": explorer("address", a)} for a in addrs],
            "balance": balance,
            "error": error,
            "thresholds": {k: str(v) for k, v in (chain.alert_outflow_threshold if chain else {}).items()},
            "monitoring": bool(addrs and chain and chain.explorer_api_url and env.get("ETHERSCAN_API_KEY")),
            "alerts_enabled": TelegramSettings.load(store).treasury_alerts,
            "alerts": store.events(("alert",), limit=100),
        }

    # ---- Telegram ----

    @app.get("/api/telegram", dependencies=[Depends(auth)])
    def telegram() -> dict:
        bot = store.get_setting("telegram_bot")
        beat = store.get_setting("telegram_heartbeat")
        online = bool(beat and datetime.now(timezone.utc) - datetime.fromisoformat(beat) < timedelta(minutes=3))
        return {
            "bot": json.loads(bot) if bot else None,
            "heartbeat": beat,
            "online": online,
            "chat": chat(),
            "chat_source": "console" if store.get_setting("telegram_chat") else ("env" if env.get("CHAINSTAFF_REPORT_CHAT") else None),
            "owners": sorted(employee.owners),
            "settings": TelegramSettings.load(store).__dict__,
            "commands": team.commands(),
            "outbox": store.outbox(unsent_only=False, limit=20),
            "messages": store.events(("telegram_in", "telegram_out"), limit=60),
        }

    @app.put("/api/telegram/settings", dependencies=[Depends(auth)])
    def put_telegram_settings(body: dict = Body(...)) -> dict:
        try:
            s = TelegramSettings.validate(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.save(store)
        store.log_event("audit", "修改 Telegram 通知设置", OWNER_ID, {"settings": s.__dict__})
        return {"ok": True, "settings": s.__dict__}

    @app.put("/api/telegram/chat", dependencies=[Depends(auth)])
    def put_telegram_chat(body: dict = Body(...)) -> dict:
        value = str(body.get("chat", "")).strip()
        if value and not value.lstrip("-").isdigit():
            raise HTTPException(400, "群 ID 是一串数字，群组一般以 -100 开头")
        store.set_setting("telegram_chat", value)
        store.log_event("audit", f"通知群改为 {value or '（使用部署时的默认值）'}", OWNER_ID)
        return {"ok": True, "chat": chat()}

    @app.post("/api/telegram/send", dependencies=[Depends(auth)])
    def telegram_send(body: dict = Body(...)) -> dict:
        target = chat()
        if not target:
            raise HTTPException(400, "还没设置通知群，先在上面填群 ID")
        kind = body.get("kind", "custom")
        texts = {"report": employee.weekly_report_text, "today": employee.today_text,
                 "pending": employee.pending_text, "rules": employee.rules_text}
        if kind in texts:
            text = texts[kind]()
        elif kind == "custom":
            text = str(body.get("text", "")).strip()
            if not text:
                raise HTTPException(400, "消息不能为空")
        else:
            raise HTTPException(400, f"不支持的类型：{kind}")
        oid = store.enqueue(target, text)
        store.log_event("audit", f"从控制台发送到群（{kind}）", OWNER_ID, {"outbox_id": oid})
        return {"ok": True, "id": oid, "message": "已排队，机器人几秒内发出"}

    # ---- the team ----

    def member_view(m, counts, paid) -> dict:
        spec = ROLE_BY_ID[m.role]
        c = counts.get(m.role, {})
        view = {"role": m.role, "name": m.name, "enabled": m.enabled, "hired_at": m.hired_at, "title": spec.name,
                "summary": spec.summary, "touches_money": spec.touches_money, "activity_7d": c}
        if m.role == "finance_ops":
            view["headline"] = {"label": "7 天付款请求", "value": paid}
            view["setup"] = [] if employee.owners else ["规则里还没写老板的 Telegram 用户名"]
        elif m.role == "community_support":
            st = desk.support.stats()
            ss = desk.support.settings
            view["headline"] = {"label": "7 天回答 / 拦截骗局", "value": f"{st['counts']['faq'] + st['counts']['tx']} / {st['counts']['scam']}"}
            view["setup"] = [x for x, ok in (("还没写 FAQ", ss.faq), ("还没填管理员", ss.admins), ("还没填官方域名", ss.official_domains)) if not ok]
        elif m.role == "chain_monitor":
            st = desk.monitor.stats()
            view["headline"] = {"label": "7 天预警（高危）", "value": f"{st['total']}（{st['high']}）"}
            view["setup"] = [x for x, ok in (("还没设要盯的地址", desk.monitor.watch()), ("没有 Etherscan API key，读不到链上转账", desk.monitor.source)) if not ok]
        else:
            drafts = [d for d in store.translations(200) if d["status"] == "draft"]
            view["headline"] = {"label": "待确认草稿", "value": len(drafts)}
            view["setup"] = [] if desk.translator.configured else ["没有 ANTHROPIC_API_KEY，还不能翻译"]
        return view

    @app.get("/api/team", dependencies=[Depends(auth)])
    def get_team() -> dict:
        now = datetime.now(timezone.utc)
        counts = store.event_counts(now - timedelta(days=7))
        paid = len(store.between(now - timedelta(days=7), now + timedelta(minutes=1)))
        members = team.members()
        hired = {m.role for m in members}
        return {
            "members": [member_view(m, counts, paid) for m in members],
            "roles": [{"id": r.id, "name": r.name, "summary": r.summary, "duties": list(r.duties), "status": r.status,
                       "touches_money": r.touches_money, "needs": list(r.needs), "hired": r.id in hired,
                       "commands": [{"cmd": c, "desc": d, "owner_only": o} for c, d, o in r.commands]} for r in ROLES],
            "recent": store.events(("audit", "alert", "support", "translate"), limit=12),
        }

    @app.post("/api/team/hire", dependencies=[Depends(auth)])
    def hire(body: dict = Body(...)) -> dict:
        try:
            m = team.hire(str(body.get("role", "")), body.get("name"), OWNER_ID)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "member": m.__dict__}

    @app.put("/api/team/{role}", dependencies=[Depends(auth)])
    def update_member(role: str, body: dict = Body(...)) -> dict:
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise HTTPException(400, "enabled 必须是开或关")
        try:
            m = team.update(role, enabled, body.get("name"), OWNER_ID)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "member": m.__dict__}

    @app.delete("/api/team/{role}", dependencies=[Depends(auth)])
    def dismiss(role: str) -> dict:
        try:
            team.dismiss(role, OWNER_ID)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/chat/simulate", dependencies=[Depends(auth)])
    def chat_simulate(body: dict = Body(...)) -> dict:
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "请输入一句话")
        return desk.simulate(text[:2000], str(body.get("username") or "member").lstrip("@")[:32],
                             bool(body.get("mentioned", False)), str(body.get("display_name", ""))[:64])

    # ---- community support ----

    @app.get("/api/support", dependencies=[Depends(auth)])
    def support() -> dict:
        return {
            "enabled": team.is_on("community_support"),
            "hired": team.get("community_support") is not None,
            "settings": SupportSettings.load(store).__dict__,
            "stats": desk.support.stats(),
            "tickets": store.tickets(limit=100),
            "events": store.events(("support",), limit=100, employee="community_support"),
            "tx_lookup": desk.support.tx_lookup is not None,
        }

    @app.put("/api/support/settings", dependencies=[Depends(auth)])
    def put_support(body: dict = Body(...)) -> dict:
        try:
            s = SupportSettings.validate(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.save(store)
        store.log_event("audit", f"修改社区客服设置（FAQ {len(s.faq)} 条，管理员 {len(s.admins)} 个）", OWNER_ID, employee="community_support")
        return {"ok": True, "settings": s.__dict__}

    @app.post("/api/support/simulate", dependencies=[Depends(auth)])
    def support_simulate(body: dict = Body(...)) -> dict:
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "请输入一句群消息")
        r = desk.support.handle(text[:2000], str(body.get("username", "")).lstrip("@")[:32], str(body.get("display_name", ""))[:64],
                                bool(body.get("mentioned", True)), simulate=True)
        return r.to_dict()

    @app.post("/api/support/tickets/{ticket_id}/close", dependencies=[Depends(auth)])
    def close_ticket(ticket_id: int, body: dict = Body(default={})) -> dict:
        if not store.close_ticket(ticket_id, OWNER_ID, str(body.get("note", ""))):
            raise HTTPException(400, f"工单 #{ticket_id} 不存在或已关闭")
        store.log_event("audit", f"关闭工单 #{ticket_id}", OWNER_ID, {"ticket": ticket_id}, employee="community_support")
        return {"ok": True}

    # ---- chain monitor ----

    @app.get("/api/monitor", dependencies=[Depends(auth)])
    def monitor() -> dict:
        mon = desk.monitor
        return {
            "enabled": team.is_on("chain_monitor"),
            "hired": team.get("chain_monitor") is not None,
            "settings": MonitorSettings.load(store).__dict__,
            "watch": [dict(w, url=explorer("address", w["address"])) for w in mon.watch()],
            "watch_source": "console" if MonitorSettings.load(store).watch else ("chain" if mon.watch() else None),
            "thresholds": {k: str(v) for k, v in mon.thresholds().items()},
            "source": mon.source is not None,
            "balances": mon.balances is not None,
            "last_run": mon.last_run(),
            "stats": mon.stats(),
            "kind_names": KIND_NAMES,
            "alerts": mon.alerts(200),
        }

    @app.put("/api/monitor/settings", dependencies=[Depends(auth)])
    def put_monitor(body: dict = Body(...)) -> dict:
        try:
            s = MonitorSettings.validate(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.save(store)
        store.log_event("audit", f"修改链上监控设置（盯 {len(s.watch) or len(desk.monitor.watch())} 个地址）", OWNER_ID, employee="chain_monitor")
        return {"ok": True, "settings": s.__dict__}

    @app.post("/api/monitor/check", dependencies=[Depends(auth)])
    def monitor_check() -> dict:
        if not team.is_on("chain_monitor"):
            raise HTTPException(400, "链上监控员没上岗")
        new = desk.monitor.run_once()
        return {"new": new, "last_run": desk.monitor.last_run()}

    @app.get("/api/monitor/brief", dependencies=[Depends(auth)])
    def monitor_brief() -> dict:
        return {"text": desk.monitor.brief_text()}

    # ---- announcement translator ----

    @app.get("/api/translator", dependencies=[Depends(auth)])
    def translator() -> dict:
        return {
            "enabled": team.is_on("announcement_translator"),
            "hired": team.get("announcement_translator") is not None,
            "configured": desk.translator.configured,
            "model": desk.translator.model,
            "settings": TranslatorSettings.load(store).__dict__,
            "languages": LANGS,
            "tones": list(TONES),
            "drafts": store.translations(50),
            "chat": chat(),
        }

    @app.put("/api/translator/settings", dependencies=[Depends(auth)])
    def put_translator(body: dict = Body(...)) -> dict:
        try:
            s = TranslatorSettings.validate(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.save(store)
        store.log_event("audit", "修改公告翻译设置", OWNER_ID, employee="announcement_translator")
        return {"ok": True, "settings": s.__dict__}

    def _translator_call(fn, *args):
        if not team.is_on("announcement_translator"):
            raise HTTPException(400, "公告翻译员没上岗")
        try:
            return fn(*args)
        except TranslationError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/translator/drafts", dependencies=[Depends(auth)])
    def new_draft(body: dict = Body(...)) -> dict:
        langs = body.get("languages") or None
        if langs is not None and (not isinstance(langs, list) or any(x not in LANGS for x in langs)):
            raise HTTPException(400, "目标语言不对")
        return _translator_call(desk.translator.draft, str(body.get("text", "")), OWNER_ID, langs)

    @app.put("/api/translator/drafts/{tid}", dependencies=[Depends(auth)])
    def edit_draft(tid: int, body: dict = Body(...)) -> dict:
        outputs = body.get("outputs")
        if not isinstance(outputs, dict):
            raise HTTPException(400, "outputs 必须是 语言: 译文")
        return _translator_call(desk.translator.edit, tid, outputs, OWNER_ID)

    @app.post("/api/translator/drafts/{tid}/post", dependencies=[Depends(auth)])
    def post_draft(tid: int) -> dict:
        ids = _translator_call(desk.translator.post, tid, OWNER_ID, chat())
        return {"ok": True, "outbox": ids, "message": f"已排队 {len(ids)} 条，机器人几秒内发出"}

    @app.post("/api/translator/drafts/{tid}/discard", dependencies=[Depends(auth)])
    def discard_draft(tid: int) -> dict:
        _translator_call(desk.translator.discard, tid, OWNER_ID)
        return {"ok": True}

    return app


def _default_app() -> FastAPI:  # pragma: no cover - process entry point
    from agent.runtime import build_front_desk

    desk, chain = build_front_desk()
    return create_app(desk.finance, os.environ.get("CHAINSTAFF_CONSOLE_TOKEN", ""), chain, desk=desk)


def __getattr__(name: str):  # pragma: no cover - lets `uvicorn backend.app:app` build lazily
    if name == "app":
        return _default_app()
    raise AttributeError(name)
