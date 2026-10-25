"""The agent runtime: one bounded run of an agent's model for one task, with every tool call checked on the server.

Providers here are hand-written scripts (autonomy_kit.Script): they return fixed turns, so each test pins one path
through the loop: refusals, truncation, provider outages and retries, budgets, limits, tool permissions, checkpoints,
cancellation and rejection."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from autonomy_kit import Kit, Script, turn

from agent.autonomy import constants as C
from agent.autonomy.agents import TEMPLATES
from agent.autonomy.db import iso, now_dt
from agent.autonomy.runtime import _plain
from agent.autonomy.tools import TOOLS, describe_endpoint, dumps
from agent.workforce.skills import SKILL_BY_ID


def make(tmp_path, *turns, env=None, http_get=None):
    p = Script(*turns)
    k = Kit(tmp_path, provider=p, env=env, http_get=http_get)
    ws = k.workspace(endpoints=[{"name": "api", "url": "https://api.example.com/health"}])
    a = k.agent(None, "Solo", ws, responsibility="你负责调查和修复这个项目的问题。",
                permissions=["delegate", "memory:write", "code:read", "code:write", "code:test", "code:propose", "net:http", "chain:read"])
    return k, p, ws, a


def run_one(k, ws, a, objective="调查这个问题", kind="investigate", **kw):
    t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective=objective, kind=kind, to_agent_id=a["id"], **kw)
    k.drain()
    return k.at.db.task(t["id"])


def test_templates_only_name_real_skills():
    for t in TEMPLATES:
        assert all(s in SKILL_BY_ID for s in t["skills"]), t["id"]
    assert {t.name for t in TOOLS} >= {"complete_task", "delegate_task", "run_tests", "submit_patch", "http_check"}


def test_a_plain_answer_completes_the_task(tmp_path):
    k, p, ws, a = make(tmp_path, turn(text="Nothing is wrong."))
    try:
        t = run_one(k, ws, a)
        assert t["status"] == C.T_COMPLETED and t["result"]["summary"] == "Nothing is wrong." and t["result"]["outcome"] == "resolved"
        req = p.requests[0]
        assert "Solo" in req["system"] and "repository buggy-service" in req["system"] and "complete_task" in req["tools"]
        assert "Task " in req["messages"][0]["content"] and "Budget for this task" in req["messages"][0]["content"]
        [run] = k.at.db.runs(task_id=t["id"])
        assert run["status"] == C.R_COMPLETED and run["model_calls"] == 1 and run["tokens_in"] == 100 and run["cost_usd"] != "0"
        assert t["cost_usd"] != "0" and t["tokens"] == 120
    finally:
        k.close()


def test_refusal_and_truncation_fail_the_task_and_notify(tmp_path):
    k, p, ws, a = make(tmp_path, turn(stop="refusal"), turn(stop="max_tokens"))
    try:
        t1 = run_one(k, ws, a, objective="one")
        t2 = run_one(k, ws, a, objective="two")
        assert t1["status"] == C.T_FAILED and "拒绝" in t1["error"]
        assert t2["status"] == C.T_FAILED and "截断" in t2["error"]
        assert {n["task_id"] for n in k.at.db.notifications()} >= {t1["id"], t2["id"]}
        assert k.at.db.activity(types=["task.failed"])[0]["needs_human"]
    finally:
        k.close()


def test_pause_turn_continues(tmp_path):
    k, p, ws, a = make(tmp_path, turn(text="thinking", stop="pause_turn"), turn(("complete_task", {"summary": "ok", "outcome": "no_issue"})))
    try:
        t = run_one(k, ws, a)
        assert t["status"] == C.T_COMPLETED and t["result"]["outcome"] == "no_issue" and len(p.requests) == 2
    finally:
        k.close()


def test_provider_outage_is_retried_then_fails(tmp_path):
    k, p, ws, a = make(tmp_path, ConnectionError("down"), ConnectionError("still down"))
    try:
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
        k.drain()
        t = k.at.db.task(t["id"])
        assert t["status"] == C.T_QUEUED and t["not_before"] and "模型调用失败" in t["error"]  # backing off
        k.at.db.update_task(t["id"], not_before=None)
        k.drain()
        t = k.at.db.task(t["id"])
        assert t["status"] == C.T_FAILED and t["attempts"] == 2
        steps = k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"])
        assert any(s["kind"] == "model" and not s["ok"] for s in steps)
    finally:
        k.close()


def test_missing_model_key_fails_with_a_reason(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        k.at.providers.clear()
        t = run_one(k, ws, a)
        assert t["status"] == C.T_FAILED and "没有配置 API Key" in t["error"]
        assert k.at.activate(a["id"], "user")["warnings"]
    finally:
        k.close()


def test_limits_on_model_and_tool_calls(tmp_path):
    loop = lambda req: turn(("recall", {"query": "x"}))  # noqa: E731
    k, p, ws, a = make(tmp_path, loop, loop, env={"KAGOU_RUN_MAX_MODEL_CALLS": "2", "KAGOU_RUN_MAX_TOOL_CALLS": "1"})
    try:
        t = run_one(k, ws, a)
        assert t["status"] == C.T_FAILED and "模型调用上限 2" in t["error"]
        tools = [s for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert tools[0]["ok"] and not tools[1]["ok"] and "工具调用上限" in tools[1]["output"]
    finally:
        k.close()


def test_tool_calls_are_checked_on_the_server(tmp_path):
    def revoke(req):
        k.at.db.update_agent(a["id"], permissions=["delegate"])
        return turn(("remember", {"text": "x"}))

    k, p, ws, a = make(tmp_path,
                       turn(("deploy_production", {}), ("recall", "not an object"), ("complete_task", {"summary": ""}), ("list_events", {"limit": "many"})),
                       revoke,
                       turn(("complete_task", {"summary": "done", "outcome": "weird"})),
                       turn(("complete_task", {"summary": "done", "outcome": "partial"})))
    try:
        t = run_one(k, ws, a)
        assert t["status"] == C.T_COMPLETED and t["result"]["outcome"] == "partial"
        outs = [s["output"] for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert "没有被授权使用工具 deploy_production" in outs[0] and "JSON 对象" in outs[1] and "summary 不能为空" in outs[2]
        assert "ValueError" in outs[3] and "已被收回" in outs[4] and "outcome 只能是" in outs[5]
    finally:
        k.close()


def test_budgets_stop_a_run(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        k.at.update_agent(a["id"], {"budget_usd": "0.01"}, "user")
        k.at.db.wdb.add_agent_usage(a["id"], "0.02", 1, 1)
        t = run_one(k, ws, a, objective="a")
        assert t["status"] == C.T_FAILED and "预算已用完" in t["error"]
        assert k.at.db.activity(types=["budget.exceeded"])[0]["needs_human"]
        k.at.update_agent(a["id"], {"budget_usd": "0"}, "user")
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="b", to_agent_id=a["id"])
        k.at.db.update_task(t["id"], cost_usd="1")
        k.drain()
        assert "任务预算" in k.at.db.task(t["id"])["error"]
        m = k.at.missions.create({"workspace_id": ws["id"], "name": "M", "goal": "g", "type": "continuous", "lead_agent_id": a["id"], "budget_usd": "1"}, "user")
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="c", to_agent_id=a["id"], mission_id=m["id"])
        k.at.db.update_mission(m["id"], cost_usd="2")
        k.drain()
        assert "Mission 预算" in k.at.db.task(t["id"])["error"]
    finally:
        k.close()


def test_deadline_and_cancellation(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="late", to_agent_id=a["id"])
        k.at.db.claim(t["id"], "wkr-test", 60)
        k.at.db.update_task(t["id"], deadline_at=iso(now_dt() - timedelta(seconds=1)))
        assert k.at.runner.run(k.at.db.task(t["id"]), "wkr-test") == C.R_FAILED
        assert "截止时间" in k.at.db.task(t["id"])["error"]
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="gone", to_agent_id=a["id"])
        k.at.db.claim(t["id"], "someone-else", 60)
        assert k.at.runner.run(k.at.db.task(t["id"]), "wkr-test") == C.R_CANCELLED  # the lease is not ours
        run = k.at.db.runs(task_id=t["id"])[0]
        assert run["status"] == C.R_CANCELLED
        k.at.wakeup.cancel(t["id"], "user")

        def cancel(req):
            k.at.wakeup.cancel(t2["id"], "user")
            return turn(("recall", {"query": "x"}))

        p.turns = [cancel]
        t2, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="cancel me", to_agent_id=a["id"])
        k.drain()
        assert k.at.db.task(t2["id"])["status"] == C.T_CANCELLED and k.at.db.runs(task_id=t2["id"])[0]["status"] == C.R_CANCELLED
    finally:
        k.close()


def test_paused_agent_hands_the_task_back(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
        k.at.db.claim(t["id"], "wkr-test", 60)
        k.at.set_agent_status(a["id"], "pause", "user")
        assert k.at.runner.run(k.at.db.task(t["id"]), "wkr-test") == C.R_CANCELLED
        t = k.at.db.task(t["id"])
        assert t["status"] == C.T_QUEUED and t["attempts"] == 0
        k.at.db.claim(t["id"], "wkr-test", 60)
        k.at.db.wdb.delete_agent(a["id"]) if hasattr(k.at.db.wdb, "delete_agent") else k.at.db._db.execute("DELETE FROM wf_agents WHERE id = ?", (a["id"],))
        assert k.at.runner.run(k.at.db.task(t["id"]), "wkr-test") == C.R_FAILED
        assert "已经不存在" in k.at.db.task(t["id"])["error"]
    finally:
        k.close()


def test_checkpoint_resume_after_a_crash(tmp_path):
    k, p, ws, a = make(tmp_path, turn(("remember", {"text": "the API failed at 10:00"})), ConnectionError("provider reset"))
    try:
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
        k.drain()
        # The run saved a checkpoint after its first turn, then failed transiently: back in the queue, checkpoint kept.
        t = k.at.db.task(t["id"])
        assert t["status"] == C.T_QUEUED and [m["role"] for m in t["checkpoint"]["messages"]] == ["user", "assistant", "user"]
        # Next attempt: a worker claims it and dies mid-run (no lease renewal).
        db = k.at.db
        db.update_task(t["id"], not_before=None)
        assert db.claim(t["id"], "wkr-dead", 60)
        db.add_run(task_id=t["id"], agent_id=a["id"], workspace_id=ws["id"], worker_id="wkr-dead", attempt=2)
        db.update_task(t["id"], lease_until=iso(now_dt() - timedelta(seconds=5)), max_attempts=3)
        assert k.worker.recover() == 1
        assert db.activity(types=["worker.recovered"])[0]["result"].endswith("从检查点继续")
        p.turns = [turn(("complete_task", {"summary": "resumed and done"}))]
        k.drain()
        t = db.task(t["id"])
        assert t["status"] == C.T_COMPLETED and t["result"]["summary"] == "resumed and done" and t["checkpoint"] is None
        msgs = p.requests[-1]["messages"]
        assert msgs[1]["content"][0]["name"] == "remember"  # the earlier tool call is replayed, not redone
        assert msgs[-1]["role"] == "user" and "interrupted" in msgs[-1]["content"][-1]["text"]
        assert [r["status"] for r in db.runs(task_id=t["id"])].count(C.R_INTERRUPTED) == 1
        assert len(db.wdb.search_memory([("agent", a["id"])], "", limit=10)) == 1
    finally:
        k.close()


def test_reject_and_delegate_paths(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        b = k.agent(None, "Helper", ws, responsibility="你负责调查问题，找出原因。", permissions=["memory:write"])
        p.turns = [turn(("reject_task", {"reason": "not mine"}))]
        t = run_one(k, ws, a)
        assert t["to_agent_id"] == b["id"] and t["status"] in (C.T_QUEUED, C.T_COMPLETED) and t["evidence"]["rejected_by"] == [a["id"]]
        # Delegating without waiting: the run completes; the child carries a follow-up back to the sender.
        p.turns = [turn(("delegate_task", {"objective": "调查原因 of the 500s", "kind": "investigate", "wait_for_result": False, "follow_up": True,
                                           "deadline_minutes": 5, "budget_usd": "0.2"}), ("reject_task", {"reason": "too late"})),
                   turn(("delegate_task", {"objective": "x", "kind": "nonsense"})),
                   turn(("complete_task", {"summary": "handed over"})),
                   turn(text="child done"), turn(text="reviewed")]
        t = run_one(k, ws, a, objective="first look")
        assert t["status"] == C.T_COMPLETED and len(t["result"]["delegated"]) == 1
        kid = k.at.db.task(t["result"]["delegated"][0])
        assert kid["to_agent_id"] == b["id"] and kid["follow_up"] == 1 and kid["budget_usd"] == "0.2"
        outs = [s["output"] for r in k.at.db.runs(task_id=t["id"]) for s in k.at.db.steps(r["id"]) if s["kind"] == "tool"]
        assert any("已经分配了子任务" in str(o) for o in outs) and any("交接没有成功" in str(o) for o in outs)
        assert k.at.db.tasks(kind="review")  # the result came back to the sender
    finally:
        k.close()


def test_waiting_on_children_that_already_finished(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        b = k.agent(None, "Helper", ws, responsibility="你负责调查问题，找出原因。", permissions=["memory:write"])
        create = k.at.wakeup.create_task

        def instant(**kw):  # the child finishes before the parent's run settles (another worker was faster)
            t, info = create(**kw)
            k.at.db.transition(t["id"], C.TASK_OPEN, C.T_COMPLETED, result={"summary": "fast"})
            return t, info

        p.turns = [turn(("delegate_task", {"objective": "调查原因", "kind": "investigate", "to_agent_id": b["id"]})),
                   turn(("complete_task", {"summary": "all done"}))]
        t, _ = create(workspace_id=ws["id"], objective="look", kind="investigate", to_agent_id=a["id"])
        k.at.wakeup.create_task = instant
        k.drain()
        t = k.at.db.task(t["id"])
        assert t["status"] == C.T_COMPLETED and t["result"]["summary"] == "all done" and t["waiting_on"] == []
        assert k.at.db.task(t["result"]["delegated"][0])["result"] == {"summary": "fast"}
    finally:
        k.close()


def test_collaboration_and_evidence_tools(tmp_path):
    calls = {"http": []}

    def http_get(url, timeout):
        calls["http"].append(url)
        if "boom" in url:
            raise TimeoutError("slow")
        return 200, 12.5, "ok"

    k, p, ws, a = make(tmp_path, env={"KAGOU_ALLOW_PRIVATE_HTTP": "1"}, http_get=http_get)
    try:
        k.at.update_agent(a["id"], {"notify_rules": {"min_severity": "high"}}, "user")
        k.log_error(ws)
        ev = k.at.db.events()[0]
        other = k.at.create_workspace({"name": "Other"}, "user")
        foreign, _ = k.at.events.ingest(workspace_id=other["id"], source="manual", type_="x.y", title="elsewhere")
        k.at.db.update_event(ev["id"], processing_status=C.E_PROCESSED)
        k.at.db.update_event(foreign["id"], processing_status=C.E_PROCESSED)
        p.turns = [turn(("get_event", {"event_id": ev["id"]}), ("get_event", {"event_id": foreign["id"]}), ("list_events", {"type": "app.error"}),
                        ("get_task", {"task_id": "job_nope"}), ("remember", {"text": "pricing breaks on empty windows", "scope": "workspace"}),
                        ("remember", {"text": ""}), ("remember", {"text": "x", "scope": "global"}), ("recall", {"query": "pricing"}),
                        ("notify_user", {"title": "low news", "severity": "low"}), ("notify_user", {"title": "bad", "severity": "loud"}),
                        ("notify_user", {"title": "big", "severity": "high"}), ("notify_user", {"title": "4th"}),
                        ("http_check", {"endpoint": "api"}), ("http_check", {"endpoint": "nope"}),
                        ("token_flows", {}), ("token_holders", {})),
                   lambda req: turn(("get_task", {"task_id": req["messages"][0]["content"].split()[1]}),
                                    ("complete_task", {"summary": "checked", "outcome": "needs_human"}))]
        t = run_one(k, ws, a)
        assert t["status"] == C.T_COMPLETED
        outs = [s["output"] for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert outs[0]["type"] == "app.error" and "没有这个事件" in outs[1] and outs[2]["events"] and "没有这个任务" in outs[3]
        assert outs[4]["ok"] and "text 不能为空" in outs[5] and "scope 只能是" in outs[6] and outs[7]["memories"][0]["text"].startswith("pricing")
        assert outs[8]["notification_id"] is None and "severity 只能是" in outs[9] and outs[10]["notification_id"] and "3 条" in outs[11]
        assert outs[12]["status"] == 200 and outs[12]["ok"] and "没有登记" in outs[13]
        assert outs[14]["flows"] == [] and outs[15]["tokens"] == []
        assert outs[16]["id"] == t["id"]
        assert any(n["title"].startswith("Solo 需要你处理") for n in k.at.db.notifications())
        assert k.at.http_check("https://boom.example.com")["ok"] is False
        k.at.watchers.env = {}
        with pytest.raises(Exception, match="内网"):
            k.at.http_check("http://127.0.0.1:9/")
    finally:
        k.close()


def test_code_tools_on_the_isolated_copy(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        p.turns = [turn(("repo_list", {}), ("repo_read", {"path": "pricing.py", "start": 1, "end": 3}), ("repo_search", {"pattern": "("}),
                        ("repo_search", {"pattern": "def quote"}), ("repo_read", {"path": "../etc/passwd"}), ("repo_write", {"path": ".git/config", "content": "x"}),
                        ("repo_write", {"path": "notes/new.md", "content": "hello"}), ("repo_replace", {"path": "pricing.py", "old": "def", "new": "fn"}),
                        ("repo_list", {"repo": "nope"}), ("submit_patch", {"title": ""}))]
        t = run_one(k, ws, a, objective="look around", kind="fix")
        outs = [s["output"] for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert "pricing.py" in outs[0]["files"] and outs[1]["from"] == 1 and "正则" in outs[2] and outs[3]["hits"][0]["path"] == "pricing.py"
        assert "不在仓库副本里" in outs[4] and ".git" in outs[5] and outs[6]["created"] and "必须正好 1 次" in outs[7]
        assert "没有仓库 nope" in outs[8]
        art = k.at.db.artifact(outs[9]["artifact_id"])
        assert art["meta"]["files"] == ["notes/new.md"] and art["meta"]["tests"] is None and art["title"].startswith("Fix:")
        assert (k.repo / "notes").exists() is False  # the original is untouched
    finally:
        k.close()


def test_skill_tools_run_through_the_skill_registry(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        k.at.update_agent(a["id"], {"skills": ["token_info"]}, "user")
        p.turns = [turn(("token_info", {"token": "0x" + "1" * 40}), ("token_info", {"bogus": 1}))]
        t = run_one(k, ws, a)
        assert "token_info" in p.requests[0]["tools"]
        outs = [s for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert len(outs) == 3 and not outs[1]["ok"]
        k.wf.set_skill_enabled("token_info", False, "user") if hasattr(k.wf, "set_skill_enabled") else k.wf.db.set_skill_state("token_info", True, False, "1")
        p.turns = [lambda req: turn(("token_info", {"token": "0x" + "1" * 40}))]
        t = run_one(k, ws, a, objective="again")
        assert "token_info" not in p.requests[-1]["tools"]
    finally:
        k.close()


def test_mission_permissions_narrow_the_tools(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        m = k.at.missions.create({"workspace_id": ws["id"], "name": "Read only", "goal": "g", "type": "continuous", "lead_agent_id": a["id"],
                                  "permissions": ["code:read"]}, "user")
        run_one(k, ws, a, mission_id=m["id"])
        tools = p.requests[-1]["tools"]
        assert "repo_read" in tools and "repo_write" not in tools and "complete_task" in tools and "delegate_task" not in tools
    finally:
        k.close()


def test_plain_blocks_and_helpers():
    o = lambda **kw: SimpleNamespace(**kw)  # noqa: E731
    assert _plain({"type": "text", "text": "a"}) == {"type": "text", "text": "a"}
    assert _plain(o(type="text", text="a")) == {"type": "text", "text": "a"}
    assert _plain(o(type="tool_use", id="1", name="n", input={}))["name"] == "n"
    assert _plain(o(type="thinking", thinking="t", signature="s"))["signature"] == "s"
    assert _plain(o(type="redacted_thinking", data="d"))["data"] == "d"
    assert _plain(o(type="server_tool_use", model_dump=lambda **kw: {"type": "server_tool_use"}))["type"] == "server_tool_use"
    assert _plain(o(type="odd")) == {"type": "odd"}
    assert dumps("x" * 50, 10).startswith("\"xxxxxxxxx") and describe_endpoint("https://a.b/c?key=1") == "https://a.b/c"


def test_memory_long_results_tool_errors_and_an_unexpected_crash(tmp_path):
    k, p, ws, a = make(tmp_path)
    try:
        k.at.db.wdb.remember("agent", a["id"], "上次 pricing.py 的折扣出过错")
        p.turns = [turn(("reject_task", {"reason": "  "}), ("submit_patch", {}), ("repo_write", {"path": "big.txt", "content": "x" * 9000}),
                        ("repo_read", {"path": "big.txt"})),
                   ValueError("provider returned garbage")]
        t = run_one(k, ws, a, kind="fix")
        assert "上次 pricing.py 的折扣出过错" in p.requests[0]["messages"][0]["content"]
        outs = [s["output"] for s in k.at.db.steps(k.at.db.runs(task_id=t["id"])[0]["id"]) if s["kind"] == "tool"]
        assert "reason 不能为空" in outs[0] and "没有任何改动" in outs[1]
        results = p.requests[1]["messages"][-1]["content"]
        assert "已截断" in results[-1]["content"]
        assert t["status"] in (C.T_QUEUED, C.T_FAILED) and "ValueError: provider returned garbage" in k.at.db.runs(task_id=t["id"])[0]["error"]
    finally:
        k.close()
