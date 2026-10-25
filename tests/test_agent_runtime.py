"""Agent Runtime: agents that reason with a model and call their own tools, against a scripted model.

The model is a stand-in (no API key in CI); the tools run against FakeChain, so every number the
"model" receives comes from a real tool run. These are mock tests: they prove the loop, limits,
permissions, accounting and the timeline, not the behaviour of a real model.
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agent.execution.rpc import RpcClient
from agent.store import Store
from agent.workforce import Workforce
from agent.workforce import db as D
from agent.workforce.chain import ChainReader
from agent.workforce.models import price_of
from agent.workforce.runtime import AnthropicProvider, Limits
from agent.workforce.skills import REGISTRY, Skill, SkillContext
from test_workforce import EOA, TOKEN, FakeChain


def text(t):
    return SimpleNamespace(type="text", text=t)


def call(name, inp, cid=None):
    return SimpleNamespace(type="tool_use", id=cid or f"tu_{name}_{abs(hash(json.dumps(inp))) % 10000}", name=name, input=inp)


class _Stream:
    def __init__(self, blocks, stop, usage, model, gate=None, owner=None):
        self.blocks, self.stop, self.usage, self.model, self.gate, self.owner = blocks, stop, usage, model, gate, owner

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self.owner is not None:
            self.owner.closed += 1
        return False

    def __iter__(self):
        for b in self.blocks:
            if b.type == "text":
                for piece in (b.text[: len(b.text) // 2], b.text[len(b.text) // 2:]):
                    if self.gate is not None:
                        assert self.gate.wait(10)
                    yield SimpleNamespace(type="text", text=piece)

    def get_final_message(self):
        stop = self.stop or ("tool_use" if any(b.type == "tool_use" for b in self.blocks) else "end_turn")
        return SimpleNamespace(content=self.blocks, stop_reason=stop, model=self.model,
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1], cache_read_input_tokens=0))


class ScriptedModel:
    """Stands in for anthropic.Anthropic(). Agent runs (stream) are routed by a key found in the task text;
    each route is a list of turns, and the turn played is the number of assistant turns already in the request."""

    def __init__(self, routes=None, plans=(), usage=(1000, 200), model="claude-opus-5-5"):
        self.routes = routes or {}
        self.plans = list(plans)
        self.usage = usage
        self.model = model
        self.requests: list[dict] = []
        self.plan_requests: list[dict] = []
        self.gate = None
        self.entered = threading.Event()
        self.closed = 0
        self.errors: list[Exception] = []
        self.options: dict = {}
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream, create=self.create))

    def __call__(self):
        return self

    def with_options(self, **kw):
        self.options = kw
        return self

    def stream(self, **kw):
        with self.lock:
            self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
            if self.errors:
                raise self.errors.pop(0)
        self.entered.set()
        first = kw["messages"][0]["content"]
        key = next(k for k in self.routes if k in first)
        n = sum(1 for m in kw["messages"] if m["role"] == "assistant")
        turn = self.routes[key][min(n, len(self.routes[key]) - 1)]
        blocks, stop = (turn, None) if isinstance(turn, list) else turn
        return _Stream(blocks, stop, self.usage, self.model, self.gate, self)

    def create(self, **kw):
        self.plan_requests.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[text(self.plans.pop(0))],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=100))


def make(llm, chain=None, **kw):
    chain = chain or FakeChain()
    reader = ChainReader(RpcClient("http://rpc", post=chain), "bsc-testnet", 97)
    wf = Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)}, llm=llm, max_workers=4, **kw)
    return wf, chain


def squad(wf, *agents, manager_provider="none"):
    mgr = wf.create_agent({"name": "Lead", "is_manager": True, "provider": manager_provider}, "owner")
    ws = wf.create_workspace({"name": "BSC", "manager_id": mgr["id"], "members": [a["id"] for a in agents]}, "owner")
    return mgr, ws


def analyst(wf, name="Analyst", skills=("wallet_analysis", "contract_analysis"), **extra):
    return wf.create_agent({"name": name, "provider": "anthropic", "skills": ["agent_task", *skills],
                            "permissions": ["chain:read", "model:invoke"], **extra}, "owner")


def evs(wf, root):
    return wf.db.messages(root_id=root, limit=2000)


# ---------------------------------------------------------------- the loop

def test_agent_reasons_calls_tools_reads_real_results_and_answers():
    llm = ScriptedModel(routes={"Assess": [
        [text("先看钱包。"), call("wallet_analysis", {"address": EOA}, "c1")],
        [call("contract_analysis", {"address": TOKEN}, "c2"), call("token_info", {"address": TOKEN}, "c3")],
        [text("结论：EOA 持有 2 BNB；代币合约可增发、可暂停。")],
    ]})
    wf, chain = make(llm)
    try:
        a = analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": f"Assess {EOA} and {TOKEN}"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.COMPLETED and wf.db.task(g["id"])["status"] == D.REVIEW
        out = sub["result"]
        assert out["answer"].startswith("结论") and out["model_calls"] == 3
        calls = {c["id"]: c for c in out["tool_calls"]}
        assert calls["c1"]["ok"] and calls["c2"]["ok"] and calls["c1"]["rpc_calls"] > 0
        assert not calls["c3"]["ok"] and "没有被授权" in calls["c3"]["error"]  # token_info is not one of its tools
        # what the model read back is the real tool output, and the denial came back as an error result
        second = llm.requests[1]["messages"][-1]["content"][0]
        assert second["tool_use_id"] == "c1" and '"bnb": "2"' in second["content"]
        third = {r["tool_use_id"]: r for r in llm.requests[2]["messages"][-1]["content"]}
        assert third["c3"]["is_error"] and "可增发" in third["c2"]["content"]
        # tools offered = the agent's own read tools; system prompt carries the runtime rules; key never in the request
        assert sorted(t["name"] for t in llm.requests[0]["tools"]) == ["contract_analysis", "wallet_analysis"]
        assert "You can only read" in llm.requests[0]["system"]
        # default model is Claude Haiku 5.5, which takes no refusal fallback
        assert llm.requests[0]["model"] == "claude-haiku-5-5" and "fallbacks" not in llm.requests[0] and llm.options == {"timeout": 120.0, "max_retries": 0}
        # every step is on the timeline, in order
        names = [m["event"] for m in evs(wf, g["id"])]
        for e in ("model.start", "agent.delta", "model.done", "tool.call", "tool.result", "tool.done", "task.completed"):
            assert e in names, e
        assert names.index("tool.call") < names.index("tool.result") < names.index("task.completed")
        # three calls at 1000/200 tokens on claude-opus-5-5 ($4 / $20 per Mtok) = 3 * $0.008
        assert Decimal(sub["cost_usd"]) == Decimal("0.024") and sub["tokens"] == 3600
        assert Decimal(wf.db.task(g["id"])["cost_usd"]) == Decimal("0.024")
        agent = wf.db.agent(a["id"])
        assert agent["tokens_in"] == 3000 and Decimal(agent["cost_usd"]) == Decimal("0.024")
        done = [m for m in evs(wf, g["id"]) if m["event"] == "tool.done"][0]
        assert done["data"]["rpc_calls"] == calls["c1"]["rpc_calls"] + calls["c2"]["rpc_calls"]
    finally:
        wf.stop()


def test_limits_stop_a_looping_agent():
    loop = [[call("wallet_analysis", {"address": EOA}, "x")]]  # never answers
    llm = ScriptedModel(routes={"Loop": loop})
    wf, _ = make(llm)
    try:
        a = analyst(wf, model_config={"max_model_calls": 4, "max_tool_calls": 2})
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Loop forever"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.FAILED and "最大模型调用次数 4" in sub["error"]
        assert len(llm.requests) == 4  # not retried: hitting a limit is not a blip
        results = [m for m in evs(wf, g["id"]) if m["event"] == "tool.result"]
        assert [r["data"]["ok"] for r in results] == [True, True, False, False]  # calls past the tool cap are refused
        assert wf.db.task(g["id"])["status"] == D.FAILED
    finally:
        wf.stop()


def test_mission_budget_stops_the_run():
    llm = ScriptedModel(routes={"Spend": [[call("wallet_analysis", {"address": EOA})]]}, usage=(100_000, 10_000))
    wf, _ = make(llm)
    try:
        a = analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Spend money"}, "owner", workspace_id=ws["id"], budget_usd="0.5")
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        # one call costs $0.6 on Opus 5.5 pricing: the next call is refused before it is made
        assert len(llm.requests) == 1 and sub["status"] == D.FAILED and "预算" in sub["error"]
    finally:
        wf.stop()


def test_agent_budget_and_missing_model_block_the_run():
    wf, _ = make(None)  # no model configured on the server
    a = analyst(wf)
    assert "没有配置可用的模型" in wf.can_run(a, "agent_task")
    wf.stop()
    llm = ScriptedModel(routes={"x": [[text("ok")]]})
    wf, _ = make(llm)
    b = wf.create_agent({"name": "NoPerm", "provider": "anthropic", "skills": ["agent_task"], "permissions": []}, "owner")
    assert "缺少权限" in wf.can_run(b, "agent_task")
    c = analyst(wf, name="Broke", budget_usd="0.01")
    wf.db.update_agent(c["id"], cost_usd="0.02")
    assert wf.over_budget(wf.db.agent(c["id"]))
    wf.stop()


def test_cancel_while_the_model_is_streaming_discards_the_run():
    llm = ScriptedModel(routes={"Slow": [[text("思考很久的一段话"), call("wallet_analysis", {"address": EOA})], [text("done")]]})
    llm.gate = threading.Event()
    wf, _ = make(llm)
    try:
        a = analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Slow job"}, "owner", workspace_id=ws["id"])
        wf.tick()
        assert llm.entered.wait(5)
        wf.cancel(g["id"], "owner")
        llm.gate.set()
        wf.drain()
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.CANCELLED and sub["result"] is None
        names = [m["event"] for m in evs(wf, g["id"])]
        assert "tool.cancelled" in names and "task.completed" not in names and "tool.call" not in names
        assert llm.closed == 1  # the stream was closed, not read to the end
    finally:
        llm.gate.set()
        wf.stop()


def test_refusal_fails_the_task_without_retry():
    llm = ScriptedModel(routes={"Bad": [([text("")], "refusal")]})
    wf, _ = make(llm)
    try:
        a = analyst(wf)
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Bad request"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.FAILED and "拒绝" in sub["error"] and len(llm.requests) == 1
    finally:
        wf.stop()


def test_provider_retries_transient_errors_then_gives_up():
    llm = ScriptedModel(routes={"R": [[text("fine")]]})
    llm.errors = [TimeoutError("slow"), ConnectionError("reset")]
    p = AnthropicProvider(llm, retries=2, backoff_s=0)
    turn = p.complete(model="claude-opus-5-5", system="s", messages=[{"role": "user", "content": "R"}], tools=[], limits=Limits(),
                      on_text=lambda t: None, should_stop=lambda: False, timeout_s=7)
    assert turn.text == "fine" and len(llm.requests) == 3 and llm.options["timeout"] == 7
    llm.errors = [TimeoutError("1"), TimeoutError("2"), TimeoutError("3")]
    with pytest.raises(TimeoutError):
        p.complete(model="m", system="s", messages=[{"role": "user", "content": "R"}], tools=[], limits=Limits(),
                   on_text=lambda t: None, should_stop=lambda: False, timeout_s=7)
    llm.errors = [ValueError("400 bad request")]
    n = len(llm.requests)
    with pytest.raises(ValueError):
        p.complete(model="m", system="s", messages=[{"role": "user", "content": "R"}], tools=[], limits=Limits(),
                   on_text=lambda t: None, should_stop=lambda: False, timeout_s=7)
    assert len(llm.requests) == n + 1


def test_tool_timeout_comes_back_to_the_model_as_an_error():
    class Slow(FakeChain):
        def eth_getCode(self, a, tag):
            time.sleep(0.3)
            return super().eth_getCode(a, tag)

    slow_tool = Skill("slow_probe", "慢探针", "Test", "reads code twice", {"type": "object", "required": ["address"], "properties": {"address": {"type": "string"}}},
                      {"type": "object"}, ("chain:read",), "1.0.0", lambda ctx, inp: {"a": ctx.need_chain().code(inp["address"]), "b": ctx.need_chain().code(inp["address"])},
                      timeout_s=0.2)
    REGISTRY.register(slow_tool)
    try:
        llm = ScriptedModel(routes={"Probe": [[call("slow_probe", {"address": TOKEN}, "s1")], [text("工具超时了，无法确认。")]]})
        wf, chain = make(llm, chain=Slow())
        a = analyst(wf, skills=("slow_probe",))
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Probe it"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        out = wf.db.subtasks(g["id"])[0]["result"]
        assert out["tool_calls"][0]["error"].startswith("工具超时") and chain.calls.count("eth_getCode") == 1
        assert out["answer"].startswith("工具超时")
        wf.stop()
    finally:
        REGISTRY.unregister("slow_probe")


# ---------------------------------------------------------------- tool registry

def test_a_registered_tool_is_usable_by_agents_without_engine_changes():
    def gas(ctx: SkillContext, inp: dict) -> dict:
        r = ctx.need_chain()
        return {"gas_price_wei": r.gas_price(), "block": r.block_number()}

    REGISTRY.register(Skill("gas_now", "当前 Gas", "Monitoring", "最新区块与 Gas 价格", {"type": "object", "properties": {}},
                            {"type": "object"}, ("chain:read",), "1.0.0", gas, timeout_s=5))
    try:
        llm = ScriptedModel(routes={"Gas": [[call("gas_now", {}, "g1")], [text("Gas 1 gwei")]]})
        wf, _ = make(llm)
        assert any(s["id"] == "gas_now" and s["installed"] for s in wf.skills_view())
        a = analyst(wf, skills=("gas_now",))
        _, ws = squad(wf, a)
        g = wf.assign_direct(a["id"], "agent_task", {"instruction": "Gas now?"}, "owner", workspace_id=ws["id"])
        wf.run_until_idle()
        out = wf.db.subtasks(g["id"])[0]["result"]
        assert out["tool_calls"][0]["ok"] and llm.requests[1]["messages"][-1]["content"][0]["content"] == json.dumps({"gas_price_wei": 10**9, "block": 1000})
        # disabling the tool on the Skills page takes it away from the agent on the next run
        wf.skill_action("gas_now", "disable", "owner")
        assert "gas_now" not in [t.id for t in wf.runtime.tools_for(wf.db.agent(a["id"]))]
        wf.stop()
    finally:
        REGISTRY.unregister("gas_now")


def test_registry_refuses_write_tools_and_unknown_permissions():
    base = dict(name="x", category="c", description="d", input_schema={"type": "object"}, output_schema={}, version="1", run=lambda c, i: {})
    with pytest.raises(ValueError, match="只读"):
        REGISTRY.register(Skill(id="send_bnb", permissions=("chain:read",), read_only=False, **base))
    with pytest.raises(ValueError, match="未知权限"):
        REGISTRY.register(Skill(id="sign_it", permissions=("wallet:sign",), **base))
    with pytest.raises(ValueError, match="已经注册"):
        REGISTRY.register(Skill(id="wallet_analysis", permissions=("chain:read",), **base))
    with pytest.raises(ValueError, match="小写"):
        REGISTRY.register(Skill(id="Bad Id", permissions=(), **base))


def test_prices_and_limits_from_agent_config():
    assert price_of("claude-opus-5-5", None) == (Decimal(4), Decimal(20))
    assert price_of("unknown-model", None) is None
    assert price_of("claude-opus-5-5", (Decimal(1), Decimal(2))) == (Decimal(1), Decimal(2))
    lim = Limits.for_agent({"model_config": {"max_tool_calls": 999, "max_tokens": 900, "effort": "high"}}, {"max_model_calls": 3})
    assert lim.max_tool_calls == 50 and lim.max_tokens == 900 and lim.effort == "high" and lim.max_model_calls == 3


# ---------------------------------------------------------------- multi-agent collaboration

def test_manager_plans_parallel_agents_that_share_results_with_a_summarizer():
    """Supervisor plans; two agents work in parallel with different tools; a third reads both results and concludes."""
    llm = ScriptedModel(routes={
        "wallet side": [[call("wallet_analysis", {"address": EOA}, "w1")], [text("钱包：2 BNB，外部账户。")]],
        "contract side": [[call("contract_analysis", {"address": TOKEN}, "k1")], [text("合约：可增发、可暂停，owner 存在。")]],
        "Combine": [[text("综合结论：钱包正常；代币合约权限集中，风险中等。")]],
    })
    wf, _ = make(llm)
    try:
        w = analyst(wf, name="Wallet Agent", skills=("wallet_analysis",))
        k = analyst(wf, name="Contract Agent", skills=("contract_analysis",), model="claude-sonnet-5-5")
        s = analyst(wf, name="Summarizer", skills=())
        mgr, ws = squad(wf, w, k, s, manager_provider="anthropic")
        llm.plans = [json.dumps({"steps": [
            {"key": "w", "title": "钱包", "skill": "agent_task", "agent_id": w["id"], "input_json": json.dumps({"instruction": f"Check the wallet side of {EOA}"}), "depends_on": []},
            {"key": "k", "title": "合约", "skill": "agent_task", "agent_id": k["id"], "input_json": json.dumps({"instruction": f"Check the contract side of {TOKEN}"}), "depends_on": []},
            {"key": "s", "title": "汇总", "skill": "agent_task", "agent_id": s["id"], "input_json": json.dumps({"instruction": "Combine both findings"}), "depends_on": ["w", "k"]},
        ]})]
        g = wf.submit_goal(ws["id"], f"Is {EOA} safe to hold {TOKEN}?", "owner")
        wf.run_until_idle()
        assert wf.db.task(g["id"])["status"] == D.REVIEW and wf.db.task(g["id"])["planner"] == "model"
        subs = {x["title"]: x for x in wf.db.subtasks(g["id"])}
        assert all(x["status"] == D.COMPLETED for x in subs.values())
        # each agent used its own model and its own tools
        by_key = {next(k for k in llm.routes if k in r["messages"][0]["content"]): r for r in llm.requests if len(r["messages"]) == 1}
        assert by_key["contract side"]["model"] == "claude-sonnet-5-5" and by_key["wallet side"]["model"] == "claude-haiku-5-5"
        assert by_key["contract side"]["fallbacks"] == "default" and "fallbacks" not in by_key["wallet side"]
        assert [t["name"] for t in by_key["wallet side"]["tools"]] == ["wallet_analysis"]
        assert "tools" not in by_key["Combine"]
        # the summarizer was handed both upstream answers (shared, authorized mission context)
        brief = by_key["Combine"]["messages"][0]["content"]
        assert "钱包：2 BNB" in brief and "合约：可增发" in brief
        assert subs["汇总"]["result"]["answer"].startswith("综合结论")
        # the summary started only after both first-level runs had finished
        t = {x["title"]: (x["started_at"], x["finished_at"]) for x in subs.values()}
        assert t["汇总"][0] >= max(t["钱包"][1], t["合约"][1])
        # costs land on each agent and on the mission
        assert Decimal(wf.db.task(g["id"])["cost_usd"]) > 0 and all(Decimal(wf.db.agent(x["id"])["cost_usd"]) > 0 for x in (w, k, s))
    finally:
        wf.stop()


def test_failed_agent_task_is_reassigned_to_a_colleague():
    llm = ScriptedModel(routes={"Look": [[text("看完了：余额 2 BNB")]]})
    llm.errors = [TimeoutError("t")] * 3  # the first agent's run fails on model timeouts (3 tries)
    wf, _ = make(llm)
    try:
        wf.runtime.providers["anthropic"].backoff_s = 0
        a1 = analyst(wf, name="First")
        a2 = analyst(wf, name="Second")
        mgr, ws = squad(wf, a1, a2, manager_provider="anthropic")
        llm.plans = [json.dumps({"steps": [{"key": "x", "title": "看钱包", "skill": "agent_task", "agent_id": a1["id"],
                                            "input_json": json.dumps({"instruction": f"Look at {EOA}"}), "depends_on": []}]})]
        sub_attempts = 2  # default max_attempts: retried once on the same agent, then reassigned
        llm.errors = [TimeoutError("t")] * (3 * sub_attempts)
        g = wf.submit_goal(ws["id"], f"check {EOA}", "owner")
        wf.run_until_idle()
        sub = wf.db.subtasks(g["id"])[0]
        assert sub["status"] == D.COMPLETED and sub["assigned_agent"] == a2["id"]
        assert any(m["event"] == "task.reassigned" for m in evs(wf, g["id"]))
    finally:
        wf.stop()
