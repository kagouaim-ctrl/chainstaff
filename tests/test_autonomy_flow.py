"""The bug workflow end to end, in one process: an error log wakes Agent 1, which hands the investigation to Agent 2,
which hands the fix to Agent 3; Agent 3 changes an isolated copy, runs the tests and submits a patch; a human approves
it, KAGOU applies it to the repository, and Agent 1 verifies the problem is gone.

The model is the scripted brain in tests/support (a mock); everything around it is the real code: the queue, leases,
wake-ups, delegation guards, the sandbox, git, the test command, approvals and activity."""

from __future__ import annotations

import subprocess

import pytest

from autonomy_kit import Kit

from agent.autonomy import constants as C


@pytest.fixture
def kit(tmp_path):
    k = Kit(tmp_path)
    yield k
    k.close()


def _tests_pass(repo) -> bool:
    return subprocess.run(["python", "-m", "unittest", "-q"], cwd=repo, capture_output=True).returncode == 0


def test_bug_workflow_from_error_log_to_verified_fix(kit):
    ws = kit.workspace()
    a1, a2, a3 = kit.trio(ws)
    assert not _tests_pass(kit.repo)  # the example ships with the bug

    r = kit.log_error(ws)
    assert r["accepted"] and not r["deduplicated"]
    kit.drain()

    tasks = {t["to_agent_id"]: t for t in kit.at.db.tasks(limit=20)}
    t1, t2, t3 = tasks[a1["id"]], tasks[a2["id"]], tasks[a3["id"]]
    assert [t1["kind"], t2["kind"], t3["kind"]] == ["investigate", "investigate", "fix"]
    assert t1["from_agent_id"] is None and t2["from_agent_id"] == a1["id"] and t3["from_agent_id"] == a2["id"]
    assert t2["parent_id"] == t1["id"] and t3["parent_id"] == t2["id"] and t3["root_id"] == t1["id"] and t3["depth"] == 2
    assert t3["chain"] == [a1["id"], a2["id"]]
    assert all(t["status"] == C.T_COMPLETED for t in (t1, t2, t3))
    assert "pricing.average_price()" in t2["result"]["summary"] and t3["result"]["artifacts"]
    assert t3["result"]["tests"]["passed"] is True

    # The patch waits for a human; the original repository is untouched.
    assert not _tests_pass(kit.repo)
    [ap] = kit.at.db.approvals(status="pending")
    assert ap["kind"] == "patch" and ap["task_id"] == t3["id"] and ap["detail"]["tests_passed"] is True
    art = kit.at.db.artifact(ap["artifact_id"])
    assert "if not volume" in art["content"] and art["status"] == "pending_review"
    state = {r["id"]: r["state"] for r in kit.at.agent_rows()}
    assert state[a3["id"]] == C.A_WAITING_APPROVAL

    out = kit.at.decide(ap["id"], True, "user", "looks right")
    assert len(out["applied_commit"]) == 40 and out["approval"]["status"] == "approved"
    assert _tests_pass(kit.repo)
    kit.drain()

    verify = kit.at.db.tasks(kind="verify")[0]
    assert verify["to_agent_id"] == a1["id"] and verify["status"] == C.T_COMPLETED and "验证通过" in verify["result"]["summary"]
    assert verify["evidence"]["origin_task_id"] == t1["id"]

    types = [a["type"] for a in kit.at.db.activity(limit=500, newest_first=False)]
    for want in ("bug.detected", "mission.triggered", "task.created", "agent.awakened", "task.delegated", "fix.started", "tests.passed",
                 "patch.submitted", "approval.requested", "task.resumed", "investigation.completed", "approval.decided", "patch.applied"):
        assert want in types, want
    # Every run is recorded with its steps and cost.
    runs = kit.at.db.runs(limit=50)
    assert runs and all(r["status"] in (C.R_COMPLETED, C.R_WAITING) for r in runs)
    detail = kit.at.task_detail(t3["id"])
    assert [x["id"] for x in detail["tree"]][:3] == [t1["id"], t2["id"], t3["id"]] and detail["steps"] and detail["artifacts"]
    assert detail["names"][a3["id"]] == "Agent 3"


def test_same_error_again_is_folded_into_the_open_event(kit):
    ws = kit.workspace()
    kit.trio(ws)
    first = kit.log_error(ws)
    again = kit.log_error(ws, "ZeroDivisionError: division by zero")
    assert again["deduplicated"] and again["event_id"] == first["event_id"] and again["occurrences"] == 2
    kit.drain()
    assert kit.at.db.count_tasks(kind="investigate", agent_id=kit.at.db.agents()[0]["id"]) == 1


def test_rejected_patch_is_not_applied(kit):
    ws = kit.workspace()
    kit.trio(ws)
    kit.log_error(ws)
    kit.drain()
    [ap] = kit.at.db.approvals(status="pending")
    out = kit.at.decide(ap["id"], False, "user", "no")
    assert out["approval"]["status"] == "rejected" and kit.at.db.artifact(ap["artifact_id"])["status"] == "rejected"
    assert not _tests_pass(kit.repo) and not kit.at.db.tasks(kind="verify")
    with pytest.raises(ValueError, match="已经处理过"):
        kit.at.decide(ap["id"], True, "user")
    with pytest.raises(ValueError, match="没有审批"):
        kit.at.decide(999, True, "user")


def test_an_error_with_nobody_responsible_asks_a_human(kit):
    ws = kit.workspace()
    kit.log_error(ws)
    kit.drain()
    [ev] = kit.at.db.events()
    assert ev["processing_status"] == C.E_PROCESSED and not ev["task_ids"]
    acts = kit.at.db.activity(types=["event.ignored"])
    assert acts and acts[0]["needs_human"]
    assert any("没有 Agent 负责" in n["title"] for n in kit.at.db.notifications())


def test_one_error_wakes_one_responsible_agent_not_every_agent(kit):
    ws = kit.workspace()
    kit.agent("bug_hunter", "Hunter A", ws)
    kit.agent(None, "Generalist", ws, responsibility="你负责处理工作区里的各种问题和报错。", permissions=["delegate"], wake_on=["app.*"])
    kit.log_error(ws)
    kit.at.events.process_due()
    tasks = kit.at.db.tasks()
    assert len(tasks) == 1 and kit.at.name(tasks[0]["to_agent_id"]) == "Hunter A"  # exact type match wins over a wildcard
    routed = kit.at.db.activity(types=["event.routed"])[0]
    assert "Generalist" in routed["reason"]


def test_overview_and_views_read_the_database(kit):
    ws = kit.workspace()
    a1, a2, a3 = kit.trio(ws)
    o = kit.at.overview()
    k = o["kpis"]
    assert k["total_agents"] == 3 and k["pending_approvals"] == 0 and k["active_missions"] == 1 and k["runtime_health"] == "ok"
    assert k["watchers_total"] == 0 and k["active_watchers"] == 0
    rows = {r["name"]: r for r in o["agents"]}
    assert rows["Agent 2"]["state"] == C.A_WAITING and "交给它任务" in rows["Agent 2"]["state_detail"]
    kit.log_error(ws)
    rows = {r["name"]: r for r in kit.at.agent_rows()}
    assert rows["Agent 1"]["state"] == C.A_WATCHING  # a healthy app_logs watcher covers its responsibility
    kit.at.events.process_due()
    rows = {r["name"]: r for r in kit.at.agent_rows()}
    assert rows["Agent 1"]["state"] == C.A_QUEUED
    assert kit.at.overview()["kpis"]["active_investigations"] == 1
    d = kit.at.agent_detail(a1["id"])
    assert d["open_tasks"] and d["missions"] and d["task_counts"] == {"queued": 1}
    h = kit.at.runtime_health()
    assert h["worker_alive"] and h["sandbox"]["network_isolated"] is False and h["github_token"] is False
    assert kit.at.model_status()["configured"]
