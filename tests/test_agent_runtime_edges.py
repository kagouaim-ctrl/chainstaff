"""Agent Runtime edge cases: odd stop reasons, run limits, bad tool calls, failing tools. Scripted model, as in
test_agent_runtime.py: these prove the runtime's handling, not a real model's behaviour."""

from __future__ import annotations

import time

import pytest

from agent.workforce import db as D
from agent.workforce.chain import Cancelled
from agent.workforce.runtime import AnthropicProvider, Limits, RunState, _deadline, _timeout
from agent.workforce.skills import REGISTRY, Skill, SkillContext, SkillError, SkillTimeout
from test_agent_runtime import ScriptedModel, analyst, call, make, squad, text
from test_workforce import EOA


def run(llm, instruction, budget="1", wf_kw=None, **agent_kw):
    wf, _ = make(llm, **(wf_kw or {}))
    a = analyst(wf, **agent_kw)
    _, ws = squad(wf, a)
    g = wf.assign_direct(a["id"], "agent_task", {"instruction": instruction}, "owner", workspace_id=ws["id"], budget_usd=budget)
    wf.run_until_idle()
    return wf, wf.db.subtasks(g["id"])[0], g


def test_pause_turn_is_continued_and_truncation_is_reported():
    llm = ScriptedModel(routes={"Pause": [([text("still working")], "pause_turn"), [text("done: 1 BNB")]],
                                "Truncated": [([], "max_tokens")], "ToolCut": [([call("wallet_analysis", {"address": EOA})], "max_tokens")]})
    wf, sub, _ = run(llm, "Pause please")
    try:
        assert sub["status"] == D.COMPLETED and sub["result"]["answer"] == "done: 1 BNB" and sub["result"]["model_calls"] == 2
    finally:
        wf.stop()
    for key, msg in (("Truncated", "输出被截断，没有给出结论"), ("ToolCut now", "调用工具时输出被截断")):
        wf, sub, _ = run(llm, key)
        try:
            assert sub["status"] == D.FAILED and msg in sub["error"]
        finally:
            wf.stop()


def test_token_and_agent_budget_limits():
    loop = [[call("wallet_analysis", {"address": EOA})]]
    wf, sub, _ = run(ScriptedModel(routes={"Tokens": loop}), "Tokens", model_config={"max_run_tokens": 1000})
    try:
        assert sub["status"] == D.FAILED and "Token 上限 1000" in sub["error"]
    finally:
        wf.stop()
    wf, sub, _ = run(ScriptedModel(routes={"Budget": loop}), "Budget", budget_usd="0.001")
    try:
        assert sub["status"] == D.FAILED and "预算" in sub["error"]
    finally:
        wf.stop()


def test_bad_tool_calls_come_back_to_the_model_as_errors():
    def explode(ctx, inp):
        raise ValueError("node returned garbage")

    REGISTRY.register(Skill("edge_explode", "Explode", "Test", "always fails", {"type": "object"}, {"type": "object"}, ("chain:read",), "1", explode))
    try:
        llm = ScriptedModel(routes={"Odd": [
            [call("wallet_analysis", "not an object", "a"), call("wallet_analysis", {"address": "0x12"}, "b"),
             call("token_info", {"address": EOA}, "c"), call("edge_explode", {}, "d"), call("tx_history", {"address": EOA}, "e")],
            [text("nothing usable")]]})
        wf, sub, _ = run(llm, "Odd calls", wf_kw={"etherscan": lambda p: {"status": "1", "result": []}},
                         skills=("wallet_analysis", "token_info", "edge_explode", "tx_history"),
                         permissions=["chain:read", "model:invoke", "net:etherscan"])
        try:
            errs = {c["id"]: c["error"] for c in sub["result"]["tool_calls"]}
            assert "JSON 对象" in errs["a"] and "参数不对" in errs["b"] and "ERC-20" in errs["c"]
            assert errs["d"].startswith("ValueError") and errs["e"] is None
        finally:
            wf.stop()
    finally:
        REGISTRY.unregister("edge_explode")


def test_runtime_guards_used_outside_the_loop():
    llm = ScriptedModel()
    wf, _ = make(llm)
    try:
        plain = wf.create_agent({"name": "Plain"}, "owner")
        with pytest.raises(SkillError):
            wf.runtime.run(plain, {"input": {"instruction": "x"}}, SkillContext(None))
        with pytest.raises(SkillTimeout):
            wf.runtime._check_limits(RunState(), Limits(), None, plain, time.monotonic() - 1, SkillContext(None))
    finally:
        wf.stop()
    with pytest.raises(Cancelled):
        AnthropicProvider(llm).complete(model="m", system="", messages=[], tools=[], limits=Limits(), on_text=lambda t: None,
                                        should_stop=lambda: True, timeout_s=5)
    assert _deadline(None) is None and _timeout(30, None) == 30
