"""Owner console API: overview, approvals, ledger, payees, rules, treasury, employee,
Telegram, reports and the audit log.

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
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.skills.finance_ops.insights import TelegramSettings, overview, payee_stats, rules_diff
from agent.skills.finance_ops.schedule import COMMANDS, HELP

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


def create_app(employee: FinanceOpsEmployee, token: str, chain: ChainConfig | None = None, env: dict | None = None) -> FastAPI:
    if not token or len(token) < 16:
        raise RuntimeError("CHAINSTAFF_CONSOLE_TOKEN must be set (at least 16 characters)")
    env = dict(os.environ if env is None else env)
    store = employee.store
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
            store.log_event("audit", "修改规则：" + "；".join(changes), OWNER_ID, {"changes": changes})
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
    def events(kind: str | None = None, limit: int = 100, before: int | None = None) -> list[dict]:
        kinds = tuple(k for k in (kind or "").split(",") if k)
        return store.events(kinds, min(max(limit, 1), 500), before)

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
            "commands": [{"cmd": c, "desc": d, "owner_only": o} for c, d, o in COMMANDS],
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

    return app


def _default_app() -> FastAPI:  # pragma: no cover - process entry point
    from agent.runtime import build_employee

    employee, chain = build_employee()
    return create_app(employee, os.environ.get("CHAINSTAFF_CONSOLE_TOKEN", ""), chain)


def __getattr__(name: str):  # pragma: no cover - lets `uvicorn backend.app:app` build lazily
    if name == "app":
        return _default_app()
    raise AttributeError(name)
