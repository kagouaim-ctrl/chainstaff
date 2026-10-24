"""Missions: the unit of work the terminal shows, read from the existing goal and subtask rows.

A mission is what the user hands to the AI workforce ("Analyze this BSC contract 0x…"). It is the
goal task; its tasks are the goal's subtasks, each run by one agent, each with its own result.
Nothing new is stored: this module only reads, so every goal created before missions existed is a
mission too, and the old /api/wf/tasks endpoints keep returning exactly what they did.

    Mission (goal) ─▶ Task (subtask) ─▶ Agent ─▶ Execution (tool run) ─▶ Result (hash)
                 └▶ approvals, and the on-chain record of the approved result

Verification is kept apart from completion. A mission is "off-chain completed" when a human
approves its result; it is "on-chain confirmed" only when the anchoring transaction is in a block.
"""

from __future__ import annotations

from typing import Iterable

from . import db as D

STAGE = {D.PENDING: "planning", D.RUNNING: "planning", D.ASSIGNED: "planning", D.WAITING: "executing",
         D.REVIEW: "review", D.COMPLETED: "completed", D.FAILED: "failed", D.CANCELLED: "cancelled"}

# proof states, in the order a result moves through them
IN_PROGRESS, AWAITING_REVIEW, OFFCHAIN, ONCHAIN_PENDING, ONCHAIN_CONFIRMED, NOT_COMPLETED = (
    "in_progress", "awaiting_review", "offchain_completed", "onchain_pending", "onchain_confirmed", "not_completed")
PROOF_STATES = (IN_PROGRESS, AWAITING_REVIEW, OFFCHAIN, ONCHAIN_PENDING, ONCHAIN_CONFIRMED, NOT_COMPLETED)


def proof(goal: dict, records: list[dict]) -> dict:
    """Where the mission's result stands between "done by the agents" and "confirmed on chain"."""
    rec = (next((r for r in records if r["status"] == "confirmed"), None)
           or next((r for r in records if r["status"] == "pending"), None)
           or (records[0] if records else None))
    if rec and rec["status"] == "confirmed":
        state = ONCHAIN_CONFIRMED
    elif rec and rec["status"] == "pending":
        state = ONCHAIN_PENDING
    elif goal["status"] == D.COMPLETED:
        state = OFFCHAIN
    elif goal["status"] == D.REVIEW:
        state = AWAITING_REVIEW
    elif goal["status"] in (D.FAILED, D.CANCELLED):
        state = NOT_COMPLETED
    else:
        state = IN_PROGRESS
    out = {"state": state, "result_hash": goal["result_hash"], "record_id": None, "tx_hash": None, "block": None,
           "network": None, "chain_status": None, "error": None, "confirmed_at": None}
    if rec:
        out.update(record_id=rec["id"], tx_hash=rec["tx_hash"], block=rec["block"], network=rec["network"],
                   chain_status=rec["status"], error=rec["error"], confirmed_at=rec["confirmed_at"])
    return out


def _task_row(t: dict) -> dict:
    return {k: t[k] for k in ("id", "title", "skill", "assigned_agent", "status", "depends_on", "attempts", "error", "result_hash",
                              "cost_usd", "created_at", "started_at", "finished_at")}


def view(goal: dict, subs: list[dict], approvals: Iterable[dict], records: list[dict]) -> dict:
    counts = {s: 0 for s in D.TASK_STATUSES}
    for s in subs:
        counts[s["status"]] += 1
    total = len(subs)
    settled = counts[D.COMPLETED] + counts[D.FAILED] + counts[D.CANCELLED]
    agents = list(dict.fromkeys([goal["assigned_agent"], *(s["assigned_agent"] for s in subs)]))
    pending = [{"id": a["id"], "kind": a["kind"], "title": a["title"], "at": a["at"]} for a in approvals if a["status"] == "pending"]
    result = goal["result"] or {}
    return {
        "id": goal["id"], "workspace_id": goal["workspace_id"], "name": goal["title"], "brief": goal["description"],
        "status": goal["status"], "stage": STAGE[goal["status"]], "priority": goal["priority"], "owner": goal["assigned_agent"],
        "planner": goal["planner"], "created_by": goal["created_by"], "created_at": goal["created_at"], "started_at": goal["started_at"],
        "finished_at": goal["finished_at"], "deadline_at": goal["deadline_at"], "updated_at": goal["updated_at"],
        "budget_usd": goal["budget_usd"], "cost_usd": goal["cost_usd"], "tokens": goal["tokens"], "steps": goal["steps"],
        "max_steps": goal["max_steps"], "error": goal["error"],
        "progress": {"total": total, "settled": settled, "completed": counts[D.COMPLETED], "failed": counts[D.FAILED],
                     "cancelled": counts[D.CANCELLED], "running": counts[D.RUNNING] + counts[D.ASSIGNED], "pending": counts[D.PENDING]},
        "agents": [a for a in agents if a],
        "tasks": [_task_row(s) for s in subs],
        "approvals": pending,
        "deliverable": {"result_hash": goal["result_hash"], "report": bool(result.get("report_md")),
                        "findings": len(result.get("findings") or []), "has_output": "output" in result} if goal["result"] else None,
        "proof": proof(goal, records),
    }


def _records_by_task(db: D.WorkforceDB) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in db.chain_records(limit=2000):
        if r["task_id"]:
            out.setdefault(r["task_id"], []).append(r)
    return out


def _approvals_by_task(db: D.WorkforceDB, status: str | None = "pending") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in db.approvals(status=status, limit=1000):
        if a["task_id"]:
            out.setdefault(a["task_id"], []).append(a)
    return out


def missions(db: D.WorkforceDB, *, workspace_id: str | None = None, status: list[str] | None = None, agent: str | None = None,
             q: str | None = None, limit: int = 100) -> list[dict]:
    goals, _ = db.tasks(kind="goal", workspace_id=workspace_id, status=status, q=q, limit=min(limit, 500))
    subs = db.tasks_by_root([g["id"] for g in goals])
    recs, aps = _records_by_task(db), _approvals_by_task(db)
    out = [view(g, subs[g["id"]], aps.get(g["id"], []), recs.get(g["id"], [])) for g in goals]
    if agent:
        out = [m for m in out if agent in m["agents"]]
    return out


def mission(db: D.WorkforceDB, goal: dict) -> dict:
    subs = db.subtasks(goal["id"])
    return view(goal, subs, db.approvals(task_id=goal["id"]), db.chain_records(task_id=goal["id"]))


def ledger(db: D.WorkforceDB, *, limit: int = 200) -> list[dict]:
    """Execution ledger: each mission that produced a result, with every agent execution inside it.

    The anchored payload is the mission's result hash; the task rows say which agent produced
    which part of it and with what hash, so a confirmed transaction can be traced back to work.
    """
    goals, _ = db.tasks(kind="goal", status=[D.REVIEW, D.COMPLETED, D.FAILED, D.CANCELLED], sort="updated_at", limit=min(limit, 500))
    subs = db.tasks_by_root([g["id"] for g in goals])
    recs = _records_by_task(db)
    out = []
    for g in goals:
        executed = [s for s in subs[g["id"]] if s["status"] in (D.COMPLETED, D.FAILED)]
        if g["status"] == D.CANCELLED and not executed:
            continue
        p = proof(g, recs.get(g["id"], []))
        out.append({
            "mission": g["id"], "workspace_id": g["workspace_id"], "name": g["title"], "status": g["status"], "owner": g["assigned_agent"],
            "finished_at": g["finished_at"] or g["updated_at"], "result_hash": g["result_hash"], "proof": p,
            "executions": [{"task": s["id"], "title": s["title"], "agent": s["assigned_agent"], "skill": s["skill"], "status": s["status"],
                            "result_hash": s["result_hash"], "error": s["error"], "finished_at": s["finished_at"]} for s in executed],
        })
    return out
