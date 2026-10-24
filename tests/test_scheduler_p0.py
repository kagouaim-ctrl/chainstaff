"""Mission scheduler fixes from the KAGOU audit (BUG-2 to BUG-9), one regression test (or more) per bug."""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agent.execution.rpc import RpcClient
from agent.store import Store
from agent.workforce import Workforce, WorkforceError
from agent.workforce import db as D
from agent.workforce.chain import ChainReader
from agent.workforce.planner import Planner
from test_workforce import EOA, TOKEN, FakeChain, FakeLLM, team


class SlowChain(FakeChain):
    """Holds eth_getBalance until released, like a slow RPC node, so a test can cancel mid-run."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def eth_getBalance(self, a, tag):
        self.entered.set()
        assert self.release.wait(10)
        return super().eth_getBalance(a, tag)


class SlowLLM(FakeLLM):
    """A model call that hangs until released (or raises the queued errors first)."""

    def __init__(self, *texts, errors=()):
        super().__init__(*texts)
        self.errors = list(errors)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def create(self, **kw):
        self.entered.set()
        if self.errors:
            self.requests.append(kw)
            raise self.errors.pop(0)
        assert self.release.wait(10)
        return super().create(**kw)


def make(chain=None, **kw) -> tuple[Workforce, FakeChain]:
    chain = chain or FakeChain()
    reader = ChainReader(RpcClient("http://rpc", post=chain), "bsc-testnet", 97)
    return Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)}, max_workers=3, **kw), chain


def events(wf, root_id):
    return [m for m in wf.db.messages(root_id=root_id, limit=1000)]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def plan_json(t):
    return json.dumps({"steps": [
        {"key": "c", "title": "合约", "skill": "contract_analysis", "agent_id": t.contract["id"], "input_json": json.dumps({"address": TOKEN}), "depends_on": []},
        {"key": "r", "title": "报告", "skill": "data_report", "agent_id": t.report["id"], "input_json": "{}", "depends_on": ["c"]}]})


# ------------------------------------------------------------- BUG-2: cancel stops the run, nothing commits after it

@pytest.mark.parametrize("how", ["cancel", "deadline"])
def test_cancelled_run_stops_reading_and_commits_nothing(how):
    wf, chain = make(SlowChain())
    try:
        t = team(wf)
        g = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
        sub = wf.db.subtasks(g["id"])[0]
        wf.tick()
        assert chain.entered.wait(5)  # the skill is inside an RPC call
        assert wf.db.task(sub["id"])["status"] == D.RUNNING
        if how == "cancel":
            wf.cancel(g["id"], "owner")
        else:
            wf.db.update_task(g["id"], deadline_at="2000-01-01T00:00:00+00:00")
            wf.tick()
        calls_at_cancel = len(chain.calls)
        cancel_id = wf.db.last_message_id()
        chain.release.set()  # the RPC request in flight finishes on its own
        wf.drain()
        wf.run_until_idle()
        assert wf.db.task(sub["id"])["status"] == D.CANCELLED and wf.db.task(sub["id"])["result"] is None
        assert wf.db.task(g["id"])["status"] == (D.CANCELLED if how == "cancel" else D.FAILED)
        late = [m["event"] for m in wf.db.messages(after=cancel_id, limit=100)]
        assert "tool.cancelled" in late
        for bad in ("tool.done", "task.completed", "task.result", "handoff", "tool.error", "task.retry"):
            assert bad not in late, late
        assert len(chain.calls) == calls_at_cancel  # no new RPC request after the cancel
        assign = [m for m in events(wf, g["id"]) if m["event"] == "task.assign"]
        assert all(m["status"] in (D.PROCESSED, D.EXPIRED) for m in assign)
        assert not wf.db.search_memory([("agent", t.onchain["id"])])  # nothing remembered from a discarded run
    finally:
        chain.release.set()
        wf.stop()


def test_result_committed_before_cancel_is_kept_and_cancel_is_last():
    """The other order: the run commits first, then the cancel. Logs and steps agree either way."""
    wf, _ = make()
    try:
        t = team(wf)
        g = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
        wf.tick()
        wf.drain()
        sub = wf.db.subtasks(g["id"])[0]
        assert wf.db.task(sub["id"])["status"] == D.COMPLETED
        wf.cancel(g["id"], "owner")
        evs = [m["event"] for m in events(wf, g["id"])]
        assert evs.index("task.completed") < evs.index("task.cancelled")
        assert wf.db.task(sub["id"])["status"] == D.COMPLETED
    finally:
        wf.stop()


# ------------------------------------------------------------- BUG-3: a restart in the middle of planning

def _claim_without_planning(wf, g):
    m = wf.db.goal_message(g["id"])
    wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED, bump=True)
    assert wf._claim_plan(wf.db.message(m["id"]))  # pending -> running; the "process" dies before planning


def test_goal_interrupted_while_planning_is_replanned_once_after_restart():
    wf, chain = make()
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    _claim_without_planning(wf, g)
    assert wf.db.task(g["id"])["status"] == D.RUNNING and wf.db.task(g["id"])["plan_claim"]["attempt"] == 1
    wf.stop()

    wf2 = Workforce(wf.store, reader=wf.reader, tokens=wf.tokens)  # the restarted process, same database
    try:
        assert wf2.recover() == 1
        assert wf2.recover() == 0  # idempotent
        assert wf2.db.task(g["id"])["status"] == D.PENDING
        wf2.run_until_idle()
        assert wf2.db.task(g["id"])["status"] == D.REVIEW
        subs = wf2.db.subtasks(g["id"])
        assert sorted(s["skill"] for s in subs) == ["data_report", "tx_history", "wallet_analysis"]  # one set, no duplicates
        assert wf2.db.task(g["id"])["plan_claim"]["attempt"] == 2
        assert [m["event"] for m in events(wf2, g["id"])].count("plan.created") == 1
        assert any(m["event"] == "plan.requeued" for m in events(wf2, g["id"]))
    finally:
        wf2.stop()


def test_goal_interrupted_too_often_fails_instead_of_looping():
    wf, _ = make()
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    for _ in range(3):
        _claim_without_planning(wf, g)
        w = Workforce(wf.store, reader=wf.reader)
        w.recover()
        w.stop()
        if wf.db.task(g["id"])["status"] != D.PENDING:
            break
    goal = wf.db.task(g["id"])
    assert goal["status"] == D.FAILED and "中断" in goal["error"]
    wf.stop()


def test_running_goal_from_an_older_version_with_subtasks_carries_on():
    wf, _ = make()
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    _claim_without_planning(wf, g)
    wf._plan_claimed(g["id"], t.mgr["id"])  # planned...
    wf.db.update_task(g["id"], status=D.RUNNING)  # ...but an old version left it running
    n = len(wf.db.subtasks(g["id"]))
    wf.stop()
    wf2 = Workforce(wf.store, reader=wf.reader, tokens=wf.tokens)
    try:
        wf2.recover()
        assert wf2.db.task(g["id"])["status"] == D.WAITING
        wf2.run_until_idle()
        assert wf2.db.task(g["id"])["status"] == D.REVIEW and len(wf2.db.subtasks(g["id"])) >= n
    finally:
        wf2.stop()


# ------------------------------------------------------------- BUG-4: planning off the scheduler thread

def test_slow_model_planning_does_not_block_other_missions():
    wf, _ = make()
    llm = SlowLLM()
    try:
        wf.llm = llm
        wf.planner = Planner(llm, (Decimal("5"), Decimal("25")))
        t = team(wf, manager_provider="anthropic")
        llm.texts = [plan_json(t)]
        llm.release.clear()
        slow = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
        started = time.monotonic()
        wf.tick()
        assert llm.entered.wait(5)
        assert time.monotonic() - started < 1.0  # tick came back while the model is still thinking
        # another mission runs to review while the first is still being planned
        other = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
        for _ in range(50):
            wf.tick()
            if wf.db.task(other["id"])["status"] == D.REVIEW:
                break
            time.sleep(0.02)
        assert wf.db.task(other["id"])["status"] == D.REVIEW
        assert wf.db.task(slow["id"])["status"] == D.RUNNING  # still planning
        llm.release.set()
        wf.run_until_idle()
        assert wf.db.task(slow["id"])["status"] == D.REVIEW and wf.db.task(slow["id"])["planner"] == "model"
    finally:
        llm.release.set()
        wf.stop()


def test_cancel_during_planning_discards_the_plan():
    wf, _ = make()
    llm = SlowLLM()
    try:
        wf.llm = llm
        wf.planner = Planner(llm, (Decimal("5"), Decimal("25")))
        t = team(wf, manager_provider="anthropic")
        llm.texts = [plan_json(t)]
        llm.release.clear()
        g = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
        wf.tick()
        assert llm.entered.wait(5)
        wf.cancel(g["id"], "owner")
        llm.release.set()
        wf.drain()
        wf.run_until_idle()
        assert wf.db.task(g["id"])["status"] == D.CANCELLED
        assert wf.db.subtasks(g["id"]) == []
        evs = [m["event"] for m in events(wf, g["id"])]
        assert "plan.discarded" in evs and "plan.created" not in evs
        assert int(wf.db.task(g["id"])["tokens"]) > 0  # the call that was made is still accounted for
    finally:
        llm.release.set()
        wf.stop()


def test_model_timeouts_are_retried_a_limited_number_of_times():
    wf, _ = make()
    llm = SlowLLM(errors=[TimeoutError("read timed out"), ConnectionError("reset")])
    wf.llm = llm
    wf.planner = Planner(llm, None, timeout_s=5, retries=2, backoff_s=0)
    t = team(wf, manager_provider="anthropic")
    llm.texts = [plan_json(t)]
    g = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
    wf.run_until_idle()
    assert len(llm.requests) == 3 and wf.db.task(g["id"])["planner"] == "model"
    # out of retries: the manager falls back to the rule planner and says why
    llm2 = SlowLLM(errors=[TimeoutError("t1"), TimeoutError("t2"), TimeoutError("t3")])
    wf.llm = llm2
    wf.planner = Planner(llm2, None, timeout_s=5, retries=2, backoff_s=0)
    g2 = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
    wf.run_until_idle()
    assert len(llm2.requests) == 3 and wf.db.task(g2["id"])["planner"] == "rules"
    assert any("TimeoutError" in m["text"] for m in events(wf, g2["id"]) if m["event"] == "plan.note")
    # a non-retryable error is not retried
    llm3 = SlowLLM(errors=[ValueError("bad request")])
    wf.llm = llm3
    wf.planner = Planner(llm3, None, retries=2, backoff_s=0)
    wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
    wf.run_until_idle()
    assert len(llm3.requests) == 1
    wf.stop()


def test_planner_passes_an_explicit_timeout_to_the_sdk():
    seen = {}

    class Client(FakeLLM):
        def with_options(self, **kw):
            seen.update(kw)
            return self

    llm = Client()
    Planner(llm, None, timeout_s=12.5)._call({"model": "", "model_config": {}}, "s", "u", None, 100)
    assert seen == {"timeout": 12.5, "max_retries": 0}


# ------------------------------------------------------------- BUG-5: one assignment, one execution

def test_assignment_is_executed_once_and_marked_processed():
    wf, _ = make()
    runs = []
    real = wf._execute

    def counting(agent_id, m):
        runs.append(m["id"])
        real(agent_id, m)

    wf._execute = counting
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    wf.run_until_idle()
    for _ in range(3):
        wf.tick()
        wf.drain()
    assign = [m for m in events(wf, g["id"]) if m["event"] == "task.assign"]
    assert assign and len(runs) == len(assign) == len(set(runs))
    assert all(m["status"] == D.PROCESSED for m in assign)
    wf.stop()


# ------------------------------------------------------------- BUG-6: idempotent mission submit

def test_same_idempotency_key_returns_the_same_mission():
    wf, _ = make()
    t = team(wf)
    a = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner", idempotency_key="req-1")
    b = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner", idempotency_key="req-1")
    assert a["id"] == b["id"] and b["replayed"] and not a.get("replayed")
    with pytest.raises(WorkforceError, match="Idempotency-Key"):
        wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner", idempotency_key="req-1")
    c = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner", idempotency_key="req-2")
    assert c["id"] != a["id"]
    wf.run_until_idle()
    goals = wf.db.tasks(kind="goal", limit=10)[0]
    assert len(goals) == 2
    # a replay after the mission finished still returns it instead of running it again
    assert wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner", idempotency_key="req-1")["id"] == a["id"]
    assert len(wf.db.tasks(kind="goal", limit=10)[0]) == 2
    wf.stop()


def test_concurrent_submits_with_one_key_create_one_mission():
    wf, _ = make()
    t = team(wf)
    out, errors = [], []

    def go():
        try:
            out.append(wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner", idempotency_key="race")["id"])
        except Exception as e:  # pragma: no cover - the assertion below reports it
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors and len(set(out)) == 1
    assert len(wf.db.tasks(kind="goal", limit=10)[0]) == 1
    wf.stop()


# ------------------------------------------------------------- BUG-7: the agent's own budget

def test_agent_over_its_budget_gets_no_more_work():
    wf, _ = make()
    t = team(wf)
    spare = wf.create_agent({"name": "Spare", "skills": ["wallet_analysis"], "permissions": ["chain:read"]}, "owner")
    sq = wf.db.squads(t.ws["id"])[0]
    wf.squad_add_member(sq["id"], spare["id"], "owner")
    wf.update_agent(t.onchain["id"], {"budget_usd": "0.5"}, "owner")
    wf.db.update_agent(t.onchain["id"], cost_usd="0.5")  # spent it all
    assert wf.over_budget(wf.db.agent(t.onchain["id"]))
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    wf.run_until_idle()
    wallet = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "wallet_analysis"][0]
    assert wallet["assigned_agent"] == spare["id"] and wallet["status"] == D.COMPLETED
    # with nobody else to take it, the task fails and says why
    wf.squad_remove_member(sq["id"], spare["id"], "owner")
    g2 = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
    wf.run_until_idle()
    sub = wf.db.subtasks(g2["id"])[0]
    assert sub["status"] == D.FAILED and "预算已用完" in sub["error"]
    wf.stop()


def test_manager_over_budget_plans_without_the_model():
    wf, _ = make()
    llm = FakeLLM()
    wf.llm = llm
    wf.planner = Planner(llm, (Decimal("5"), Decimal("25")))
    t = team(wf, manager_provider="anthropic")
    wf.update_agent(t.mgr["id"], {"budget_usd": "0.01"}, "owner")
    wf.db.update_agent(t.mgr["id"], cost_usd="0.02")
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    wf.run_until_idle()
    assert wf.db.task(g["id"])["planner"] == "rules" and not llm.requests
    assert any("预算已用完" in m["text"] for m in events(wf, g["id"]) if m["event"] == "plan.note")
    wf.stop()


# ------------------------------------------------------------- BUG-8: RPC calls counted per run

def test_rpc_calls_are_counted_per_run_not_globally():
    wf, chain = make()
    t = team(wf)
    a = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
    b = wf.assign_direct(t.onchain["id"], "wallet_analysis", {"address": EOA}, "owner", workspace_id=t.ws["id"])
    wf.run_until_idle()
    done = [m for g in (a, b) for m in events(wf, g["id"]) if m["event"] == "tool.done"]
    assert len(done) == 2
    per_run = [m["data"]["rpc_calls"] for m in done]
    assert per_run[0] == per_run[1] > 0 and sum(per_run) == len(chain.calls)
    assert wf.reader.calls == 0  # the shared reader is never written by runs
    wf.stop()


# ------------------------------------------------------------- BUG-9: finished work wakes the loop

def test_loop_wakes_on_new_and_finished_work_instead_of_waiting_the_interval():
    wf, _ = make()
    t = team(wf)
    wf.start(interval_s=5.0)  # a long idle heartbeat; progress must not depend on it
    try:
        started = time.monotonic()
        g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
        wait_for(lambda: wf.db.task(g["id"])["status"] == D.REVIEW, timeout=4.0)
        assert time.monotonic() - started < 4.0
    finally:
        wf.stop()


def test_submit_idempotency_over_http(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient
    from test_workforce_api import H, ok

    from agent.config import load_chain
    from agent.runtime import build_employee
    from backend.app import create_app

    root = Path(__file__).resolve().parent.parent
    p = tmp_path / "policy.json"
    p.write_text((root / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    chain = load_chain(root / "config" / "chain.example.json")
    reader = ChainReader(RpcClient("http://rpc", post=FakeChain()), chain.network, chain.chain_id)
    wf = Workforce(emp.store, reader=reader)
    c = TestClient(create_app(emp, "t" * 24, chain, workforce=wf))
    try:
        t = team(wf)
        sq = wf.db.squads(t.ws["id"])[0]
        body = {"text": f"analyze {EOA}"}
        r1 = ok(c.post(f"/api/wf/squads/{sq['id']}/missions", headers={**H, "Idempotency-Key": "k-1"}, json=body))
        r2 = ok(c.post(f"/api/wf/squads/{sq['id']}/missions", headers={**H, "Idempotency-Key": "k-1"}, json=body))
        assert r1["id"] == r2["id"] and r2["replayed"]
        r3 = c.post(f"/api/wf/squads/{sq['id']}/missions", headers={**H, "Idempotency-Key": "k-1"}, json={"text": f"analyze {TOKEN}"})
        assert r3.status_code == 400
        assert len(wf.db.tasks(kind="goal", limit=10)[0]) == 1
    finally:
        wf.stop()
