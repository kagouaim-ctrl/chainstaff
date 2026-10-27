"""Wake-up and delegation guards: depth, circular hand-offs, rate, deduplication, fix cap, budgets, mission state,
target checks, matching, manual assignment, rejection, cancel, retry and deadlines."""

from __future__ import annotations

from datetime import timedelta

import pytest

from autonomy_kit import Kit

from agent.autonomy import constants as C
from agent.autonomy.constants import Limits
from agent.autonomy.agents import AgentError, agent_state, clean_autonomy, match, relevance
from agent.autonomy.db import iso, now_dt
from agent.autonomy.wakeup import DelegationBlocked, TaskError, _evidence, _why_matched, dedupe_key


@pytest.fixture
def kit(tmp_path):
    k = Kit(tmp_path)
    yield k
    k.close()


def _ws_agents(kit):
    ws = kit.workspace()
    return ws, kit.trio(ws)


def test_create_task_validates_its_input(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    with pytest.raises(TaskError, match="不能为空"):
        w.create_task(workspace_id=ws["id"], objective=" ")
    with pytest.raises(TaskError, match="任务类型"):
        w.create_task(workspace_id=ws["id"], objective="x", kind="dance")
    with pytest.raises(TaskError, match="没有工作区"):
        w.create_task(workspace_id="ws_nope", objective="x")
    with pytest.raises(TaskError, match="没有 Mission"):
        w.create_task(workspace_id=ws["id"], objective="x", mission_id="msn_nope")
    other = kit.at.create_workspace({"name": "Other"}, "user")
    m = kit.at.db.missions(responsibility_of=a1["id"])[0]
    with pytest.raises(TaskError, match="不属于"):
        w.create_task(workspace_id=other["id"], objective="x", mission_id=m["id"])
    with pytest.raises(TaskError, match="没有权限 rocket"):
        w.create_task(workspace_id=ws["id"], objective="x", required=["rocket"])
    with pytest.raises(TaskError, match="预算格式"):
        w.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a2["id"], budget_usd="lots")
    with pytest.raises(TaskError, match="负数"):
        w.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a2["id"], budget_usd="-1")
    t, _ = w.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a2["id"], priority="high", evidence="see log", deadline_s=1)
    assert t["priority"] == 2 and t["evidence"] == {"note": "see log"} and t["budget_usd"] == "0.5"
    assert _evidence({"blob": "x" * 40000})["truncated"] is True and _evidence(None) == {}


def test_named_target_checks(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    legacy = kit.wf.create_agent({"name": "Old", "permissions": []}, "user")
    kit.at.db.update_agent(legacy["id"], workspace_ids=[ws["id"]])
    lonely = kit.agent(None, "Lonely", None, activate=False, responsibility="r", work_mode="on_demand")
    paused = kit.agent(None, "Sleepy", ws, responsibility="r")
    kit.at.set_agent_status(paused["id"], "pause", "user")
    cases = [("agt_nope", "没有 Agent"), (legacy["id"], "没有设置长期职责"), (lonely["id"], "没有这个工作区"), (paused["id"], "paused"),
             (a2["id"], "缺少权限")]
    for target, msg in cases:
        with pytest.raises(DelegationBlocked, match=msg):
            w.create_task(workspace_id=ws["id"], objective="fix it", kind="fix", to_agent_id=target, from_agent_id=a1["id"])
    blocked = kit.at.db.activity(types=["task.blocked"])
    assert len(blocked) == len(cases) and blocked[0]["data"]["guard"] == "target"


def test_circular_delegation_is_refused_with_the_chain(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    t1, _ = w.create_task(workspace_id=ws["id"], objective="look", kind="investigate", to_agent_id=a1["id"])
    t2, _ = w.create_task(workspace_id=ws["id"], objective="dig", kind="investigate", to_agent_id=a2["id"], from_agent_id=a1["id"], parent=t1)
    with pytest.raises(DelegationBlocked) as e:
        w.create_task(workspace_id=ws["id"], objective="back to you", kind="investigate", to_agent_id=a1["id"], from_agent_id=a2["id"], parent=t2)
    assert e.value.guard == "circular" and "Agent 1 → Agent 2 → Agent 1" in str(e.value)
    # Auto-matching never picks an agent already on the chain either.
    t3, info = w.create_task(workspace_id=ws["id"], objective="调查原因 again", kind="investigate", from_agent_id=a2["id"], parent=t2)
    assert t3["status"] == C.T_UNASSIGNED
    reasons = {c["name"]: c["reason"] for c in info["considered"]}
    assert "循环" in reasons["Agent 1"] and "循环" in reasons["Agent 2"] and "缺少" not in reasons["Agent 3"]


def test_depth_limit(tmp_path):
    k = Kit(tmp_path, env={"KAGOU_MAX_DELEGATION_DEPTH": "1"})
    try:
        ws, (a1, a2, a3) = _ws_agents(k)
        w = k.at.wakeup
        t1, _ = w.create_task(workspace_id=ws["id"], objective="a", to_agent_id=a1["id"])
        t2, _ = w.create_task(workspace_id=ws["id"], objective="b", to_agent_id=a2["id"], from_agent_id=a1["id"], parent=t1)
        with pytest.raises(DelegationBlocked) as e:
            w.create_task(workspace_id=ws["id"], objective="c", kind="fix", to_agent_id=a3["id"], from_agent_id=a2["id"], parent=t2)
        assert e.value.guard == "depth"
    finally:
        k.close()


def test_delegation_rate_limit(tmp_path):
    k = Kit(tmp_path, env={"KAGOU_MAX_DELEGATIONS_PER_HOUR": "2"})
    try:
        ws, (a1, a2, a3) = _ws_agents(k)
        for i in range(2):
            k.at.wakeup.create_task(workspace_id=ws["id"], objective=f"job {i}", to_agent_id=a2["id"], from_agent_id=a1["id"])
        with pytest.raises(DelegationBlocked) as e:
            k.at.wakeup.create_task(workspace_id=ws["id"], objective="job 3", to_agent_id=a2["id"], from_agent_id=a1["id"])
        assert e.value.guard == "rate"
    finally:
        k.close()


def test_open_work_is_deduplicated(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    t, info = w.create_task(workspace_id=ws["id"], objective="Check  the API", to_agent_id=a2["id"])
    again, info2 = w.create_task(workspace_id=ws["id"], objective="check the api", to_agent_id=a2["id"])
    assert again["id"] == t["id"] and info2["deduplicated"] and not info["deduplicated"]
    assert kit.at.db.activity(types=["task.deduplicated"])
    kit.at.db.transition(t["id"], C.TASK_OPEN, C.T_COMPLETED)
    fresh, _ = w.create_task(workspace_id=ws["id"], objective="check the api", to_agent_id=a2["id"])
    assert fresh["id"] != t["id"]  # finished work does not block new work
    assert dedupe_key("w", "fix", None, "A  b", None) == dedupe_key("w", "fix", None, "a b", None)


def test_fix_attempts_per_issue_are_capped(tmp_path):
    k = Kit(tmp_path, env={"KAGOU_MAX_FIXES_PER_ISSUE": "1"})
    try:
        ws, (a1, a2, a3) = _ws_agents(k)
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="fix one", kind="fix", to_agent_id=a3["id"], fingerprint="fp1")
        k.at.db.transition(t["id"], C.TASK_OPEN, C.T_FAILED)
        with pytest.raises(DelegationBlocked) as e:
            k.at.wakeup.create_task(workspace_id=ws["id"], objective="fix two", kind="fix", to_agent_id=a3["id"], fingerprint="fp1")
        assert e.value.guard == "fix_cap"
    finally:
        k.close()


def test_mission_state_and_budgets_stop_new_work(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    m = kit.at.missions.create({"workspace_id": ws["id"], "name": "Watch", "goal": "g", "type": "continuous", "lead_agent_id": a2["id"], "budget_usd": "1"}, "user")
    kit.at.db.update_mission(m["id"], cost_usd="1")
    with pytest.raises(DelegationBlocked, match="Mission 预算"):
        w.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a2["id"], mission_id=m["id"])
    kit.at.missions.set_status(m["id"], "pause", "user")
    with pytest.raises(DelegationBlocked) as e:
        w.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a2["id"], mission_id=m["id"])
    assert e.value.guard == "mission"
    parent, _ = w.create_task(workspace_id=ws["id"], objective="p", to_agent_id=a1["id"], budget_usd="0.1")
    kit.at.db.update_task(parent["id"], cost_usd="0.2")
    with pytest.raises(DelegationBlocked, match="上游任务"):
        w.create_task(workspace_id=ws["id"], objective="child", to_agent_id=a2["id"], from_agent_id=a1["id"], parent=kit.at.db.task(parent["id"]))
    # An agent's own cap on a single task's budget wins over a bigger ask.
    kit.at.update_agent(a2["id"], {"approval_rules": {"max_task_budget_usd": "0.05"}}, "user")
    t, _ = w.create_task(workspace_id=ws["id"], objective="cheap", to_agent_id=a2["id"], budget_usd="3")
    assert t["budget_usd"] == "0.05"


def test_matching_explains_every_candidate(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    other = kit.at.create_workspace({"name": "Other"}, "user")
    kit.agent(None, "Elsewhere", other, responsibility="修复代码", permissions=["code:write", "code:test", "code:propose"])
    t, info = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="修复 average_price 的除零", kind="fix", from_agent_id=a2["id"])
    assert t["to_agent_id"] == a3["id"] and t["status"] == C.T_QUEUED
    why = {c["name"]: c for c in info["considered"]}
    assert why["Agent 2"]["reason"].startswith("是发起方") and "缺少权限" in why["Agent 1"]["reason"] and "工作区" in why["Elsewhere"]["reason"]
    delegated = kit.at.db.activity(types=["task.delegated"])[0]
    assert "自动匹配：Agent 3" in delegated["reason"]
    assert _why_matched(a3, [], None) == "" and "指定" in _why_matched(a3, [], a3["id"])
    # No permission requirement and nothing in common: not a fit.
    best, considered = match(kit.at.db, workspace_id=ws["id"], kind="general", objective="zzz", required=(), exclude=set())
    assert best is None and all(c["reason"] for c in considered)
    # An employee from the old workforce has no standing responsibility and is never auto-picked.
    kit.at.db._db.execute("UPDATE wf_agents SET autonomous = 0 WHERE id = ?", (a1["id"],))
    _, considered = match(kit.at.db, workspace_id=ws["id"], kind="fix", objective="修复", required=(), exclude=set())
    assert "旧版员工" in next(c for c in considered if c["agent_id"] == a1["id"])["reason"]
    assert relevance(a2, "investigate", "调查原因") > relevance(a3, "investigate", "调查原因")


def test_unassigned_task_notifies_and_a_human_assigns_it(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="deploy to production", kind="general", required=["github:draft_pr"])
    assert t["status"] == C.T_UNASSIGNED and t["to_agent_id"] is None
    assert any(n["task_id"] == t["id"] for n in kit.at.db.notifications())
    w = kit.at.wakeup
    with pytest.raises(TaskError, match="不能接收"):
        w.assign(t["id"], "agt_nope", "user")
    with pytest.raises(TaskError, match="缺少权限"):
        w.assign(t["id"], a2["id"], "user")
    kit.at.update_agent(a2["id"], {"permissions": [*a2["permissions"], "github:draft_pr"]}, "user")
    out = w.assign(t["id"], a2["id"], "user")
    assert out["status"] == C.T_QUEUED and out["to_agent_id"] == a2["id"]
    with pytest.raises(TaskError, match="只能指派"):
        w.assign(t["id"], a2["id"], "user")
    t2, _ = w.create_task(workspace_id=ws["id"], objective="nobody", required=["github:draft_pr"], from_agent_id=a2["id"])
    kit.at.db.transition(t2["id"], C.TASK_OPEN, C.T_UNASSIGNED, to_agent_id=None)
    with pytest.raises(TaskError, match="循环"):
        w.assign(t2["id"], a2["id"], "user")
    other = kit.at.create_workspace({"name": "Other"}, "user")
    far = kit.agent(None, "Far", other, responsibility="r", permissions=["github:draft_pr"])
    with pytest.raises(TaskError, match="工作区"):
        w.assign(t2["id"], far["id"], "user")


def test_reject_reroutes_or_waits_for_a_human(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    other = kit.agent("investigator", "Agent 4", ws)
    t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="调查原因", kind="investigate", to_agent_id=a2["id"])
    kit.at.db.claim(t["id"], "wkr", 60)
    out = kit.at.wakeup.reject(kit.at.db.task(t["id"]), a2, "busy with something else")
    assert out["status"] == C.T_QUEUED and out["to_agent_id"] in (other["id"], a1["id"])
    assert out["evidence"]["rejected_by"] == [a2["id"]]
    kit.at.db.claim(t["id"], "wkr", 60)
    for agent in (kit.at.db.agent(out["to_agent_id"]),):
        out = kit.at.wakeup.reject(kit.at.db.task(t["id"]), agent, "not mine")
    while out["status"] == C.T_QUEUED:
        kit.at.db.claim(t["id"], "wkr", 60)
        out = kit.at.wakeup.reject(kit.at.db.task(t["id"]), kit.at.db.agent(out["to_agent_id"]), "not mine either")
    assert out["status"] == C.T_UNASSIGNED and len(out["evidence"]["rejections"]) >= 2
    assert kit.at.db.activity(types=["task.unassigned"])[0]["needs_human"]


def test_cancel_cascades_and_retry(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    root, _ = w.create_task(workspace_id=ws["id"], objective="root", to_agent_id=a1["id"])
    kid, _ = w.create_task(workspace_id=ws["id"], objective="kid", to_agent_id=a2["id"], from_agent_id=a1["id"], parent=root)
    out = w.cancel(root["id"], "user", "not needed")
    assert out["status"] == C.T_CANCELLED and kit.at.db.task(kid["id"])["status"] == C.T_CANCELLED
    with pytest.raises(TaskError, match="已经结束"):
        w.cancel(root["id"], "user")
    with pytest.raises(TaskError, match="没有任务"):
        w.cancel("job_nope", "user")
    again = w.retry(root["id"], "user")
    assert again["status"] == C.T_QUEUED and again["attempts"] == 0 and again["error"] is None
    with pytest.raises(TaskError, match="只能重试"):
        w.retry(root["id"], "user")
    lost, _ = w.create_task(workspace_id=ws["id"], objective="nobody", required=["github:draft_pr"])
    kit.at.db.transition(lost["id"], C.TASK_OPEN, C.T_FAILED)
    with pytest.raises(TaskError, match="先指派"):
        w.retry(lost["id"], "user")


def test_deadlines_fail_overdue_work_and_wake_the_parent(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    parent, _ = w.create_task(workspace_id=ws["id"], objective="parent", to_agent_id=a1["id"])
    kid, _ = w.create_task(workspace_id=ws["id"], objective="kid", to_agent_id=a2["id"], from_agent_id=a1["id"], parent=parent)
    kit.at.db.transition(parent["id"], C.TASK_OPEN, C.T_WAITING, waiting_on=[kid["id"]])
    kit.at.db.update_task(kid["id"], deadline_at=iso(now_dt() - timedelta(seconds=1)))
    assert w.enforce_deadlines() == 1
    assert kit.at.db.task(kid["id"])["status"] == C.T_FAILED and kit.at.db.task(parent["id"])["status"] == C.T_QUEUED
    assert kit.at.db.activity(types=["task.timeout"])[0]["needs_human"]


def test_follow_up_results_go_back_to_the_sender_once(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    w = kit.at.wakeup
    t, _ = w.create_task(workspace_id=ws["id"], objective="look at this", to_agent_id=a2["id"], from_agent_id=a1["id"], follow_up=True, kind="investigate")
    kit.at.db.transition(t["id"], C.TASK_OPEN, C.T_COMPLETED, result={"summary": "found it"})
    w.finished(kit.at.db.task(t["id"]))
    [rev] = kit.at.db.tasks(kind="review")
    assert rev["to_agent_id"] == a1["id"] and rev["evidence"]["result"] == {"summary": "found it"} and rev["follow_up"] == 0
    # With a parent that is not waiting on it, the result still goes back; a paused sender gets nothing.
    t2, _ = w.create_task(workspace_id=ws["id"], objective="second", to_agent_id=a3["id"], from_agent_id=a1["id"], follow_up=True, parent=rev)
    kit.at.db.transition(t2["id"], C.TASK_OPEN, C.T_FAILED, result="plain")
    kit.at.set_agent_status(a1["id"], "pause", "user")
    w.finished(kit.at.db.task(t2["id"]))
    assert len(kit.at.db.tasks(kind="review")) == 1
    kit.at.set_agent_status(a1["id"], "resume", "user")
    w.finished(kit.at.db.task(t2["id"]))
    assert len(kit.at.db.tasks(kind="review")) == 2


def test_one_time_mission_finishes_with_its_task(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    m = kit.at.missions.create({"workspace_id": ws["id"], "name": "Once", "goal": "do it once", "type": "one_time", "lead_agent_id": a2["id"]}, "user")
    [t] = kit.at.db.tasks(mission_id=m["id"])
    kit.at.db.transition(t["id"], C.TASK_OPEN, C.T_FAILED, error="boom")
    kit.at.wakeup.finished(kit.at.db.task(t["id"]))
    m = kit.at.db.mission(m["id"])
    assert m["status"] == C.M_FAILED and m["result"]["error"] == "boom"


def test_agent_state_machine(kit):
    ws, (a1, a2, a3) = _ws_agents(kit)
    db = kit.at.db
    st = lambda a, alive=True: agent_state(db, db.agent(a["id"]), worker_alive=alive, watched=set())["state"]  # noqa: E731
    assert st(a2) == C.A_WAITING and st(a2, alive=False) == C.A_OFFLINE
    t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="调查", kind="investigate", to_agent_id=a2["id"])
    assert st(a2) == C.A_QUEUED and "1 个任务在等" in agent_state(db, db.agent(a2["id"]), worker_alive=False)["detail"]
    db.claim(t["id"], "w", 60)
    assert st(a2) == C.A_INVESTIGATING
    db.transition(t["id"], C.TASK_OPEN, C.T_WAITING)
    assert st(a2) == C.A_WAITING
    db.transition(t["id"], C.TASK_OPEN, C.T_COMPLETED)
    r = db.add_run(task_id=t["id"], agent_id=a2["id"], workspace_id=ws["id"], worker_id="w", attempt=1)
    db.update_run(r["id"], status=C.R_FAILED, finished_at=iso(now_dt()), error="model down")
    assert st(a2) == C.A_ERROR
    kit.at.set_agent_status(a2["id"], "pause", "user")
    assert st(a2) == C.A_PAUSED
    legacy = kit.wf.create_agent({"name": "Old"}, "user")
    assert st(legacy) == C.A_IDLE
    sched = kit.agent(None, "Nightly", ws, responsibility="nightly check", work_mode="scheduled", schedule_cron="0 3 * * *")
    assert "下一次定时执行" in agent_state(db, db.agent(sched["id"]), worker_alive=True)["detail"]
    db.update_agent(sched["id"], schedule_cron="bad cron")
    assert agent_state(db, db.agent(sched["id"]), worker_alive=True)["state"] == C.A_WAITING
    plain = kit.agent(None, "Plain", ws, responsibility="r")
    assert "可以被事件" in agent_state(db, db.agent(plain["id"]), worker_alive=True)["detail"]
    db.wdb.update_agent(a3["id"], status="archived")
    assert st(a3) == C.A_ARCHIVED


def test_clean_autonomy_rules(kit):
    db = kit.at.db
    with pytest.raises(AgentError, match="工作职责"):
        clean_autonomy({}, db)
    with pytest.raises(AgentError, match="工作模式"):
        clean_autonomy({"responsibility": "r", "work_mode": "sometimes"}, db)
    with pytest.raises(AgentError, match="cron"):
        clean_autonomy({"responsibility": "r", "work_mode": "scheduled"}, db)
    with pytest.raises(AgentError, match="cron 表达式不对"):
        clean_autonomy({"responsibility": "r", "schedule_cron": "every day"}, db)
    with pytest.raises(AgentError, match="没有工作区"):
        clean_autonomy({"responsibility": "r", "workspace_ids": ["ws_x"]}, db)
    for bad in ({"approval_rules": ["x"]}, {"notify_rules": ["x"]}, {"notify_rules": {"min_severity": "loud"}}, {"wake_on": "app.error"},
                {"wake_on": ["App Error"]}, {"approval_rules": {"max_task_budget_usd": "x"}}, {"approval_rules": {"max_task_budget_usd": "-2"}}):
        with pytest.raises(AgentError):
            clean_autonomy({"responsibility": "r", **bad}, db)
    f = clean_autonomy({"responsibility": " r ", "approval_rules": {"delegate_outside_mission": 1, "max_task_budget_usd": ""},
                        "notify_rules": {"min_severity": "high"}, "wake_on": ["bsc.*", "bsc.*", "*"], "template": "nope"}, db)
    assert f["approval_rules"] == {"patch": "always", "delegate_outside_mission": True, "max_task_budget_usd": None}
    assert f["wake_on"] == ["bsc.*", "*"] and f["template"] == "" and f["responsibility"] == "r" and f["work_mode"] == "event"
    assert clean_autonomy({"wake_on": None}, db, partial=True) == {"wake_on": None}


def test_limits_read_from_the_environment_are_clamped():
    assert Limits({"KAGOU_MAX_DELEGATION_DEPTH": "not a number"}).max_depth == 4
    assert Limits({"KAGOU_MAX_DELEGATION_DEPTH": "99"}).max_depth == 10
    assert Limits({"KAGOU_MAX_DELEGATION_DEPTH": "0"}).max_depth == 1
