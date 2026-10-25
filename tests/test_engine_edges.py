"""Workforce engine and store edge cases around planning, recovery, transactions and the tool registry."""

from __future__ import annotations

import pytest

from agent.workforce import db as D
from agent.workforce.planner import PlanError
from agent.workforce.skills import REGISTRY, SKILL_BY_ID, Skill, SkillContext, SkillError, data_report
from test_scheduler_p0 import make
from test_workforce import TOKEN, team


def goal(wf, t, text=f"分析合约 {TOKEN}"):
    return wf.submit_goal(t.ws["id"], text, "owner")


def test_atomic_nests_and_rolls_back():
    wf, _ = make()
    try:
        a = wf.create_agent({"name": "A"}, "owner")
        with pytest.raises(RuntimeError):
            with wf.db.atomic():
                with wf.db.atomic():
                    wf.db.update_agent(a["id"], role="changed")
                raise RuntimeError("boom")
        assert wf.db.agent(a["id"])["role"] == ""
        assert wf.db.update_agent(a["id"]) == wf.db.agent(a["id"])
        wf.db.add_agent_usage("agt_nope", "1", 1, 1)  # unknown agent: nothing to add to
    finally:
        wf.stop()


def test_claim_skips_ended_goals_and_planning_errors_fail_the_goal():
    wf, _ = make()
    try:
        t = team(wf)
        g = goal(wf, t)
        m = wf.db.goal_message(g["id"])
        wf.db.transition(g["id"], (D.PENDING,), D.CANCELLED)
        wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
        assert wf._claim_plan(m) is None and wf.db.message(m["id"])["status"] == D.PROCESSED
        g2 = goal(wf, t)
        m2 = wf.db.goal_message(g2["id"])
        wf.db.set_message_status(m2["id"], (D.QUEUED,), D.DELIVERED)
        wf._plan_claimed = lambda gid, mid: (_ for _ in ()).throw(KeyError("bad"))
        wf._plan(t.mgr, m2)
        assert wf.db.task(g2["id"])["status"] == D.FAILED and "规划出错" in wf.db.task(g2["id"])["error"]
    finally:
        wf.stop()


def test_plan_results_are_discarded_when_the_goal_ends_during_planning():
    wf, _ = make()
    try:
        t = team(wf)
        real = wf.planner.plan

        def cancel_then(result):
            def plan(*a, **kw):
                out = real(*a, **kw)
                for x in wf.db.open_goals():
                    wf.db.transition(x["id"], (D.RUNNING,), D.CANCELLED)
                if result == "error":
                    raise PlanError("model said no")
                return out
            return plan

        for mode in ("error", "plan"):
            wf.planner.plan = cancel_then(mode)
            g = goal(wf, t)
            m = wf.db.goal_message(g["id"])
            wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
            wf._plan(t.mgr, m)
            assert wf.db.subtasks(g["id"]) == [] and wf.db.task(g["id"])["status"] == D.CANCELLED
            assert any(x["event"] == "plan.discarded" for x in wf.db.messages(task_id=g["id"]))
        wf._plan_claimed(g["id"], t.mgr["id"])  # not running any more: nothing to do
    finally:
        wf.stop()


def test_recovery_reposts_a_lost_goal_message_and_drain_times_out():
    wf, _ = make()
    try:
        t = team(wf)
        g = goal(wf, t)
        m = wf.db.goal_message(g["id"])
        wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
        assert wf._claim_plan(m)
        wf.db._db.execute("DELETE FROM wf_messages WHERE id=?", (m["id"],))
        wf.instance = "eng_after_restart"
        assert wf.recover() >= 1
        assert wf.db.task(g["id"])["status"] == D.PENDING and wf.db.goal_message(g["id"])
        import time

        wf.tick = lambda: time.sleep(0.01) or 1
        with pytest.raises(TimeoutError):
            wf.run_until_idle(timeout_s=0.05)
    finally:
        wf.stop()


def test_registry_and_tools_registered_after_start():
    wf, _ = make()
    try:
        with pytest.raises(ValueError, match="object"):
            REGISTRY.register(Skill("edge_bad", "x", "x", "x", {"type": "string"}, {}, (), "1", lambda c, i: {}))
        with pytest.raises(ValueError, match="已经注册"):
            REGISTRY.register(SKILL_BY_ID["wallet_analysis"])
        with pytest.raises(SkillError, match="Agent Runtime"):
            SKILL_BY_ID["agent_task"].run(SkillContext(None), {})
        with pytest.raises(Exception, match="自主执行"):
            wf.run_skill("agent_task", {"instruction": "x"}, "owner")
        REGISTRY.register(Skill("edge_late", "Late", "x", "x", {"type": "object"}, {"type": "object"}, ("chain:read",), "1", lambda c, i: {}))
        try:
            assert wf.skill_state("edge_late") == {"installed": True, "enabled": True, "version": "1"}
        finally:
            REGISTRY.unregister("edge_late")
    finally:
        wf.stop()


def test_report_includes_agent_answers_and_their_tools():
    ctx = SkillContext(None, upstream=[{"skill": "agent_task", "agent_name": "Analyst",
                                        "result": {"answer": "结论：安全", "tool_calls": [{"tool": "wallet_analysis"}, {"tool": "wallet_analysis"}]}}])
    md = data_report(ctx, {})["report_md"]
    assert "结论：安全" in md and "调用了 2 次工具：wallet_analysis" in md
