"""Missions (the read-only view over goals), direct assignment to one agent, and the execution ledger."""

from __future__ import annotations

import pytest

from agent.execution import RpcSender
from agent.store import Store
from agent.workforce import Workforce, WorkforceError
from agent.workforce import db as D
from agent.workforce import missions as M
from agent.workforce.anchor import Anchor
from test_workforce import EOA, TOKEN, WALLET, FakeWallet, fake, reader, team, wf  # noqa: F401  (fixtures)


@pytest.fixture
def anchored(fake, reader):
    w = Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)}, anchor=Anchor(RpcSender(FakeWallet(), reader.rpc, 97), reader, WALLET))
    yield w
    w.stop()


def test_mission_view_follows_a_goal_through_review_approval_and_the_chain(anchored, fake):
    wf = anchored
    t = team(wf)
    goal = wf.submit_goal(t.ws["id"], f"Analyze this BSC contract {TOKEN}", "owner")
    m = M.mission(wf.db, goal)
    assert m["stage"] == "planning" and m["progress"]["total"] == 0 and m["proof"]["state"] == M.IN_PROGRESS and m["deliverable"] is None
    wf.run_until_idle()

    m = M.mission(wf.db, wf.db.task(goal["id"]))
    p = m["progress"]
    assert m["stage"] == "review" and p["total"] == 6 and p["completed"] == 5 and p["failed"] == 1 and p["settled"] == 6
    assert m["owner"] == t.mgr["id"] and set(m["agents"]) == {t.mgr["id"], t.onchain["id"], t.contract["id"], t.report["id"]}
    assert m["approvals"] and m["approvals"][0]["kind"] == "task_result"
    assert m["deliverable"]["report"] and m["deliverable"]["result_hash"] == m["proof"]["result_hash"]
    assert m["proof"]["state"] == M.AWAITING_REVIEW
    # nothing is on chain yet, so the ledger lists the mission as awaiting review, with every execution
    led = M.ledger(wf.db)
    assert led[0]["mission"] == goal["id"] and len(led[0]["executions"]) == 6 and led[0]["proof"]["state"] == M.AWAITING_REVIEW

    wf.decide(m["approvals"][0]["id"], True, "owner")
    m = M.mission(wf.db, wf.db.task(goal["id"]))
    assert m["stage"] == "completed" and m["proof"]["state"] == M.OFFCHAIN and not m["approvals"]
    wf.anchor_task(goal["id"], "owner")
    m = M.mission(wf.db, wf.db.task(goal["id"]))
    assert m["proof"]["state"] == M.ONCHAIN_PENDING and m["proof"]["tx_hash"] in fake.sent and m["proof"]["block"] is None
    fake.mine()
    wf.run_until_idle()
    m = M.mission(wf.db, wf.db.task(goal["id"]))
    assert m["proof"]["state"] == M.ONCHAIN_CONFIRMED and m["proof"]["block"] == 1001 and m["proof"]["chain_status"] == "confirmed"

    listed = M.missions(wf.db, workspace_id=t.ws["id"])
    assert [x["id"] for x in listed] == [goal["id"]] and listed[0]["proof"]["state"] == M.ONCHAIN_CONFIRMED
    assert M.missions(wf.db, agent=t.contract["id"]) and not M.missions(wf.db, agent="agt_nobody")
    assert not M.missions(wf.db, status=[D.FAILED])


def test_proof_states_for_failed_and_cancelled_missions(wf):
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"Analyze {EOA}", "owner")
    wf.cancel(g["id"], "owner")
    m = M.mission(wf.db, wf.db.task(g["id"]))
    assert m["stage"] == "cancelled" and m["proof"]["state"] == M.NOT_COMPLETED
    # a cancelled mission that never ran anything is not ledger material
    assert all(r["mission"] != g["id"] for r in M.ledger(wf.db))
    # an anchoring attempt that failed leaves the mission off-chain completed, with the error kept
    rec = {"id": 1, "status": "failed", "tx_hash": None, "block": None, "network": "bsc-testnet", "error": "boom", "confirmed_at": None}
    p = M.proof({**wf.db.task(g["id"]), "status": D.COMPLETED}, [rec])
    assert p["state"] == M.OFFCHAIN and p["error"] == "boom" and p["chain_status"] == "failed"


def test_assign_direct_runs_one_skill_on_one_agent_and_goes_to_review(wf):
    t = team(wf)
    goal = wf.assign_direct(t.contract["id"], "token_info", {"address": TOKEN}, "owner", priority=1)
    assert goal["kind"] == "goal" and goal["planner"] == "direct" and goal["assigned_agent"] == t.contract["id"] and goal["started_at"]
    subs = wf.db.subtasks(goal["id"])
    assert len(subs) == 1 and subs[0]["status"] == D.ASSIGNED and subs[0]["assigned_agent"] == t.contract["id"]
    views = {a["id"]: a for a in wf.agent_views()}
    assert views[t.contract["id"]]["live"] == "busy"
    wf.run_until_idle()

    goal = wf.db.task(goal["id"])
    assert goal["status"] == D.REVIEW and goal["result"]["output"]["symbol"] == "KGU"
    events = [m["event"] for m in wf.db.messages(root_id=goal["id"], limit=200)]
    for ev in ("user.instruction", "task.created", "plan.created", "task.assign", "task.assigned", "tool.start", "tool.done", "task.completed", "task.result", "goal.review"):
        assert ev in events, ev
    assert wf.db.approvals(task_id=goal["id"])[0]["kind"] == "task_result"
    views = {a["id"]: a for a in wf.agent_views()}
    assert views[t.contract["id"]]["last_activity_at"] and views[t.contract["id"]]["tasks_completed"] == 1
    assert views[t.contract["id"]]["live"] == "review" and views[t.contract["id"]]["current_task"]["status"] == D.REVIEW
    assert any("直接派活" in e["text"] for e in wf.store.events(("audit",)))


def test_assign_direct_refuses_what_the_agent_cannot_do(wf):
    t = team(wf)
    loner = wf.create_agent({"name": "Loner", "skills": ["token_info"], "permissions": ["chain:read"]}, "owner")
    cases = [
        (("agt_nope", "token_info", {"address": TOKEN}), {}, "没有员工"),
        ((t.contract["id"], "nope", {}), {}, "没有技能"),
        ((t.report["id"], "data_report", {}), {}, "不能单独派"),
        ((t.contract["id"], "wallet_analysis", {"address": TOKEN}), {}, "没有 钱包分析 技能|没有.*技能"),
        ((t.contract["id"], "token_info", {}), {}, "输入不对"),
        ((loner["id"], "token_info", {"address": TOKEN}), {}, "不在任何工作区"),
        ((t.contract["id"], "token_info", {"address": TOKEN}), {"workspace_id": "ws_other"}, "不在这个工作区"),
        ((t.contract["id"], "token_info", {"address": TOKEN}), {"priority": 9}, "优先级"),
        ((t.contract["id"], "token_info", {"address": TOKEN}), {"timeout_s": 1}, "超时"),
        ((t.contract["id"], "token_info", {"address": TOKEN}), {"budget_usd": "x"}, "格式"),
    ]
    for args, kw, msg in cases:
        with pytest.raises(WorkforceError, match=msg):
            wf.assign_direct(*args, "owner", **kw)
    wf.set_agent_status(t.contract["id"], "paused", "owner")
    with pytest.raises(WorkforceError, match="不在岗"):
        wf.assign_direct(t.contract["id"], "token_info", {"address": TOKEN}, "owner")


def test_status_counts_and_tasks_by_root(wf):
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"Analyze this BSC contract {TOKEN}", "owner")
    wf.run_until_idle()
    counts = wf.db.status_counts(kind="subtask")
    assert counts[D.COMPLETED] == 5 and counts[D.FAILED] == 1
    assert wf.db.status_counts(kind="goal", q="BSC contract") == {D.REVIEW: 1}
    assert wf.db.status_counts(agent=t.contract["id"]) == {D.COMPLETED: 3}
    grouped = wf.db.tasks_by_root([g["id"], "tsk_none"])
    assert len(grouped[g["id"]]) == 6 and grouped["tsk_none"] == [] and wf.db.tasks_by_root([]) == {}
