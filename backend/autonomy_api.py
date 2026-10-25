"""Console API for autonomous agents (/api/at/*) and the watcher ingest endpoints (/api/ingest/*).

The console endpoints use the console's bearer token. The ingest endpoints are called by other systems (an app's
logger, a CI job, GitHub), so they never accept the console token: each watcher has its own token or webhook secret.
The API process only writes rows; agents run in the worker process (agent/autonomy/worker.py).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.autonomy import constants as C
from agent.autonomy.agents import TEMPLATES, AgentError
from agent.autonomy.events import EventError
from agent.autonomy.github import verify_signature
from agent.autonomy.missions import MissionError
from agent.autonomy.service import Autonomy, AutonomyError
from agent.autonomy.tools import TOOLS
from agent.autonomy.wakeup import TaskError
from agent.autonomy.watchers import WatcherError
from agent.workforce.engine import WorkforceError

from backend.workforce_api import RateLimit

ERRORS = (AutonomyError, AgentError, TaskError, MissionError, WatcherError, EventError, WorkforceError, ValueError)
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def _since(range_: str | None) -> datetime | None:
    d = RANGES.get(range_ or "")
    return datetime.now(timezone.utc) - d if d else None


def _csv(v: str | None) -> list[str] | None:
    out = [x.strip() for x in (v or "").split(",") if x.strip()]
    return out or None


def mount_autonomy(app: FastAPI, at: Autonomy, auth: Callable, owner: str, write_limit: int = 120, ingest_limit: int = 600) -> None:
    db = at.db
    limiter = RateLimit(write_limit)
    ingest_limiter = RateLimit(ingest_limit)

    def write(request: Request) -> None:
        limiter.check("write")

    A = [Depends(auth)]
    W = [Depends(auth), Depends(write)]

    def run(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except ERRORS as e:
            raise HTTPException(400, str(e)) from e

    def found(v, what: str):
        if v is None:
            raise HTTPException(404, f"没有这个{what}")
        return v

    # ---- meta ----

    @app.get("/api/at/meta", dependencies=A)
    def meta() -> dict:
        return {"templates": TEMPLATES, "work_modes": C.WORK_MODES, "mission_types": C.MISSION_TYPES, "task_kinds": C.TASK_KINDS,
                "severities": C.SEVERITIES, "watcher_kinds": C.WATCHER_KINDS, "permissions": C.AUTONOMY_PERMISSIONS, "human_only": C.HUMAN_ONLY,
                "default_permissions": C.DEFAULT_PERMISSIONS, "tools": [t.public() for t in TOOLS], "limits": at.limits.public(),
                "models": at.model_status(), "sandbox": at.sandbox.info(), "github_token": at.github is not None,
                "skills": [{"id": s["id"], "name": s["name"], "description": s.get("description", ""), "permissions": s["permissions"],
                            "enabled": s["enabled"], "installed": s["installed"], "missing": s["missing"], "needs_chain": s["needs_chain"]}
                           for s in at.wf.skills_view()],
                "last_activity_id": db.last_activity_id()}

    @app.get("/api/at/overview", dependencies=A)
    def overview() -> dict:
        return at.overview()

    @app.get("/api/at/runtime", dependencies=A)
    def runtime() -> dict:
        return at.runtime_health()

    @app.get("/api/at/usage", dependencies=A)
    def usage(range: str = "24h") -> dict:
        since = _since(range) or datetime.now(timezone.utc) - timedelta(days=1)
        return {"totals": db.usage_totals(since), "by_agent": db.cost_by("agent_id", since), "by_mission": db.cost_by("mission_id", since),
                "calls": db.model_calls(since, limit=200), "gateway": at.runtime_health()["gateway"]}

    # ---- agents ----

    @app.get("/api/at/agents", dependencies=A)
    def agents(archived: bool = False) -> list[dict]:
        return at.agent_rows(archived)

    @app.post("/api/at/agents", dependencies=W)
    def create_agent(body: dict = Body(...)) -> dict:
        return run(at.create_agent, body, owner)

    @app.get("/api/at/agents/{aid}", dependencies=A)
    def agent(aid: str) -> dict:
        return run(at.agent_detail, aid)

    @app.put("/api/at/agents/{aid}", dependencies=W)
    def update_agent(aid: str, body: dict = Body(...)) -> dict:
        return run(at.update_agent, aid, body, owner)

    @app.post("/api/at/agents/{aid}/activate", dependencies=W)
    def activate(aid: str) -> dict:
        return run(at.activate, aid, owner)

    @app.post("/api/at/agents/{aid}/tasks", dependencies=W)
    def give_task(aid: str, body: dict = Body(...)) -> dict:
        return run(at.give_task, aid, body, owner)

    @app.get("/api/at/agents/{aid}/memory", dependencies=A)
    def agent_memory(aid: str, q: str = "") -> list[dict]:
        a = run(at.agent_or_raise, aid)
        scopes = [("agent", aid), *(("workspace", w) for w in a.get("workspace_ids") or [])]
        return db.wdb.search_memory(scopes, q, limit=100)

    @app.delete("/api/at/agents/{aid}/memory/{mid}", dependencies=W)
    def forget(aid: str, mid: int) -> dict:
        m = found(db.wdb.memory(mid), "记忆")
        a = run(at.agent_or_raise, aid)
        if not ((m["scope"] == "agent" and m["owner"] == aid) or (m["scope"] == "workspace" and m["owner"] in (a.get("workspace_ids") or []))):
            raise HTTPException(404, "没有这个记忆")
        db.wdb.forget(mid)
        at.activity.audit(f"删除记忆 #{mid}", owner, {"agent": aid})
        return {"ok": True}

    @app.post("/api/at/agents/{aid}/{action}", dependencies=W)
    def agent_action(aid: str, action: str) -> dict:
        return run(at.set_agent_status, aid, action, owner)

    # ---- workspaces ----

    @app.get("/api/at/workspaces", dependencies=A)
    def workspaces() -> list[dict]:
        out = []
        for w in db.workspaces():
            out.append({**w, "watchers": len(db.watchers(workspace_id=w["id"])), "agents": sum(1 for a in db.agents() if w["id"] in (a.get("workspace_ids") or [])),
                        "open_tasks": db.count_tasks(workspace_id=w["id"], status=list(C.TASK_OPEN))})
        return out

    @app.post("/api/at/workspaces", dependencies=W)
    def create_workspace(body: dict = Body(...)) -> dict:
        return run(at.create_workspace, body, owner)

    @app.get("/api/at/workspaces/{wid}", dependencies=A)
    def workspace(wid: str) -> dict:
        w = found(db.workspace(wid), "工作区")
        alive = at.worker_alive()
        ws = []
        for x in db.watchers(workspace_id=wid):
            ok, why = at.watchers.healthy(x, alive)
            ws.append({**at.watchers.get(x["id"]), "healthy": ok, "health": why})
        return {**w, "watchers": ws, "agents": [r for r in at.agent_rows() if any(x["id"] == wid for x in r["workspaces"])],
                "missions": db.missions(workspace_id=wid, limit=100)}

    @app.put("/api/at/workspaces/{wid}", dependencies=W)
    def update_workspace(wid: str, body: dict = Body(...)) -> dict:
        return run(at.update_workspace, wid, body, owner)

    # ---- watchers ----

    @app.get("/api/at/watchers", dependencies=A)
    def watchers(workspace_id: str = "") -> list[dict]:
        alive = at.worker_alive()
        out = []
        for x in db.watchers(workspace_id=workspace_id or None):
            ok, why = at.watchers.healthy(x, alive)
            out.append({**at.watchers.get(x["id"]), "healthy": ok, "health": why})
        return out

    @app.post("/api/at/watchers", dependencies=W)
    def create_watcher(body: dict = Body(...)) -> dict:
        w, secret = run(at.watchers.create, body, owner)
        return {"watcher": w, "secret": secret, "ingest_path": _ingest_path(w)}

    @app.put("/api/at/watchers/{wid}", dependencies=W)
    def update_watcher(wid: str, body: dict = Body(...)) -> dict:
        return run(at.watchers.update, wid, body, owner)

    @app.delete("/api/at/watchers/{wid}", dependencies=W)
    def delete_watcher(wid: str) -> dict:
        run(at.watchers.delete, wid, owner)
        return {"ok": True}

    @app.post("/api/at/watchers/{wid}/rotate", dependencies=W)
    def rotate(wid: str) -> dict:
        w, secret = run(at.watchers.rotate, wid, owner)
        return {"watcher": w, "secret": secret, "ingest_path": _ingest_path(w)}

    @app.post("/api/at/watchers/{wid}/{action}", dependencies=W)
    def watcher_action(wid: str, action: str) -> dict:
        if action not in ("pause", "resume"):
            raise HTTPException(400, "操作只能是 pause / resume")
        return run(at.watchers.set_status, wid, action == "resume", owner)

    def _ingest_path(w: dict) -> str | None:
        return {"app_logs": f"/api/ingest/logs/{w['id']}", "ci_failure": f"/api/ingest/ci/{w['id']}",
                "github_webhook": f"/api/ingest/github/{w['id']}"}.get(w["kind"])

    # ---- missions ----

    @app.get("/api/at/missions", dependencies=A)
    def missions(workspace_id: str = "", status: str = "", agent_id: str = "") -> list[dict]:
        out = []
        for m in db.missions(workspace_id=workspace_id or None, status=_csv(status), agent_id=agent_id or None, limit=500):
            out.append({**m, "task_counts": db.task_status_counts(mission_id=m["id"]),
                        "agents": [{"id": a, "name": at.name(a)} for a in m.get("agent_ids") or []], "lead_name": at.name(m.get("lead_agent_id"))})
        return out

    @app.post("/api/at/missions", dependencies=W)
    def create_mission(body: dict = Body(...)) -> dict:
        return run(at.missions.create, body, owner)

    @app.get("/api/at/missions/{mid}", dependencies=A)
    def mission(mid: str) -> dict:
        m = run(at.missions.get, mid)
        return {**m, "tasks": db.tasks(mission_id=mid, limit=200), "task_counts": db.task_status_counts(mission_id=mid),
                "activity": db.activity(mission_id=mid, limit=100),
                "agents": [{"id": a, "name": at.name(a)} for a in m.get("agent_ids") or []], "lead_name": at.name(m.get("lead_agent_id"))}

    @app.put("/api/at/missions/{mid}", dependencies=W)
    def update_mission(mid: str, body: dict = Body(...)) -> dict:
        return run(at.missions.update, mid, body, owner)

    @app.post("/api/at/missions/{mid}/run", dependencies=W)
    def run_mission(mid: str) -> dict:
        return run(at.missions.run_now, mid, owner)

    @app.post("/api/at/missions/{mid}/{action}", dependencies=W)
    def mission_action(mid: str, action: str) -> dict:
        return run(at.missions.set_status, mid, action, owner)

    # ---- tasks and runs ----

    @app.get("/api/at/tasks", dependencies=A)
    def tasks(workspace_id: str = "", mission_id: str = "", agent_id: str = "", status: str = "", kind: str = "", q: str = "",
              range: str = "", limit: int = 100, offset: int = 0) -> dict:
        kw = dict(workspace_id=workspace_id or None, mission_id=mission_id or None, agent_id=agent_id or None, status=_csv(status), kind=kind or None,
                  q=q or None, since=_since(range))
        return {"items": db.tasks(**kw, limit=max(1, min(limit, 500)), offset=max(0, offset)), "total": db.count_tasks(**kw)}

    @app.get("/api/at/tasks/{tid}", dependencies=A)
    def task(tid: str) -> dict:
        return run(at.task_detail, tid)

    @app.post("/api/at/tasks/{tid}/cancel", dependencies=W)
    def cancel(tid: str, body: dict = Body(default={})) -> dict:
        return run(at.wakeup.cancel, tid, owner, str((body or {}).get("reason") or ""))

    @app.post("/api/at/tasks/{tid}/retry", dependencies=W)
    def retry(tid: str) -> dict:
        return run(at.wakeup.retry, tid, owner)

    @app.post("/api/at/tasks/{tid}/assign", dependencies=W)
    def assign(tid: str, body: dict = Body(...)) -> dict:
        return run(at.wakeup.assign, tid, str(body.get("agent_id") or ""), owner)

    @app.get("/api/at/runs/{rid}", dependencies=A)
    def run_detail(rid: str) -> dict:
        r = found(db.run(rid), "执行记录")
        return {**r, "steps": db.steps(rid)}

    # ---- events ----

    @app.get("/api/at/events", dependencies=A)
    def events(workspace_id: str = "", source: str = "", type: str = "", severity: str = "", watcher_id: str = "", status: str = "",
               range: str = "", limit: int = 100) -> list[dict]:
        return db.events(workspace_id=workspace_id or None, source=source or None, type_=type or None, severity=_csv(severity),
                         watcher_id=watcher_id or None, status=status or None, since=_since(range), limit=max(1, min(limit, 500)))

    @app.get("/api/at/events/{eid}", dependencies=A)
    def event(eid: str) -> dict:
        e = found(db.event(eid), "事件")
        return {**e, "tasks": [db.task(t) for t in e.get("task_ids") or [] if db.task(t)]}

    @app.post("/api/at/events", dependencies=W)
    def manual_event(body: dict = Body(...)) -> dict:
        """A human reports something (an incident, a bug): routed to missions like any watcher event."""
        sev = str(body.get("severity") or "medium")
        if sev not in C.SEVERITIES:
            raise HTTPException(400, "severity 不对")
        ev, created = run(at.events.ingest, workspace_id=str(body.get("workspace_id") or ""), source="manual", type_=str(body.get("type") or "manual.report")[:60],
                          severity=sev, title=str(body.get("title") or "")[:300], payload={"description": str(body.get("description") or "")[:8000], "reported_by": owner})
        at.activity.audit(f"手动上报事件 {ev['id']}", owner, {"event": ev["id"]})
        return {**ev, "deduplicated": not created}

    # ---- activity, notifications, approvals ----

    @app.get("/api/at/activity", dependencies=A)
    def activity(after: int = 0, before: int | None = None, agent_id: str = "", workspace_id: str = "", mission_id: str = "", task_id: str = "",
                 severity: str = "", types: str = "", needs_human: bool | None = None, range: str = "", limit: int = 200) -> list[dict]:
        rows = db.activity(after=after, before=before, agent_id=agent_id or None, workspace_id=workspace_id or None, mission_id=mission_id or None,
                           task_id=task_id or None, severity=_csv(severity), types=_csv(types), needs_human=needs_human, since=_since(range),
                           limit=max(1, min(limit, 1000)), newest_first=not after)
        return _named(rows)

    def _named(rows: list[dict]) -> list[dict]:
        names: dict[str, str] = {}

        def name(i: str) -> str:
            if i not in names:
                names[i] = i if i.startswith("wkr-") else at.name(i)
            return names[i]

        for r in rows:
            r["agent_name"] = name(r["agent_id"]) if r.get("agent_id") else None
            r["target_agent_name"] = name(r["target_agent_id"]) if r.get("target_agent_id") else None
            r["actor_name"] = name(r["actor"])
        return rows

    @app.get("/api/at/stream", dependencies=A)
    async def stream(request: Request, after: int = 0, once: bool = False,
                     last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

        async def gen():
            cursor, idle = start, 0.0
            yield "retry: 2000\n\n"
            while True:
                rows = _named(db.activity(after=cursor, limit=500, newest_first=False))
                for m in rows:
                    cursor = m["id"]
                    yield f"id: {m['id']}\nevent: activity\ndata: {json.dumps(m, ensure_ascii=False, default=str)}\n\n"
                if once:
                    return
                if rows:
                    idle = 0.0
                else:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": ping\n\n"
                if await request.is_disconnected():  # pragma: no cover - real client
                    return
                await asyncio.sleep(1.0)  # pragma: no cover - long-lived stream

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/at/notifications", dependencies=A)
    def notifications(unread: bool = False, limit: int = 100) -> dict:
        return {"items": db.notifications(unread, max(1, min(limit, 500))), "unread": db.unread_count()}

    @app.post("/api/at/notifications/read", dependencies=W)
    def read_notifications(body: dict = Body(default={})) -> dict:
        ids = (body or {}).get("ids")
        return {"marked": db.mark_read([int(i) for i in ids] if isinstance(ids, list) else None)}

    @app.get("/api/at/approvals", dependencies=A)
    def approvals(status: str = "pending", limit: int = 100) -> list[dict]:
        out = []
        for a in db.approvals(status=status or None, limit=max(1, min(limit, 500))):
            out.append({**a, "agent_name": at.name(a.get("agent_id"))})
        return out

    @app.post("/api/at/approvals/{apid}", dependencies=W)
    def decide(apid: int, body: dict = Body(...)) -> dict:
        if not isinstance(body.get("approve"), bool):
            raise HTTPException(400, "approve 必须是 true 或 false")
        return run(at.decide, apid, body["approve"], owner, str(body.get("note") or "")[:1000])

    @app.get("/api/at/artifacts/{art}", dependencies=A)
    def artifact(art: str) -> dict:
        return found(db.artifact(art), "产出")

    # ---- ingest: other systems push events here with the watcher's own credential ----

    def _watcher(wid: str, kind: str) -> dict:
        ingest_limiter.check(wid)
        w = db.watcher(wid)
        if w is None or w["kind"] != kind:
            raise HTTPException(404, "没有这个 Watcher")
        if w["status"] != "active":
            raise HTTPException(409, "这个 Watcher 已暂停")
        return w

    def _token(w: dict, authorization: str, x_kagou_token: str) -> None:
        if not at.watchers.token_ok(w, authorization.removeprefix("Bearer ") or x_kagou_token):
            raise HTTPException(401, "Watcher 令牌不对")

    @app.post("/api/ingest/logs/{wid}")
    async def ingest_logs(wid: str, request: Request, authorization: str = Header(default=""), x_kagou_token: str = Header(default="")) -> dict:
        w = _watcher(wid, "app_logs")
        _token(w, authorization, x_kagou_token)
        body = await _json(request)
        items = body if isinstance(body, list) else [body]
        if len(items) > 100:
            raise HTTPException(413, "一次最多 100 条日志")
        return {"results": [run(at.watchers.ingest_log, w, x) for x in items]}

    @app.post("/api/ingest/ci/{wid}")
    async def ingest_ci(wid: str, request: Request, authorization: str = Header(default=""), x_kagou_token: str = Header(default="")) -> dict:
        w = _watcher(wid, "ci_failure")
        _token(w, authorization, x_kagou_token)
        return run(at.watchers.ingest_ci, w, await _json(request))

    @app.post("/api/ingest/github/{wid}")
    async def ingest_github(wid: str, request: Request, x_hub_signature_256: str = Header(default=""), x_github_event: str = Header(default=""),
                            x_github_delivery: str = Header(default="")) -> dict:
        w = _watcher(wid, "github_webhook")
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "请求太大")
        if not verify_signature(w.get("webhook_secret") or "", raw, x_hub_signature_256):
            raise HTTPException(401, "签名不对")
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "不是 JSON") from None
        return run(at.watchers.ingest_github, w, x_github_event, x_github_delivery or None, body)

    async def _json(request: Request):
        raw = await request.body()
        if len(raw) > 1_000_000:
            raise HTTPException(413, "请求太大")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "不是 JSON") from None
