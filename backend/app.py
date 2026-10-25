"""Owner console API for the whole AI team: the team roster, the finance ops employee
(overview, approvals, ledger, payees, rules, treasury, reports), community support,
the chain monitor, the announcement translator and the audit log. KAGOU has no Telegram: alerts and
reports live in the console only.

Run:
    export CHAINSTAFF_CONSOLE_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
    uvicorn backend.app:app --port 8000
Then open http://localhost:8000 and paste the token.

The server also runs the background duties that used to live in the Telegram bot (agent/background.py).
"""

from __future__ import annotations

import csv
import hmac
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from urllib.parse import urlsplit

from agent.config import ChainConfig
from agent.front_desk import FrontDesk
from agent.skills.airdrop_sybil.sybil import RULE_NAMES, VERDICT_NAMES
from agent.skills.campaign_payouts.payouts import STATUS_NAMES, PayoutError
from agent.skills.chain_monitor.monitor import KIND_NAMES, MonitorSettings
from agent.skills.community_support.support import SupportSettings
from agent.skills.translator.translator import LANGS, TONES, TranslationError, TranslatorSettings
from agent.team import ROLE_BY_ID, ROLES, Team
from agent.workforce import Workforce
from agent.bsc.service import ChainService
from backend.chain_api import mount_chain
from agent.autonomy.service import Autonomy
from backend.autonomy_api import mount_autonomy
from backend.workforce_api import mount_workforce
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


def cors_origins(value: str | None) -> list[str]:
    """KAGOU_CORS_ORIGINS: comma-separated exact origins allowed to call the API from another site, e.g. a console
    hosted apart from this server. Empty means same-origin only, which is all the bundled console needs. No wildcard:
    every origin that may hold a console token is named."""
    out = []
    for o in (value or "").split(","):
        o = o.strip().rstrip("/")
        if not o:
            continue
        u = urlsplit(o)
        if o == "*" or u.scheme not in ("https", "http") or not u.netloc or u.path or u.query or u.fragment:
            raise RuntimeError(f"KAGOU_CORS_ORIGINS: {o!r} is not an origin like https://app.kagouai.com")
        if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError(f"KAGOU_CORS_ORIGINS: {o!r} must use https (http only for localhost)")
        out.append(o)
    return out


def create_app(employee: FinanceOpsEmployee, token: str, chain: ChainConfig | None = None, env: dict | None = None,
               desk: FrontDesk | None = None, sybil_background: bool = True, workforce: Workforce | None = None,
               chain_service: ChainService | None = None, viewer_token: str | None = None, autonomy: Autonomy | None = None) -> FastAPI:
    if not token or len(token) < 16:
        raise RuntimeError("CHAINSTAFF_CONSOLE_TOKEN must be set (at least 16 characters)")
    if viewer_token and (len(viewer_token) < 16 or viewer_token == token):
        raise RuntimeError("KAGOU_DEMO_TOKEN must be at least 16 characters and differ from the console token")
    env = dict(os.environ if env is None else env)
    origins = cors_origins(env.get("KAGOU_CORS_ORIGINS"))
    store = employee.store
    desk = desk or FrontDesk(employee)
    team: Team = desk.team
    started = datetime.now(timezone.utc)
    app = FastAPI(title="ChainStaff console", docs_url=None, redoc_url=None)
    if origins:
        # Bearer tokens, no cookies: credentials stay off, so a listed origin can only use a token its user pasted.
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key"], max_age=600)

    def origin_ok(origin: str | None, host: str | None) -> bool:
        """For the WebSocket, which CORS does not cover: no Origin (not a browser), this server's own host, or a listed one."""
        if not origin:
            return True
        return urlsplit(origin).netloc == (host or "") or origin.rstrip("/") in origins

    def is_viewer(given: str) -> bool:
        return bool(viewer_token) and hmac.compare_digest(given.strip().encode(), viewer_token.encode())

    def token_ok(given: str) -> bool:
        return hmac.compare_digest(given.strip().encode(), token.encode()) or is_viewer(given)

    def auth(authorization: str = Header(default="")) -> None:
        if not token_ok(authorization.removeprefix("Bearer ")):
            raise HTTPException(401, "令牌不对")

    # Read-only demo login (KAGOU_DEMO_TOKEN): every page and stream, no change of any kind. Checked once here for the
    # whole app, so a new write endpoint is read-only for demo visitors without anyone remembering to mark it.
    @app.middleware("http")
    async def viewer_read_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and is_viewer(request.headers.get("authorization", "").removeprefix("Bearer ")):
            return JSONResponse({"detail": "演示账号只能查看，不能修改"}, status_code=403)
        return await call_next(request)

    @app.get("/api/session", dependencies=[Depends(auth)])
    def session(authorization: str = Header(default="")) -> dict:
        return {"role": "viewer" if is_viewer(authorization.removeprefix("Bearer ")) else "owner"}

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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB)

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness for the container and the reverse proxy. No auth and nothing private: just that the app answers."""
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> Response:
        """Readiness: the database answers. Unauthenticated, so it says nothing beyond ready or not."""
        try:
            store.ping()
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True})

    @app.get("/api/system", dependencies=[Depends(auth)])
    def system() -> dict:
        """What this server can really do right now, so the console never implies a capability it lacks.
        Live mode only: the static demo answers this from its snapshot with mode=demo."""
        wf = app.state.workforce
        persistent = store.path not in ("", ":memory:")
        return {
            "mode": "live",
            "version": env.get("KAGOU_VERSION") or None,
            "started_at": started.isoformat(),
            "database": {"engine": "sqlite", "persistent": persistent, "file": Path(store.path).name if persistent else None},
            "model": {"configured": bool(wf.runtime.providers), "providers": sorted(wf.runtime.providers),
                      "default_model": wf.planner.model if wf.llm else None,
                      "compat": {"service": wf.compat.label, "model": wf.compat.model, "host": wf.compat.host} if wf.compat else None,
                      "reason": None if wf.runtime.providers else "服务器没有设置 AI_PROVIDER / AI_API_KEY（或 ANTHROPIC_API_KEY），Agent 不能调用模型"},
            "ai_usage": {k: v for k, v in wf.gateway.usage(days=0).items() if k != "days"},
            "chain": {"configured": chain is not None, "network": chain.network if chain else None,
                      "chain_id": chain.chain_id if chain else None, "listener": chain_service is not None},
            "explorer_api": wf.etherscan is not None,
            "anchor": wf.anchor is not None,
            "writes": {"payments": bool(employee.executor.live), "agent_signing": False},
            "auth": {"viewer_token": bool(viewer_token), "cors_origins": origins},
        }

    # The AI workforce terminal: agents, workspaces, tasks, approvals, on-chain records.
    app.state.workforce = workforce or Workforce(store)
    mount_workforce(app, app.state.workforce, auth, OWNER_ID, chain)
    # Autonomous agents: responsibilities, missions, watchers, the task queue the worker runs, activity, approvals.
    app.state.autonomy = autonomy or Autonomy(app.state.workforce, env=env)
    if chain_service is not None and chain_service.autonomy is None:
        chain_service.attach_autonomy(app.state.autonomy)
    mount_autonomy(app, app.state.autonomy, auth, OWNER_ID)
    # BSC realtime data layer: status, monitors, events, and the live WebSocket.
    app.state.chain = chain_service
    mount_chain(app, chain_service, auth, token_ok, origin_ok=origin_ok)

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

    @app.put("/api/treasury/alerts", dependencies=[Depends(auth)])
    def put_treasury_alerts(body: dict = Body(...)) -> dict:
        """The basic treasury alerts (used when no chain monitor is on the team). They are logged, never posted."""
        s = TelegramSettings.load(store)  # legacy name: the settings row predates KAGOU and is kept for existing ledgers
        s.treasury_alerts = bool(body.get("enabled"))
        s.save(store)
        store.log_event("audit", f"{'打开' if s.treasury_alerts else '关闭'}金库提醒", OWNER_ID)
        return {"ok": True, "alerts_enabled": s.treasury_alerts}

    # ---- the team ----

    def member_view(m, counts, paid) -> dict:
        spec = ROLE_BY_ID[m.role]
        c = counts.get(m.role, {})
        view = {"role": m.role, "name": m.name, "enabled": m.enabled, "hired_at": m.hired_at, "title": spec.name,
                "summary": spec.summary, "touches_money": spec.touches_money, "activity_7d": c}
        if m.role == "finance_ops":
            view["headline"] = {"label": "7 天付款请求", "value": paid}
            view["todo"] = {"label": "待签字", "value": len(store.pending()), "href": "#approvals"}
            view["setup"] = [] if employee.owners else ["规则里还没写老板（签字人）"]
        elif m.role == "community_support":
            st = desk.support.stats()
            ss = desk.support.settings
            view["headline"] = {"label": "7 天回答 / 拦截骗局", "value": f"{st['counts']['faq'] + st['counts']['tx']} / {st['counts']['scam']}"}
            view["todo"] = {"label": "待处理工单", "value": st["tickets_open"], "href": "#support"}
            view["setup"] = [x for x, ok in (("还没写 FAQ", ss.faq), ("还没填管理员", ss.admins), ("还没填官方域名", ss.official_domains)) if not ok]
        elif m.role == "chain_monitor":
            st = desk.monitor.stats()
            view["headline"] = {"label": "7 天预警（高危）", "value": f"{st['total']}（{st['high']}）"}
            day = desk.monitor.stats(days=1)
            view["todo"] = {"label": "24 小时高危预警", "value": day["high"], "href": "#monitor"}
            view["setup"] = [x for x, ok in (("还没设要盯的地址", desk.monitor.watch()), ("没有 Etherscan API key，读不到链上转账", desk.monitor.source)) if not ok]
        elif m.role == "campaign_payouts":
            ov = desk.payouts.overview()
            view["headline"] = {"label": "已提案金额", "value": " / ".join(f"{v} {k}" for k, v in ov["paid_totals"].items()) or "0"}
            view["todo"] = {"label": "待确认批次", "value": ov["pending"], "href": "#payouts"}
            view["setup"] = [x for x, ok in (("没有配置 Safe 多签地址", desk.payouts.safe_address), ("没有配置代币合约地址，只能导出名单", ov["exportable"])) if not ok]
        elif m.role == "airdrop_sybil":
            checks = store.sybil_checks(200)
            removed = sum(((c["result"] or {}).get("counts") or {}).get("excluded", 0) for c in checks)
            view["headline"] = {"label": "检查过 / 剔除", "value": f"{sum(len(c['addresses']) for c in checks)} / {removed}"}
            view["todo"] = {"label": "进行中的检查", "value": sum(c["status"] == "running" for c in checks), "href": "#sybil"}
            view["setup"] = [] if desk.sybil.ready else ["没有 Etherscan API key，只能用导入的链上资料"]
        else:
            drafts = [d for d in store.translations(200) if d["status"] == "draft"]
            posted = sum(d["status"] == "posted" for d in store.translations(200))
            view["headline"] = {"label": "已发出公告", "value": posted}
            view["todo"] = {"label": "待确认草稿", "value": len(drafts), "href": "#translator"}
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

    @app.post("/api/translator/drafts/{tid}/discard", dependencies=[Depends(auth)])
    def discard_draft(tid: int) -> dict:
        _translator_call(desk.translator.discard, tid, OWNER_ID)
        return {"ok": True}

    # ---- campaign payouts ----

    def _payouts(fn, *args):
        if not team.is_on("campaign_payouts"):
            raise HTTPException(400, "营销结算员没上岗")
        try:
            return fn(*args)
        except PayoutError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/payouts", dependencies=[Depends(auth)])
    def payouts() -> dict:
        return {**desk.payouts.overview(), "enabled": team.is_on("campaign_payouts"),
                "hired": team.get("campaign_payouts") is not None, "status_names": STATUS_NAMES,
                "sybil_checks": [{"id": c["id"], "name": c["name"], "excluded": c["result"]["counts"]["excluded"]}
                                 for c in store.sybil_checks(30) if c["status"] == "done"]}

    def _batch_args(body: dict) -> tuple:
        sc = body.get("sybil_check")
        return str(body.get("text", ""))[:400_000], body.get("rules") or {}, str(body.get("campaign", ""))[:80], int(sc) if sc else None

    @app.post("/api/payouts/preview", dependencies=[Depends(auth)])
    def payouts_preview(body: dict = Body(...)) -> dict:
        text, rules, campaign, sc = _batch_args(body)
        return _payouts(desk.payouts.preview, text, rules, campaign, sc)

    @app.post("/api/payouts/batches", dependencies=[Depends(auth)])
    def payouts_create(body: dict = Body(...)) -> dict:
        text, rules, campaign, sc = _batch_args(body)
        return _payouts(desk.payouts.create, campaign, text, rules, OWNER_ID, sc)

    @app.get("/api/payouts/batches/{bid}", dependencies=[Depends(auth)])
    def payouts_batch(bid: int) -> dict:
        b = _payouts(desk.payouts.get, bid)
        b["tx_url"] = explorer("tx", b["tx_hash"]) if b["tx_hash"] else None
        return b

    @app.post("/api/payouts/batches/{bid}/{action}", dependencies=[Depends(auth)])
    def payouts_action(bid: int, action: str, body: dict = Body(default={})) -> dict:
        if action == "propose":
            return _payouts(desk.payouts.propose, bid, OWNER_ID)
        if action == "executed":
            return _payouts(desk.payouts.mark_executed, bid, str(body.get("tx_hash", "")), OWNER_ID)
        if action == "cancel":
            return _payouts(desk.payouts.cancel, bid, OWNER_ID, str(body.get("note", "")))
        raise HTTPException(404, "没有这个操作")

    @app.get("/api/payouts/batches/{bid}/export/{fmt}", dependencies=[Depends(auth)])
    def payouts_export(bid: int, fmt: str) -> Response:
        name, media, content = _payouts(desk.payouts.export, bid, fmt)
        store.log_event("audit", f"导出结算批次 #{bid}（{name}）", OWNER_ID, {"batch": bid}, employee="campaign_payouts")
        return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---- airdrop sybil ----

    def _sybil(fn, *args):
        if not team.is_on("airdrop_sybil"):
            raise HTTPException(400, "空投反女巫没上岗")
        try:
            return fn(*args)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/sybil", dependencies=[Depends(auth)])
    def sybil() -> dict:
        return {**desk.sybil.overview(), "enabled": team.is_on("airdrop_sybil"), "hired": team.get("airdrop_sybil") is not None,
                "rule_names": RULE_NAMES, "verdict_names": VERDICT_NAMES}

    @app.put("/api/sybil/settings", dependencies=[Depends(auth)])
    def sybil_settings(body: dict = Body(...)) -> dict:
        try:
            s = desk.sybil.save_settings(body, OWNER_ID)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "settings": s.to_dict()}

    @app.post("/api/sybil/checks", dependencies=[Depends(auth)])
    def sybil_start(body: dict = Body(...)) -> dict:
        cid = _sybil(desk.sybil.start, str(body.get("name", ""))[:80], str(body.get("text", ""))[:400_000], OWNER_ID, sybil_background)
        return {"ok": True, "id": cid}

    @app.post("/api/sybil/profiles", dependencies=[Depends(auth)])
    def sybil_profiles(body: dict = Body(...)) -> dict:
        n = _sybil(desk.sybil.import_profiles, str(body.get("text", ""))[:2_000_000], OWNER_ID)
        return {"ok": True, "imported": n}

    @app.get("/api/sybil/checks/{cid}", dependencies=[Depends(auth)])
    def sybil_check(cid: int) -> dict:
        try:
            c = desk.sybil.check(cid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        c["total"] = len(c.pop("addresses"))
        return c

    @app.get("/api/sybil/checks/{cid}/export/{which}", dependencies=[Depends(auth)])
    def sybil_export(cid: int, which: str) -> Response:
        if which not in ("clean", "excluded", "review", "all"):
            raise HTTPException(404, "没有这个导出")
        try:
            name, content = desk.sybil.export(cid, which)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return Response(content, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return app


def _default_app() -> FastAPI:  # pragma: no cover - process entry point
    from agent.runtime import build_front_desk

    from agent.workforce import build_workforce

    desk, chain = build_front_desk()
    wf = build_workforce(desk.finance.store, chain)
    wf.start()
    svc = None
    if chain and os.environ.get("KAGOU_CHAIN_LISTENER", "1") != "0":
        svc = ChainService(desk.finance.store, chain, wf)
        svc.start()
    if os.environ.get("KAGOU_BACKGROUND", "1") != "0":
        # The chain monitor's pass and the basic treasury alerts, which used to run in the Telegram bot.
        from agent.background import BackgroundDuties
        from agent.skills.finance_ops.alerts import EtherscanV2Source

        api_key = os.environ.get("ETHERSCAN_API_KEY")
        source = None
        if chain and chain.explorer_api_url and api_key:
            source = EtherscanV2Source(chain.explorer_api_url, api_key, {s: t.address for s, t in chain.tokens.items()})
        BackgroundDuties(desk, source, chain.treasury_addresses if chain else (),
                         chain.alert_outflow_threshold if chain else {}).start()
    return create_app(desk.finance, os.environ.get("CHAINSTAFF_CONSOLE_TOKEN", ""), chain, desk=desk, workforce=wf, chain_service=svc,
                      viewer_token=os.environ.get("KAGOU_DEMO_TOKEN") or None)


def __getattr__(name: str):  # pragma: no cover - lets `uvicorn backend.app:app` build lazily
    if name == "app":
        return _default_app()
    raise AttributeError(name)
