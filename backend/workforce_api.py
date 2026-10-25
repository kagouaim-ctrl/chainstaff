"""Console API for the AI workforce terminal (/api/wf/*) and the terminal's static files (/terminal/).

Same bearer token as the rest of the console. Writes are rate limited per process.
The live stream is Server-Sent Events over a fetch() body, so the token stays in a header;
`after` (or Last-Event-ID) resumes exactly where a dropped connection stopped.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from agent.config import ChainConfig
from agent.workforce import Workforce, WorkforceError
from agent.workforce import db as D
from agent.workforce import missions as M
from agent.workforce.anchor import decode_calldata
from agent.workforce.engine import ALL_PERMISSIONS, PROVIDERS, SQUAD_PERMISSIONS

TERMINAL = Path(__file__).resolve().parent.parent / "web" / "terminal" / "dist"
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
TASK_CSV = ["id", "kind", "title", "status", "priority", "assigned_agent", "skill", "created_at", "started_at", "finished_at",
            "steps", "cost_usd", "tokens", "result_hash", "error"]


class RateLimit:
    """Sliding window per key; enough for a single-owner console, not a public API gateway."""

    def __init__(self, limit: int, window_s: float = 60) -> None:
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, "操作太频繁，请稍后再试")
            q.append(now)


def _since(range_: str | None) -> datetime | None:
    d = RANGES.get(range_ or "")
    return datetime.now(timezone.utc) - d if d else None


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"日期格式不对：{v}") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def mount_workforce(app: FastAPI, wf: Workforce, auth: Callable, owner: str, chain: ChainConfig | None = None,
                    write_limit: int = 120) -> None:
    db = wf.db
    limiter = RateLimit(write_limit)

    def write(request: Request) -> None:
        limiter.check("write")

    A = [Depends(auth)]
    W = [Depends(auth), Depends(write)]

    def run(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except WorkforceError as e:
            raise HTTPException(400, str(e)) from e

    def explorer(kind: str, value: str | None) -> str | None:
        if not (chain and chain.explorer_tx_url and value):
            return None
        return chain.explorer_tx_url.replace("/tx/", f"/{kind}/") + value

    # ---- meta ----

    @app.get("/api/wf/ai-usage", dependencies=A)
    def ai_usage(days: int = 31) -> dict:
        """Model spend per UTC day, provider and model, with the budgets and limits in force."""
        return wf.gateway.usage(days=max(1, min(days, 366)))

    @app.get("/api/wf/meta", dependencies=A)
    def meta() -> dict:
        return {"providers": list(PROVIDERS), "permissions": ALL_PERMISSIONS, "squad_permissions": SQUAD_PERMISSIONS, "task_statuses": list(D.TASK_STATUSES),
                "message_kinds": list(D.MSG_KINDS), "default_model": wf.planner.model,
                "skills": [{"id": s["id"], "name": s["name"], "permissions": s["permissions"]} for s in wf.skills_view()],
                "engine": wf.overview()["engine"], "network": chain.network if chain else None,
                "chain_id": chain.chain_id if chain else None, "explorer": chain.explorer_tx_url if chain else None,
                "last_message_id": db.last_message_id()}

    @app.get("/api/wf/overview", dependencies=A)
    def overview(range: str = "24h") -> dict:
        out = wf.overview(_since(range))
        out["recent_tasks"] = db.tasks(kind="goal", limit=10)[0]
        out["onchain"] = db.chain_records(limit=10)
        out["activity"] = db.messages(limit=30, newest_first=True)
        return out

    # ---- agents ----

    @app.get("/api/wf/agents", dependencies=A)
    def agents(range: str = "all") -> list[dict]:
        return wf.agent_views(_since(range))

    @app.post("/api/wf/agents", dependencies=W)
    def create_agent(body: dict = Body(...)) -> dict:
        return run(wf.create_agent, body, owner)

    @app.get("/api/wf/agents/{aid}", dependencies=A)
    def agent(aid: str) -> dict:
        view = next((a for a in wf.agent_views() if a["id"] == aid), None)
        if view is None:
            raise HTTPException(404, f"没有员工 {aid}")
        view["tasks"] = db.tasks(agent=aid, limit=50)[0]
        view["logs"] = db.messages(sender=aid, kinds=["tool", "agent", "task"], limit=100, newest_first=True)
        view["memory"] = db.search_memory([("agent", aid)], limit=50)
        view["workspaces"] = [w for w in db.workspaces() if aid in w["members"] or w["manager_id"] == aid]
        view["can_run"] = {s: wf.can_run(view, s) for s in view["skills"]}
        view["audit"] = [e for e in wf.store.events(("audit",), limit=300, employee="workforce") if (e["data"] or {}).get("agent") == aid][:30]
        return view

    @app.post("/api/wf/agents/{aid}/run", dependencies=W)
    def run_agent(aid: str, body: dict = Body(...)) -> dict:
        """Direct assignment: one agent, one skill, tracked as a one-task mission."""
        return run(wf.assign_direct, aid, str(body.get("skill", "")), body.get("input") or {}, owner,
                   workspace_id=body.get("workspace_id") or None, title=str(body.get("title", "")), priority=int(body.get("priority", 2)),
                   budget_usd=body.get("budget_usd", "1"), timeout_s=int(body.get("timeout_s", 600)))

    @app.put("/api/wf/agents/{aid}", dependencies=W)
    def update_agent(aid: str, body: dict = Body(...)) -> dict:
        return run(wf.update_agent, aid, body, owner)

    @app.post("/api/wf/agents/{aid}/duplicate", dependencies=W)
    def duplicate_agent(aid: str) -> dict:
        return run(wf.duplicate_agent, aid, owner)

    @app.post("/api/wf/agents/{aid}/status", dependencies=W)
    def agent_status(aid: str, body: dict = Body(...)) -> dict:
        return run(wf.set_agent_status, aid, str(body.get("status", "")), owner)

    @app.post("/api/wf/agents/bulk", dependencies=W)
    def agents_bulk(body: dict = Body(...)) -> dict:
        ids, status = list(body.get("ids") or []), str(body.get("status", ""))
        ok, errors = [], {}
        for aid in ids[:200]:
            try:
                wf.set_agent_status(aid, status, owner)
                ok.append(aid)
            except WorkforceError as e:
                errors[aid] = str(e)
        return {"ok": ok, "errors": errors}

    @app.delete("/api/wf/agents/{aid}", dependencies=W)
    def delete_agent(aid: str) -> dict:
        run(wf.delete_agent, aid, owner)
        return {"ok": True}

    # ---- workspaces and the timeline ----

    @app.get("/api/wf/workspaces", dependencies=A)
    def workspaces() -> list[dict]:
        out = []
        for w in db.workspaces():
            goals, total = db.tasks(workspace_id=w["id"], kind="goal", limit=50)
            out.append({**w, "goals": goals, "goal_count": total,
                        "squads": [{"id": q["id"], "name": q["name"], "status": q["status"], "manager_id": q["manager_id"]} for q in db.squads(w["id"])]})
        return out

    @app.post("/api/wf/workspaces", dependencies=W)
    def create_workspace(body: dict = Body(...)) -> dict:
        return run(wf.create_workspace, body, owner)

    @app.put("/api/wf/workspaces/{wid}", dependencies=W)
    def update_workspace(wid: str, body: dict = Body(...)) -> dict:
        return run(wf.update_workspace, wid, body, owner)

    @app.get("/api/wf/workspaces/{wid}/timeline", dependencies=A)
    def timeline(wid: str, after: int = 0, before: int | None = None, limit: int = 200, task: str | None = None) -> list[dict]:
        run(wf.workspace_or_404, wid)
        if before:
            return db.messages(workspace_id=wid, before=before, root_id=task, limit=min(limit, 500), newest_first=True)[::-1]
        if after:
            return db.messages(workspace_id=wid, after=after, root_id=task, limit=min(limit, 500))
        return db.messages(workspace_id=wid, root_id=task, limit=min(limit, 500), newest_first=True)[::-1]

    def _int(body: dict, key: str, default: int) -> int:
        try:
            return int(body.get(key, default))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} 必须是整数") from None

    def _submit(wid: str, body: dict, squad_id: str | None = None, key: str | None = None) -> dict:
        # A retried request (double click, network retry) carries the same key and gets the first mission back.
        return run(wf.submit_goal, wid, str(body.get("text", "")), owner, priority=_int(body, "priority", 2),
                   max_steps=_int(body, "max_steps", 20), budget_usd=body.get("budget_usd", "1"), timeout_s=_int(body, "timeout_s", 600),
                   squad_id=squad_id or body.get("squad_id") or None, manager_id=body.get("manager_id") or None,
                   attachments=body.get("attachments") or None, idempotency_key=key or body.get("idempotency_key") or None)

    @app.post("/api/wf/workspaces/{wid}/goals", dependencies=W)
    def submit_goal(wid: str, body: dict = Body(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
        return _submit(wid, body, key=idempotency_key)

    # ---- squads: the team a mission is given to ----

    @app.get("/api/wf/squads", dependencies=A)
    def squads(workspace: str = "") -> list[dict]:
        return wf.squad_views(workspace or None)

    @app.post("/api/wf/squads", dependencies=W)
    def create_squad(body: dict = Body(...)) -> dict:
        return run(wf.create_squad, body, owner)

    def _squad_view(sid: str) -> dict:
        sq = run(wf.squad_or_404, sid)
        return next(v for v in wf.squad_views(sq["workspace_id"]) if v["id"] == sid)

    @app.get("/api/wf/squads/{sid}", dependencies=A)
    def squad(sid: str) -> dict:
        return _squad_view(sid)

    @app.put("/api/wf/squads/{sid}", dependencies=W)
    def update_squad(sid: str, body: dict = Body(...)) -> dict:
        run(wf.update_squad, sid, body, owner)
        return _squad_view(sid)

    @app.delete("/api/wf/squads/{sid}", dependencies=W)
    def delete_squad(sid: str) -> dict:
        run(wf.delete_squad, sid, owner)
        return {"ok": True}

    @app.post("/api/wf/squads/{sid}/members", dependencies=W)
    def add_member(sid: str, body: dict = Body(...)) -> dict:
        run(wf.squad_add_member, sid, str(body.get("agent_id", "")), owner)
        return _squad_view(sid)

    @app.delete("/api/wf/squads/{sid}/members/{aid}", dependencies=W)
    def remove_member(sid: str, aid: str) -> dict:
        run(wf.squad_remove_member, sid, aid, owner)
        return _squad_view(sid)

    @app.post("/api/wf/squads/{sid}/manager", dependencies=W)
    def set_manager(sid: str, body: dict = Body(...)) -> dict:
        run(wf.set_squad_manager, sid, str(body.get("agent_id", "")), owner)
        return _squad_view(sid)

    @app.post("/api/wf/squads/{sid}/status", dependencies=W)
    def squad_status(sid: str, body: dict = Body(...)) -> dict:
        run(wf.set_squad_status, sid, str(body.get("status", "")), owner)
        return _squad_view(sid)

    @app.post("/api/wf/squads/{sid}/missions", dependencies=W)
    def squad_mission(sid: str, body: dict = Body(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
        sq = run(wf.squad_or_404, sid)
        return _submit(sq["workspace_id"], body, sid, key=idempotency_key)

    @app.post("/api/wf/missions/preflight", dependencies=A)
    def preflight(body: dict = Body(...)) -> dict:
        """What would stop this mission, checked without creating anything (the composer calls it as you type)."""
        return wf.preflight(str(body.get("workspace_id") or ""), squad_id=body.get("squad_id") or None, manager_id=body.get("manager_id") or None,
                            text=str(body.get("text", "")) + "".join(f"\n{f.get('text', '')}" for f in (body.get("attachments") or []) if isinstance(f, dict)),
                            budget_usd=body.get("budget_usd", "1"))

    @app.post("/api/wf/workspaces/{wid}/notes", dependencies=W)
    def note(wid: str, body: dict = Body(...)) -> dict:
        return {"id": run(wf.note, wid, str(body.get("text", "")), owner)}

    @app.post("/api/wf/messages", dependencies=W)
    def send(body: dict = Body(...)) -> dict:
        mid = run(wf.send, str(body.get("sender") or "user"), str(body.get("recipient", "")), str(body.get("text", "")),
                  task_id=body.get("task_id"), dedupe_key=body.get("dedupe_key"))
        return {"id": mid, "duplicate": mid is None}

    # ---- missions: a read-only view over goals, so existing data and endpoints stay as they are ----

    def _with_urls(p: dict) -> dict:
        p["tx_url"] = explorer("tx", p["tx_hash"])
        return p

    @app.get("/api/wf/missions", dependencies=A)
    def missions(workspace: str = "", squad: str = "", status: str = "", agent: str = "", q: str = "", limit: int = 100) -> list[dict]:
        out = M.missions(db, workspace_id=workspace or None, squad_id=squad or None, status=status.split(",") if status else None, agent=agent or None,
                         q=q or None, limit=limit)
        for m in out:
            _with_urls(m["proof"])
        return out

    @app.get("/api/wf/missions/{mid}", dependencies=A)
    def mission(mid: str) -> dict:
        t = run(wf.task_or_404, mid)
        if t["kind"] != "goal":
            raise HTTPException(404, f"{mid} 是任务，不是 Mission；它属于 {t['root_id']}")
        out = M.mission(db, t)
        _with_urls(out["proof"])
        return out

    @app.get("/api/wf/ledger", dependencies=A)
    def ledger(limit: int = 200) -> list[dict]:
        out = M.ledger(db, limit=limit)
        for r in out:
            _with_urls(r["proof"])
        return out

    # ---- tasks ----

    def _task_query(q, status, agent, kind, priority, start, end, sort, order, page, size):
        return db.tasks(q=q or None, status=status.split(",") if status else None, agent=agent or None, kind=kind or None,
                        priority=priority, since=_when(start), until=_when(end), sort=sort, desc=order != "asc",
                        limit=min(max(size, 1), 500), offset=max(page - 1, 0) * min(max(size, 1), 500))

    @app.get("/api/wf/tasks", dependencies=A)
    def tasks(q: str = "", status: str = "", agent: str = "", kind: str = "", priority: int | None = None, start: str | None = None,
              end: str | None = None, sort: str = "created_at", order: str = "desc", page: int = 1, size: int = 50) -> dict:
        items, total = _task_query(q, status, agent, kind, priority, start, end, sort, order, page, size)
        counts = db.status_counts(kind=kind or None, agent=agent or None, q=q or None)
        return {"items": items, "total": total, "page": page, "size": size, "counts": {st: counts.get(st, 0) for st in D.TASK_STATUSES}}

    @app.get("/api/wf/tasks.csv", dependencies=A)
    def tasks_csv(q: str = "", status: str = "", agent: str = "", kind: str = "", priority: int | None = None,
                  start: str | None = None, end: str | None = None) -> Response:
        items, _ = _task_query(q, status, agent, kind, priority, start, end, "created_at", "desc", 1, 500)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=TASK_CSV, extrasaction="ignore")
        w.writeheader()
        for t in items:
            w.writerow(t)
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="tasks.csv"'})

    @app.get("/api/wf/tasks/{tid}", dependencies=A)
    def task(tid: str) -> dict:
        t = run(wf.task_or_404, tid)
        root = db.task(t["root_id"])
        subs = db.subtasks(root["id"])
        recs = db.chain_records(task_id=root["id"])
        for r in recs:
            r["tx_url"] = explorer("tx", r["tx_hash"])
        return {"task": t, "root": root, "subtasks": subs, "messages": db.messages(root_id=root["id"], limit=1000),
                "approvals": db.approvals(task_id=root["id"]), "chain": recs,
                "graph": {"nodes": [{"id": s["id"], "title": s["title"], "status": s["status"], "agent": s["assigned_agent"], "skill": s["skill"]} for s in subs],
                          "edges": [{"from": d, "to": s["id"]} for s in subs for d in s["depends_on"]]}}

    @app.post("/api/wf/tasks/{tid}/cancel", dependencies=W)
    def cancel(tid: str, body: dict = Body(default={})) -> dict:
        return run(wf.cancel, tid, owner, str(body.get("reason", "")))

    @app.post("/api/wf/tasks/{tid}/retry", dependencies=W)
    def retry(tid: str) -> dict:
        return run(wf.retry, tid, owner)

    @app.post("/api/wf/tasks/{tid}/anchor", dependencies=W)
    def anchor(tid: str) -> dict:
        return run(wf.anchor_task, tid, owner)

    # ---- approvals ----

    @app.get("/api/wf/approvals", dependencies=A)
    def approvals(status: str | None = None) -> list[dict]:
        return db.approvals(status=status or None, limit=200)

    @app.post("/api/wf/approvals/{aid}", dependencies=W)
    def decide(aid: int, body: dict = Body(...)) -> dict:
        return run(wf.decide, aid, bool(body.get("approve")), owner, str(body.get("note", "")), bool(body.get("anchor", False)))

    # ---- memory ----

    @app.get("/api/wf/memory", dependencies=A)
    def memory(q: str = "", scope: str = "", owner_id: str = "", agent: str = "") -> list[dict]:
        scopes = wf.memory_scopes(agent_id=agent or None)
        if scope:
            scopes = [s for s in scopes if s[0] == scope and (not owner_id or s[1] == owner_id)]
        return db.search_memory(scopes, q, limit=200)

    @app.post("/api/wf/memory", dependencies=W)
    def add_memory(body: dict = Body(...)) -> dict:
        return run(wf.add_memory, body, owner)

    @app.put("/api/wf/memory/{mid}", dependencies=W)
    def edit_memory(mid: int, body: dict = Body(...)) -> dict:
        if not db.update_memory(mid, body.get("text"), body.get("ttl_days")):
            raise HTTPException(404, f"没有记忆 #{mid}，或没有可改的内容")
        return db.memory(mid)

    @app.delete("/api/wf/memory/{mid}", dependencies=W)
    def forget(mid: int) -> dict:
        if not db.forget(mid):
            raise HTTPException(404, f"没有记忆 #{mid}")
        wf.store.log_event("audit", f"删除记忆 #{mid}", owner, {"memory": mid}, employee="workforce")
        return {"ok": True}

    # ---- schedules ----

    @app.get("/api/wf/schedules", dependencies=A)
    def schedules() -> list[dict]:
        return [{**s, "runs": db.schedule_runs(s["id"], 10)} for s in db.schedules()]

    @app.post("/api/wf/schedules", dependencies=W)
    def add_schedule(body: dict = Body(...)) -> dict:
        return run(wf.add_schedule, body, owner)

    @app.post("/api/wf/schedules/{sid}/{action}", dependencies=W)
    def schedule_action(sid: int, action: str) -> dict:
        if action in ("enable", "disable"):
            return run(wf.set_schedule_enabled, sid, action == "enable", owner)
        if action == "run":
            return run(wf.run_schedule_now, sid, owner)
        raise HTTPException(400, "操作只能是 enable / disable / run")

    @app.delete("/api/wf/schedules/{sid}", dependencies=W)
    def delete_schedule(sid: int) -> dict:
        if not db.delete_schedule(sid):
            raise HTTPException(404, f"没有定时任务 #{sid}")
        return {"ok": True}

    # ---- skills ----

    @app.get("/api/wf/skills", dependencies=A)
    def skills() -> list[dict]:
        return wf.skills_view()

    @app.post("/api/wf/skills/{sid}/run", dependencies=W)
    def run_skill(sid: str, body: dict = Body(...)) -> dict:
        return run(wf.run_skill, sid, body.get("input") or {}, owner)

    @app.post("/api/wf/skills/{sid}/{action}", dependencies=W)
    def skill_action(sid: str, action: str) -> dict:
        return run(wf.skill_action, sid, action, owner)

    # ---- on-chain ----

    @app.get("/api/wf/chain", dependencies=A)
    def chain_status() -> dict:
        out: dict = {"configured": wf.reader is not None, "network": chain.network if chain else None,
                     "chain_id": chain.chain_id if chain else None, "explorer": chain.explorer_tx_url if chain else None,
                     "anchor": None, "rpc": None}
        if wf.reader is not None:
            t0 = time.monotonic()
            try:
                head = wf.reader.block("latest")
                gas = wf.reader.gas_price()
                out["rpc"] = {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000), "block": head["number"],
                              "block_time": head["timestamp"], "gas_price_wei": str(gas)}
            except Exception as e:
                out["rpc"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        if wf.anchor is not None:
            a = {"address": wf.anchor.address, "url": explorer("address", wf.anchor.address)}
            try:
                a["bnb"] = str(wf.reader.native_balance(wf.anchor.address))
            except Exception as e:
                a["error"] = str(e)[:200]
            out["anchor"] = a
        return out

    @app.get("/api/wf/chain/records", dependencies=A)
    def chain_records(status: str | None = None) -> list[dict]:
        head = None
        if wf.reader is not None:
            try:
                head = wf.reader.block_number()
            except Exception:
                head = None
        out = []
        for r in db.chain_records(status=status or None, limit=500):
            r["tx_url"] = explorer("tx", r["tx_hash"])
            r["confirmations"] = head - r["block"] + 1 if head and r["block"] else None
            out.append(r)
        return out

    @app.get("/api/wf/chain/verify/{tx_hash}", dependencies=A)
    def verify(tx_hash: str) -> dict:
        """Read an anchoring transaction back from the chain and compare it with the stored result."""
        if wf.reader is None:
            raise HTTPException(400, "没有配置链")
        tx = wf.reader.transaction(tx_hash)
        if not tx:
            raise HTTPException(404, "链上查不到这笔交易")
        decoded = decode_calldata(tx.get("input") or "0x")
        if decoded is None:
            return {"ok": False, "reason": "不是 ChainStaff 任务哈希交易"}
        t = db.task(decoded["task_id"])
        return {"ok": bool(t and t["result_hash"] == decoded["result_hash"]), "onchain": decoded,
                "stored_hash": t["result_hash"] if t else None, "block": int(tx["blockNumber"], 16) if tx.get("blockNumber") else None}

    @app.get("/api/wf/registry", dependencies=A)
    def registry() -> list[dict]:
        return [{"id": a["id"], "name": a["name"], "role": a["role"], "status": a["status"], "onchain": a["onchain"],
                 "address_url": explorer("address", a["onchain"].get("address")) if a["onchain"].get("address") else None}
                for a in db.agents()]

    # ---- activity and the live stream ----

    @app.get("/api/wf/activity", dependencies=A)
    def activity(after: int = 0, before: int | None = None, kinds: str = "", agent: str = "", task: str = "",
                 limit: int = 500) -> list[dict]:
        k = [x for x in kinds.split(",") if x] or None
        if after:
            return db.messages(after=after, kinds=k, sender=agent or None, root_id=task or None, limit=min(limit, 2000))
        return db.messages(before=before, kinds=k, sender=agent or None, root_id=task or None, limit=min(limit, 2000), newest_first=True)

    @app.get("/api/wf/stream", dependencies=A)
    async def stream(request: Request, after: int = 0, once: bool = False,
                     last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

        async def gen():
            cursor, idle = start, 0.0
            yield "retry: 2000\n\n"
            while True:
                rows = db.messages(after=cursor, limit=500)
                for m in rows:
                    cursor = m["id"]
                    yield f"id: {m['id']}\nevent: message\ndata: {json.dumps(m, ensure_ascii=False)}\n\n"
                if once:
                    return
                if rows:
                    idle = 0.0
                else:
                    idle += 0.5
                    if idle >= 15:
                        idle = 0.0
                        yield ": ping\n\n"
                if await request.is_disconnected():  # pragma: no cover - real client
                    return
                await asyncio.sleep(0.5)  # pragma: no cover - long-lived stream

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- the terminal itself ----

    @app.get("/terminal")
    def terminal_root() -> Response:
        return Response(status_code=307, headers={"Location": "/terminal/"})

    @app.get("/terminal/{path:path}")
    def terminal(path: str) -> FileResponse:
        if not TERMINAL.exists():
            raise HTTPException(404, "终端还没有构建：cd web/terminal && npm ci && npm run build")
        f = (TERMINAL / path).resolve()
        if path and f.is_file() and TERMINAL.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(TERMINAL / "index.html")
