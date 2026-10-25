"""The AI Model Gateway and the AI_* configuration: budgets, ceilings, concurrency, the circuit breaker, Qwen pricing.

Mock tests: models are scripted (a fake chat completions endpoint, a scripted Anthropic client); tools run against FakeChain.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest

from agent.store import Store
from agent.workforce import build_workforce
from agent.workforce import db as D
from agent.workforce.gateway import Gateway
from agent.workforce.models import qwen_price
from agent.workforce.openai_compat import from_env
from agent.workforce.runtime import Limits
from test_agent_runtime import ScriptedModel, analyst, evs, make, squad, text
from test_openai_compat import Endpoint, compat_analyst, provider, reply
from test_workforce import EOA

QWEN_SG = "https://ws-123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def run_one(wf, a, instruction="Look"):
    _, ws = squad(wf, a)
    g = wf.assign_direct(a["id"], "agent_task", {"instruction": instruction}, "owner", workspace_id=ws["id"])
    wf.run_until_idle()
    return g, wf.db.subtasks(g["id"])[0]


# ---------------------------------------------------------------- budgets

def test_daily_budget_stops_new_model_calls_with_a_clear_error():
    ep = Endpoint(routes={"Look": [reply("ok", usage=(1_000_000, 0))]})
    wf, _ = make(None, compat=provider(ep, price=(Decimal("0.4"), Decimal("1.2"))), ai_env={"AI_DAILY_BUDGET_USD": "0.5"})
    try:
        a = compat_analyst(wf)
        _, first = run_one(wf, a)
        assert first["status"] == D.COMPLETED and Decimal(first["cost_usd"]) == Decimal("0.4")
        _, second = run_one(wf, a)
        assert second["status"] == D.COMPLETED  # $0.4 < $0.5: still allowed, and it brings today to $0.8
        g, third = run_one(wf, a)
        assert third["status"] == D.FAILED and "AI 预算已用完" in third["error"] and "AI_DAILY_BUDGET_USD" in third["error"]
        assert len(ep.calls) == 2  # the third run never reached the API
        u = wf.gateway.usage()
        assert u["today_usd"] == "0.8" and u["days"][0]["calls"] == 2
        assert not [m for m in evs(wf, g["id"]) if m["event"] == "model.start"]
    finally:
        wf.stop()


def test_monthly_budget_and_spend_survive_a_restart(tmp_path):
    db = str(tmp_path / "k.db")
    days = iter(["2026-10-01"] * 50 + ["2026-10-25"] * 50 + ["2026-11-01"] * 50)
    store = Store(db)
    gw = Gateway(D.WorkforceDB(store), monthly_budget=Decimal("1"), today=lambda: next(days))
    gw.record("anthropic", "claude-haiku-5-5", 10, 10, Decimal("0.7"))
    store.close() if hasattr(store, "close") else None
    gw2 = Gateway(D.WorkforceDB(Store(db)), monthly_budget=Decimal("1"), today=lambda: "2026-10-25")
    gw2.record("anthropic", "claude-haiku-5-5", 10, 10, Decimal("0.3"))
    assert "本月" in gw2.blocked("anthropic") and gw2.spent("month") == (Decimal("1.0"), 0)
    assert gw2.spent("day") == (Decimal("0.3"), 0)
    gw3 = Gateway(D.WorkforceDB(Store(db)), monthly_budget=Decimal("1"), today=lambda: "2026-11-01")
    assert gw3.blocked("anthropic") is None  # a new month


def test_budget_blocks_planning_and_the_rule_planner_takes_over():
    llm = ScriptedModel(routes={"x": [[text("ok")]]})
    wf, _ = make(llm, ai_env={"AI_DAILY_BUDGET_USD": "0.01"})
    try:
        wf.gateway.record("anthropic", "claude-haiku-5-5", 0, 0, Decimal("0.02"))
        w = analyst(wf, name="W", skills=("wallet_analysis",))
        _, ws = squad(wf, w, manager_provider="anthropic")
        g = wf.submit_goal(ws["id"], f"Check {EOA}", "owner")
        wf.run_until_idle()
        assert wf.db.task(g["id"])["planner"] == "rules" and llm.plan_requests == []
        assert any("AI 预算已用完" in (m.get("text") or "") for m in evs(wf, g["id"]))
    finally:
        wf.stop()


# ---------------------------------------------------------------- circuit breaker

def test_breaker_opens_after_repeated_failures_and_recovers():
    clock = [1000.0]
    ep = Endpoint(fail=[(500, {"error": "down"})] * 2)
    wf, _ = make(None, compat=provider(ep, retries=0), ai_env={"AI_BREAKER_FAILURES": "2", "AI_BREAKER_COOLDOWN": "30"})
    wf.gateway.clock = lambda: clock[0]
    try:
        a = compat_analyst(wf, model_config={"max_model_calls": 1})
        for _ in range(2):
            _, sub = run_one(wf, a)
            assert sub["status"] == D.FAILED and "HTTP 500" in sub["error"]
        calls = len(ep.calls)
        _, sub = run_one(wf, a)
        assert sub["status"] == D.FAILED and "熔断" in sub["error"] and "HTTP 500" in sub["error"]
        assert len(ep.calls) == calls  # fail fast: nothing sent while the breaker is open
        assert wf.gateway.usage()["breaker"]["open"]["openai_compat"]["failures"] >= 2
        clock[0] += 31
        ep.routes = {"Look": [reply("恢复了")]}
        _, sub = run_one(wf, a)
        assert sub["status"] == D.COMPLETED and wf.gateway.blocked("openai_compat") is None
        assert wf.gateway.usage()["days"][0]["failures"] >= 2
    finally:
        wf.stop()


def test_planner_failures_count_towards_the_breaker():
    llm = ScriptedModel()
    llm.errors = [TimeoutError("slow")] * 3
    wf, _ = make(llm, ai_env={"AI_BREAKER_FAILURES": "1"})
    wf.planner.retries = 0
    try:
        w = analyst(wf, name="W", skills=("wallet_analysis",))
        mgr, ws = squad(wf, w, manager_provider="anthropic")
        llm.beta.messages.create = lambda **kw: (_ for _ in ()).throw(TimeoutError("slow"))
        g = wf.submit_goal(ws["id"], f"Check {EOA}", "owner")
        wf.run_until_idle()
        assert wf.db.task(g["id"])["planner"] == "rules"
        assert "熔断" in wf.gateway.blocked("anthropic")
    finally:
        wf.stop()


# ---------------------------------------------------------------- concurrency and ceilings

def test_concurrent_model_calls_are_capped():
    gate = threading.Event()
    peak, now, lock = [0], [0], threading.Lock()

    def slow(url, headers, body, timeout):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        gate.wait(5)
        with lock:
            now[0] -= 1
        return reply("ok")

    wf, _ = make(None, compat=provider(slow), ai_env={"AI_MAX_CONCURRENT_TASKS": "1"})
    try:
        agents = [compat_analyst(wf, name=f"A{i}") for i in range(3)]
        _, ws = squad(wf, *agents)
        gs = [wf.assign_direct(a["id"], "agent_task", {"instruction": "Look"}, "owner", workspace_id=ws["id"]) for a in agents]
        t = threading.Thread(target=wf.run_until_idle)
        t.start()
        time.sleep(0.6)
        assert wf.gateway.usage()["in_flight"] == 1
        gate.set()
        t.join(20)
        assert peak[0] == 1 and all(wf.db.subtasks(g["id"])[0]["status"] == D.COMPLETED for g in gs)
    finally:
        gate.set()
        wf.stop()


def test_cancel_while_waiting_for_a_slot():
    from agent.workforce.chain import Cancelled

    gw = Gateway(D.WorkforceDB(Store()), max_concurrent=1)
    with gw.slot():
        with pytest.raises(Cancelled):
            with gw.slot(lambda: True):
                pass  # pragma: no cover


def test_server_ceilings_override_agent_settings():
    ep = Endpoint(routes={"Look": [reply(None, [("c", "wallet_analysis", {"address": EOA})])]})
    wf, _ = make(None, compat=provider(ep), ai_env={"AI_MAX_AGENT_STEPS": "2", "AI_MAX_OUTPUT_TOKENS": "1024", "AI_REQUEST_TIMEOUT": "30"})
    try:
        a = compat_analyst(wf, model_config={"max_model_calls": 20, "max_tokens": 32000, "call_timeout_s": 300})
        _, sub = run_one(wf, a)
        assert sub["status"] == D.FAILED and "最大模型调用次数 2" in sub["error"]
        assert len(ep.calls) == 2 and ep.calls[0][2]["max_tokens"] == 1024 and ep.calls[0][3] <= 30
    finally:
        wf.stop()
    lim = Limits(max_model_calls=3)
    Gateway(D.WorkforceDB(Store()), max_steps=8).cap(lim)
    assert lim.max_model_calls == 3  # an agent may ask for less


def test_gateway_env_validation():
    db = D.WorkforceDB(Store())
    g = Gateway.from_env(db, {})
    assert g.daily_budget is None and g.max_concurrent == 5 and g.max_steps is None
    g = Gateway.from_env(db, {"AI_DAILY_BUDGET_USD": "0", "AI_MONTHLY_BUDGET_USD": "10", "AI_MAX_CONCURRENT_TASKS": "5", "AI_MAX_AGENT_STEPS": "8"})
    assert g.daily_budget is None and g.monthly_budget == Decimal(10) and g.max_steps == 8
    for env, msg in (({"AI_DAILY_BUDGET_USD": "abc"}, "数字"), ({"AI_DAILY_BUDGET_USD": "-1"}, "负数"),
                     ({"AI_MAX_AGENT_STEPS": "x"}, "整数"), ({"AI_MAX_CONCURRENT_TASKS": "0"}, "之间")):
        with pytest.raises(ValueError, match=msg):
            Gateway.from_env(db, env)


# ---------------------------------------------------------------- AI_* configuration and Qwen

def test_ai_provider_qwen_configuration():
    env = {"AI_PROVIDER": "qwen", "AI_BASE_URL": QWEN_SG, "AI_API_KEY": "sk-q", "AI_MODEL": "qwen-plus"}
    p = from_env(env)
    assert p.label == "qwen" and p.model == "qwen-plus" and p.extra_body == {"enable_thinking": False}
    assert p.price_for("qwen-plus", 1000) == (Decimal("0.4"), Decimal("1.2"))
    assert p.price_for("qwen-plus", 300 * 1024) == (Decimal("1.2"), Decimal("3.6"))
    assert p.price_for("some-other-model", 10) is None
    p = from_env({**env, "AI_ENABLE_THINKING": "1"})
    assert p.extra_body == {"enable_thinking": True} and p.price_for("qwen-plus", 1000) == (Decimal("0.4"), Decimal("4"))
    # Beijing bills in CNY at other prices: no built-in price there, AI_PRICE sets one
    cn = {**env, "AI_BASE_URL": "https://ws-1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    assert from_env(cn).price_for("qwen-plus", 1000) is None
    assert from_env({**cn, "AI_PRICE": "0.11,0.28"}).price_for("qwen-plus", 1000) == (Decimal("0.11"), Decimal("0.28"))
    assert from_env({**env, "AI_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}).price_for("qwen-flash", 1) is not None
    # Japan (Tokyo): its own (lower) Flash prices; qwen-plus is not priced there
    jp = from_env({**env, "AI_BASE_URL": "https://llm-abc.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1", "AI_MODEL": "qwen3.7-flash"})
    assert jp.price_for("qwen3.7-flash", 1000) == (Decimal("0.028"), Decimal("0.110")) and jp.price_for("qwen-plus", 1000) is None


def test_ai_provider_errors_and_other_services():
    with pytest.raises(ValueError, match="AI_PROVIDER 只能是"):
        from_env({"AI_PROVIDER": "deepmind"})
    with pytest.raises(ValueError, match="AI_API_KEY、AI_MODEL"):
        from_env({"AI_PROVIDER": "qwen", "AI_BASE_URL": QWEN_SG})
    with pytest.raises(ValueError, match="https"):
        from_env({"AI_PROVIDER": "groq", "AI_BASE_URL": "http://api.groq.com/openai/v1", "AI_API_KEY": "k", "AI_MODEL": "m"})
    with pytest.raises(ValueError, match="输入价,输出价"):
        from_env({"AI_PROVIDER": "groq", "AI_BASE_URL": "https://api.groq.com/openai/v1", "AI_API_KEY": "k", "AI_MODEL": "m", "AI_PRICE": "1"})
    with pytest.raises(ValueError, match="负数"):
        from_env({"AI_PROVIDER": "groq", "AI_BASE_URL": "https://api.groq.com/openai/v1", "AI_API_KEY": "k", "AI_MODEL": "m", "AI_PRICE": "-1,0"})
    local = from_env({"AI_PROVIDER": "local", "AI_BASE_URL": "http://localhost:11434/v1", "AI_MODEL": "qwen3:8b"})
    assert local.key == "local" and local.price_for("qwen3:8b", 10) == (Decimal(0), Decimal(0)) and local.extra_body == {}
    g = from_env({"AI_PROVIDER": "openai_compat", "AI_BASE_URL": "https://api.groq.com/openai/v1", "AI_API_KEY": "k", "AI_MODEL": "m"})
    assert g.label == "openai_compatible" and g.price_for("m", 1) is None
    assert from_env({"AI_PROVIDER": "anthropic"}) is None


def test_qwen_agent_end_to_end_with_list_price():
    ep = Endpoint(routes={"Look": [reply(None, [("c1", "wallet_analysis", {"address": EOA})], model="qwen-plus"),
                                   reply("钱包有 2 BNB", usage=(10_000, 1_000), model="qwen-plus")]})
    p = from_env({"AI_PROVIDER": "qwen", "AI_BASE_URL": QWEN_SG, "AI_API_KEY": "sk-q", "AI_MODEL": "qwen-plus"}, post=ep)
    p.backoff_s = 0
    wf, _ = make(None, compat=p)
    try:
        a = compat_analyst(wf)
        _, sub = run_one(wf, a, f"Look at {EOA}")
        assert sub["status"] == D.COMPLETED and sub["result"]["answer"] == "钱包有 2 BNB"
        assert all(c[2]["enable_thinking"] is False and c[2]["model"] == "qwen-plus" for c in ep.calls)
        # 800 + 10000 input at $0.4, 100 + 1000 output at $1.2 per million tokens
        assert Decimal(sub["cost_usd"]) == (Decimal(10_800) * Decimal("0.4") + Decimal(1_100) * Decimal("1.2")) / Decimal(1_000_000)
        assert Decimal(wf.gateway.usage()["today_usd"]) == Decimal(sub["cost_usd"])
    finally:
        wf.stop()


def test_build_workforce_reads_ai_env():
    wf = build_workforce(Store(), env={"AI_PROVIDER": "qwen", "AI_BASE_URL": QWEN_SG, "AI_API_KEY": "sk", "AI_MODEL": "qwen-plus",
                                       "AI_DAILY_BUDGET_USD": "1", "AI_MAX_AGENT_STEPS": "8"}, compat_post=Endpoint())
    try:
        assert wf.compat.label == "qwen" and wf.gateway.daily_budget == Decimal(1) and wf.gateway.max_steps == 8
        assert wf.llm is None and list(wf.runtime.providers) == ["openai_compat"]
    finally:
        wf.stop()
    made = []
    wf = build_workforce(Store(), env={"AI_PROVIDER": "anthropic", "AI_API_KEY": "sk-ant", "AI_MODEL": "claude-sonnet-5-5"},
                         anthropic_factory=lambda: made.append(1))
    try:
        assert wf.llm is not None and wf.planner.model == "claude-sonnet-5-5" and wf.compat is None
        a = wf.create_agent({"name": "x", "provider": "anthropic"}, "owner")
        assert a["model"] == "claude-sonnet-5-5"
    finally:
        wf.stop()
    with pytest.raises(ValueError, match="AI_PROVIDER=anthropic 还需要"):
        build_workforce(Store(), env={"AI_PROVIDER": "anthropic"})


def test_qwen_price_table():
    assert qwen_price("qwen3.7-flash", 1000) == (Decimal("0.03"), Decimal("0.13"))
    assert qwen_price("qwen3.7-flash", 100 * 1024) == (Decimal("0.1"), Decimal("0.4"))
    assert qwen_price("qwen3.7-flash", 2 * 1024 * 1024) == (Decimal("0.2"), Decimal("0.8"))  # past the last tier: the last price
    assert qwen_price("qwen-plus-2025-12-01", 10) == (Decimal("0.4"), Decimal("1.2"))
    assert qwen_price("qwen3.6-plus", 10, thinking=True) == (Decimal("0.5"), Decimal("3"))  # one output price for this model
    assert qwen_price("qwen-max", 10) == (Decimal("1.6"), Decimal("6.4")) and qwen_price("qwen3.7-max", 10) is None
    assert qwen_price("qwen3.6-flash", 300 * 1024, region="jp") == (Decimal("0.66"), Decimal("3.961"))
