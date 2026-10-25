"""Agents on an OpenAI-compatible API (Gemini free tier, Groq, OpenRouter), against a scripted HTTP endpoint.

Mock tests: the endpoint is a stand-in that answers in the chat completions shape; the tools run against FakeChain.
They prove the message conversion, the tool loop, retries, planning and pricing, not a real model's behaviour.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agent.workforce import build_workforce
from agent.workforce import db as D
from agent.workforce.models import CHEAPEST_MODEL, anthropic_extras, cost_of, price_of
from agent.workforce.openai_compat import CompatError, OpenAICompatProvider, from_env, to_chat, to_functions
from agent.workforce.runtime import Limits
from test_agent_runtime import evs, make, squad
from test_workforce import EOA, TOKEN

URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def reply(text=None, calls=(), finish=None, usage=(800, 100), model="gemini-2.5-flash"):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [{"id": cid, "type": "function", "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                             for cid, n, a in calls]
    return 200, {"model": model, "choices": [{"message": msg, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
                 "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


class Endpoint:
    """Routes an agent run by a key in its first user message; the turn played is the number of assistant turns so far.
    Plan calls (no tools, a schema in the system prompt) take the next entry of `plans`. `fail` is played first."""

    def __init__(self, routes=None, plans=(), fail=()):
        self.routes = routes or {}
        self.plans = list(plans)
        self.fail = list(fail)
        self.calls: list[tuple[str, dict, dict, float]] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(json.dumps(body)), timeout))
        if self.fail:
            f = self.fail.pop(0)
            if isinstance(f, Exception):
                raise f
            return f
        if "JSON Schema" in body["messages"][0]["content"]:
            return reply(self.plans.pop(0), usage=(500, 120))
        first = next(m["content"] for m in body["messages"] if m["role"] == "user")
        key = next(k for k in self.routes if k in first)
        n = sum(1 for m in body["messages"] if m["role"] == "assistant")
        return self.routes[key][min(n, len(self.routes[key]) - 1)]


def provider(ep, price=(Decimal(0), Decimal(0)), **kw):
    return OpenAICompatProvider(URL, "sk-test", "gemini-2.5-flash", price, post=ep, backoff_s=0, **kw)


def compat_analyst(wf, name="Analyst", skills=("wallet_analysis", "contract_analysis"), **extra):
    return wf.create_agent({"name": name, "provider": "openai_compat", "skills": ["agent_task", *skills],
                            "permissions": ["chain:read", "model:invoke"], **extra}, "owner")


# ---------------------------------------------------------------- the agent loop

def test_agent_on_a_free_tier_calls_tools_and_answers():
    ep = Endpoint(routes={"Assess": [
        reply("先看钱包。", [("c1", "wallet_analysis", {"address": EOA})]),
        reply(None, [("c2", "contract_analysis", {"address": TOKEN}), ("c3", "token_info", {"address": TOKEN})]),
        reply("结论：EOA 持有 2 BNB；代币合约可增发。"),
    ]})
    wf, _ = make(None, compat=provider(ep))
    try:
        a = compat_analyst(wf)
        assert a["model"] == "gemini-2.5-flash"
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": f"Assess {EOA} and {TOKEN}"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.COMPLETED and wf.db.task(g["id"])["status"] == D.REVIEW
        out = sub["result"]
        assert out["answer"].startswith("结论") and out["model_calls"] == 3
        calls = {c["id"]: c for c in out["tool_calls"]}
        assert calls["c1"]["ok"] and calls["c2"]["ok"] and not calls["c3"]["ok"]
        # the request: chat completions URL, key in the header only, the agent's own tools as functions
        url, headers, body, _ = ep.calls[0]
        assert url == URL + "/chat/completions" and headers["Authorization"] == "Bearer sk-test"
        assert "sk-test" not in json.dumps(body)
        assert "Never present an inference as an on-chain fact" in body["messages"][0]["content"]
        assert body["messages"][0]["role"] == "system" and sorted(f["function"]["name"] for f in body["tools"]) == ["contract_analysis", "wallet_analysis"]
        # the second request carries the assistant's call and the real tool output back as a tool message
        second = ep.calls[1][2]["messages"]
        assert second[-2]["tool_calls"][0]["id"] == "c1" and second[-1] == {**second[-1], "role": "tool", "tool_call_id": "c1"}
        assert '"bnb": "2"' in second[-1]["content"]
        third = {m["tool_call_id"]: m["content"] for m in ep.calls[2][2]["messages"] if m["role"] == "tool"}
        assert third["c3"].startswith("ERROR: ") and "可增发" in third["c2"]
        # free tier: tokens counted from the returned usage, cost exactly 0, and reported as priced
        assert sub["tokens"] == 3 * 900 and Decimal(sub["cost_usd"]) == 0
        done = [m for m in evs(wf, g["id"]) if m["event"] == "model.done"]
        assert len(done) == 3 and all(d["data"]["priced"] and d["data"]["provider"] == "openai_compat" for d in done)
        assert wf.overview()["engine"]["compat"] == "gemini-2.5-flash"
    finally:
        wf.stop()


def test_paid_compat_price_is_charged_and_unknown_price_is_flagged():
    ep = Endpoint(routes={"Look": [reply("看完了", usage=(1_000_000, 0))]})
    wf, _ = make(None, compat=provider(ep, price=(Decimal("0.3"), Decimal("2.5"))))
    try:
        a = compat_analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Look"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        assert Decimal(wf.db.subtasks(g["id"])[0]["cost_usd"]) == Decimal("0.3")
    finally:
        wf.stop()
    ep = Endpoint(routes={"Look": [reply("看完了")]})
    wf, _ = make(None, compat=provider(ep, price=None))
    try:
        a = compat_analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Look"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        assert Decimal(wf.db.subtasks(g["id"])[0]["cost_usd"]) == 0
        done = [m for m in evs(wf, g["id"]) if m["event"] == "model.done"][0]
        assert done["data"]["priced"] is False  # no price set: not a made-up figure, and marked as such
    finally:
        wf.stop()


def test_bad_arguments_and_truncation():
    ep = Endpoint(routes={"Try": [
        reply(None, [("b1", "wallet_analysis", "{not json")]),
        reply(None, [("", "wallet_analysis", {"address": EOA})]),
        reply("答案", finish="length"),
    ]})
    wf, _ = make(None, compat=provider(ep))
    try:
        a = compat_analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Try"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        tool_msgs = [m for m in ep.calls[1][2]["messages"] if m["role"] == "tool"]
        assert tool_msgs[0]["content"].startswith("ERROR: ")  # malformed arguments go back to the model as an error
        assert ep.calls[2][2]["messages"][-2]["tool_calls"][0]["id"].startswith("call_")  # a missing call id gets one
        assert sub["status"] == D.COMPLETED and sub["result"]["truncated"] is True  # cut off, kept, and marked as cut off
    finally:
        wf.stop()


def test_retries_rate_limits_and_server_errors_then_gives_up():
    lim = Limits()
    ep = Endpoint(routes={"x": [reply("ok")]}, fail=[(429, {"error": {"message": "quota"}}), CompatError("连接失败", retryable=True)])
    turn = provider(ep).complete(model="", system="s", messages=[{"role": "user", "content": "x"}], tools=[], limits=lim,
                                 on_text=lambda t: None, should_stop=lambda: False, timeout_s=5)
    assert turn.text == "ok" and len(ep.calls) == 3 and ep.calls[0][2]["model"] == "gemini-2.5-flash"
    ep = Endpoint(fail=[(503, {"error": "busy"})] * 3)
    with pytest.raises(CompatError, match="HTTP 503：busy"):
        provider(ep).complete(model="m", system="s", messages=[], tools=[], limits=lim, on_text=print, should_stop=lambda: False, timeout_s=5)
    assert len(ep.calls) == 3
    ep = Endpoint(fail=[(401, {"error": {"message": "API key not valid"}})])
    with pytest.raises(CompatError, match="HTTP 401") as e:
        provider(ep).complete(model="m", system="s", messages=[], tools=[], limits=lim, on_text=print, should_stop=lambda: False, timeout_s=5)
    assert not e.value.retryable and len(ep.calls) == 1  # a bad key is not retried
    ep = Endpoint(fail=[(200, ["not", "a", "dict"])])
    with pytest.raises(CompatError, match="HTTP 200"):
        provider(ep).complete(model="m", system="s", messages=[], tools=[], limits=lim, on_text=print, should_stop=lambda: False, timeout_s=5)
    from agent.workforce.chain import Cancelled

    with pytest.raises(Cancelled):
        provider(Endpoint()).complete(model="m", system="s", messages=[], tools=[], limits=lim, on_text=print, should_stop=lambda: True, timeout_s=5)


def test_failed_compat_call_fails_the_task_with_the_reason():
    ep = Endpoint(fail=[(400, {"error": {"message": "model does not support tools"}})] * 5)
    wf, _ = make(None, compat=provider(ep, retries=0))
    try:
        a = compat_analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Look"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.FAILED and "does not support tools" in sub["error"]
    finally:
        wf.stop()


# ---------------------------------------------------------------- planning

def test_compat_manager_plans_a_mission():
    ep = Endpoint(routes={"wallet": [reply("钱包：2 BNB")], "contract": [reply("合约：可增发")]})
    wf, _ = make(None, compat=provider(ep))
    try:
        w = compat_analyst(wf, name="W", skills=("wallet_analysis",))
        k = compat_analyst(wf, name="K", skills=("contract_analysis",))
        _, ws = squad(wf, w, k, manager_provider="openai_compat")
        ep.plans = ["```json\n" + json.dumps({"steps": [
            {"key": "w", "title": "钱包", "skill": "agent_task", "agent_id": w["id"], "input_json": json.dumps({"instruction": f"Check the wallet {EOA}"}), "depends_on": []},
            {"key": "k", "title": "合约", "skill": "agent_task", "agent_id": k["id"], "input_json": json.dumps({"instruction": f"Check the contract {TOKEN}"}), "depends_on": []},
        ]}) + "\n```"]
        pre = wf.preflight(ws["id"], text=f"Summarize the risk of {EOA} holding {TOKEN}")
        assert pre["ok"] and pre["planner"] == "model", pre
        g = wf.submit_goal(ws["id"], f"Summarize the risk of {EOA} holding {TOKEN}", "owner")
        wf.run_until_idle()
        goal = wf.db.task(g["id"])
        assert goal["status"] == D.REVIEW and goal["planner"] == "model"
        plan_call = next(c for c in ep.calls if "JSON Schema" in c[2]["messages"][0]["content"])
        assert "tools" not in plan_call[2] and plan_call[2]["messages"][1]["content"].count("Summarize the risk") == 1
    finally:
        wf.stop()


def test_compat_plan_errors_fall_back_or_explain():
    p = provider(Endpoint(fail=[reply("{}", finish="length")]))
    with pytest.raises(CompatError, match="截断"):
        p.json_call(model=None, system="s", user="u", schema=None, max_tokens=10, timeout_s=5)
    p = provider(Endpoint(fail=[reply("", finish="content_filter")]))
    with pytest.raises(CompatError, match="拒绝"):
        p.json_call(model=None, system="s", user="u", schema=None, max_tokens=10, timeout_s=5)
    ep = Endpoint(fail=[(400, {"error": {"message": "bad"}})])
    wf, _ = make(None, compat=provider(ep, retries=0))
    try:
        w = compat_analyst(wf, name="W", skills=("wallet_analysis",))
        _, ws = squad(wf, w, manager_provider="openai_compat")
        # a goal with an address: the rule planner takes over and says why
        g = wf.submit_goal(ws["id"], f"Check {EOA}", "owner")
        wf.run_until_idle()
        goal = wf.db.task(g["id"])
        assert goal["planner"] == "rules"
        assert any("HTTP 400" in m["text"] for m in evs(wf, g["id"]) if m.get("text"))
    finally:
        wf.stop()


def test_compat_manager_cancelled_after_the_call(monkeypatch):
    from agent.workforce.planner import PlanCancelled

    wf, _ = make(None, compat=provider(Endpoint(plans=['{"steps": []}'])))
    try:
        mgr = wf.create_agent({"name": "M", "is_manager": True, "provider": "openai_compat"}, "owner")
        stop = iter([False, True])
        with pytest.raises(PlanCancelled):
            wf.planner._call(mgr, "JSON Schema follows", "u", {"type": "object"}, 100, cancelled=lambda: next(stop))
    finally:
        wf.stop()


# ---------------------------------------------------------------- configuration

def test_preflight_warns_when_compat_is_not_configured():
    wf, _ = make(None)
    try:
        a = compat_analyst(wf)
        assert a["model"] in (None, "")
        _, ws = squad(wf, a, manager_provider="openai_compat")
        pre = wf.preflight(ws["id"], text=f"Check {EOA}")
        assert any("KAGOU_OPENAI_COMPAT" in c["text"] for c in pre["checks"] if c["level"] == "warn")
        assert wf.overview()["engine"]["compat"] is None
    finally:
        wf.stop()


def test_from_env():
    assert from_env({}) is None
    assert from_env({"KAGOU_OPENAI_COMPAT_URL": URL, "KAGOU_OPENAI_COMPAT_KEY": "k"}) is None
    p = from_env({"KAGOU_OPENAI_COMPAT_URL": URL + "/", "KAGOU_OPENAI_COMPAT_KEY": "k", "KAGOU_OPENAI_COMPAT_MODEL": "llama-3.3-70b"})
    assert p.url == URL + "/chat/completions" and p.price is None and p.host == "generativelanguage.googleapis.com"
    p = from_env({"KAGOU_OPENAI_COMPAT_URL": URL, "KAGOU_OPENAI_COMPAT_KEY": "k", "KAGOU_OPENAI_COMPAT_MODEL": "m", "KAGOU_OPENAI_COMPAT_PRICE": "0, 0"})
    assert p.price == (Decimal(0), Decimal(0))
    with pytest.raises(ValueError):
        OpenAICompatProvider(URL, "", "m")
    wf = build_workforce(__import__("agent.store", fromlist=["Store"]).Store(), env={
        "KAGOU_OPENAI_COMPAT_URL": URL, "KAGOU_OPENAI_COMPAT_KEY": "k", "KAGOU_OPENAI_COMPAT_MODEL": "m"}, compat_post=Endpoint())
    try:
        assert wf.compat.model == "m" and "openai_compat" in wf.runtime.providers and "anthropic" not in wf.runtime.providers
    finally:
        wf.stop()


def test_message_conversion():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "tool_use", "id": "t", "name": "f", "input": {"x": 1}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [{"k": "v"}]}, {"type": "text", "text": "more"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    out = to_chat("sys", msgs)
    assert out[0] == {"role": "system", "content": "sys"} and out[1] == {"role": "user", "content": "hi"}
    assert out[2]["tool_calls"][0]["function"] == {"name": "f", "arguments": '{"x": 1}'}
    assert out[3] == {"role": "tool", "tool_call_id": "t", "content": '[{"k": "v"}]'} and out[4] == {"role": "user", "content": "more"}
    assert out[5] == {"role": "assistant", "content": "done"}
    assert to_functions([{"name": "f", "input_schema": {"type": "object"}}])[0]["function"] == {"name": "f", "description": "", "parameters": {"type": "object"}}


def test_model_prices_and_options():
    assert CHEAPEST_MODEL == "claude-haiku-5-5"
    assert price_of("claude-haiku-5-5", None) == (Decimal("0.1"), Decimal("0.5"))
    assert price_of("claude-haiku-5-5", None, 150_000) == (Decimal("0.5"), Decimal("2.5"))  # long-prompt rate card
    assert price_of("claude-sonnet-5-5", None, 150_000) == (Decimal(2), Decimal(10))
    assert price_of("claude-opus-5-5-20260901", None) == (Decimal(4), Decimal(20))
    assert price_of("unknown", None) is None and cost_of("unknown", 10, 10, None) is None
    assert price_of("unknown", (Decimal(1), Decimal(2))) == (Decimal(1), Decimal(2))
    assert cost_of("claude-haiku-5-5", 100_000, 1_000_000, None) == Decimal("0.51")
    assert cost_of("claude-haiku-5-5", 1_000_000, 1_000_000, None) == Decimal("3.0")
    assert anthropic_extras("claude-haiku-5-5") == {}
    assert anthropic_extras("claude-opus-5-5")["fallbacks"] == "default"
