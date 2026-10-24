"""The workforce terminal API: the same flow as the engine tests, driven over HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import load_chain
from agent.execution import RpcSender
from agent.execution.rpc import RpcClient
from agent.runtime import build_employee
from agent.workforce import Workforce
from agent.workforce.anchor import Anchor
from agent.workforce.chain import ChainReader
from backend import workforce_api
from backend.app import create_app
from test_workforce import EOA, TOKEN, WALLET, FakeChain, FakeWallet

ROOT = Path(__file__).resolve().parent.parent
TOKEN_AUTH = "t" * 24
H = {"Authorization": "Bearer " + TOKEN_AUTH}


@pytest.fixture
def setup(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    chain = load_chain(ROOT / "config" / "chain.example.json")
    fake = FakeChain()
    reader = ChainReader(RpcClient("http://rpc", post=fake), chain.network, chain.chain_id, chain.explorer_tx_url)
    anchor = Anchor(RpcSender(FakeWallet(), reader.rpc, 97), reader, WALLET)
    wf = Workforce(emp.store, reader=reader, anchor=anchor)
    c = TestClient(create_app(emp, TOKEN_AUTH, chain, workforce=wf))
    yield c, wf, fake
    wf.stop()


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_full_flow_over_http(setup):
    c, wf, fake = setup
    assert c.get("/api/wf/agents").status_code == 401
    meta = ok(c.get("/api/wf/meta", headers=H))
    assert "chain:read" in meta["permissions"] and meta["chain_id"] == 97 and meta["engine"]["anchor"]
    mgr = ok(c.post("/api/wf/agents", headers=H, json={"name": "AI Manager", "is_manager": True}))
    a1 = ok(c.post("/api/wf/agents", headers=H, json={"name": "On-Chain", "skills": ["wallet_analysis", "tx_history"], "permissions": ["chain:read", "net:etherscan"]}))
    a2 = ok(c.post("/api/wf/agents", headers=H, json={"name": "Contract", "skills": ["contract_analysis", "token_info", "contract_events"], "permissions": ["chain:read"]}))
    a3 = ok(c.post("/api/wf/agents", headers=H, json={"name": "Report", "skills": ["data_report"], "permissions": ["report:write"]}))
    assert c.post("/api/wf/agents", headers=H, json={"name": ""}).status_code == 400
    assert ok(c.put(f"/api/wf/agents/{a1['id']}", headers=H, json={"role": "Analyst"}))["role"] == "Analyst"
    dup = ok(c.post(f"/api/wf/agents/{a1['id']}/duplicate", headers=H))
    assert ok(c.post(f"/api/wf/agents/{dup['id']}/status", headers=H, json={"status": "paused"}))["status"] == "paused"
    bulk = ok(c.post("/api/wf/agents/bulk", headers=H, json={"ids": [dup["id"], "agt_nope"], "status": "archived"}))
    assert bulk["ok"] == [dup["id"]] and "agt_nope" in bulk["errors"]
    assert ok(c.delete(f"/api/wf/agents/{dup['id']}", headers=H))["ok"]
    ws = ok(c.post("/api/wf/workspaces", headers=H, json={"name": "Research", "manager_id": mgr["id"], "members": [a1["id"], a2["id"], a3["id"]]}))
    assert ok(c.put(f"/api/wf/workspaces/{ws['id']}", headers=H, json={"description": "d"}))["description"] == "d"
    goal = ok(c.post(f"/api/wf/workspaces/{ws['id']}/goals", headers=H, json={"text": f"Analyze this BSC contract {TOKEN}", "priority": 1}))
    ok(c.post(f"/api/wf/workspaces/{ws['id']}/notes", headers=H, json={"text": "watch the owner"}))
    assert c.post(f"/api/wf/workspaces/{ws['id']}/goals", headers=H, json={"text": ""}).status_code == 400
    wf.run_until_idle()

    tl = ok(c.get(f"/api/wf/workspaces/{ws['id']}/timeline", headers=H))
    kinds = {m["kind"] for m in tl}
    assert {"user", "agent", "task", "tool", "system"} <= kinds
    newer = ok(c.get(f"/api/wf/workspaces/{ws['id']}/timeline", headers=H, params={"after": tl[5]["id"]}))
    older = ok(c.get(f"/api/wf/workspaces/{ws['id']}/timeline", headers=H, params={"before": tl[5]["id"]}))
    assert newer[0]["id"] == tl[6]["id"] and older[-1]["id"] == tl[4]["id"]
    assert ok(c.get(f"/api/wf/workspaces/{ws['id']}/timeline", headers=H, params={"task": goal["id"]}))
    assert c.get("/api/wf/workspaces/ws_nope/timeline", headers=H).status_code == 400
    assert ok(c.get("/api/wf/workspaces", headers=H))[0]["goal_count"] == 1

    tasks = ok(c.get("/api/wf/tasks", headers=H, params={"kind": "subtask", "sort": "priority", "order": "asc", "size": 3, "page": 2}))
    assert tasks["total"] == 6 and len(tasks["items"]) == 3
    assert ok(c.get("/api/wf/tasks", headers=H, params={"status": "failed"}))["total"] == 1
    assert ok(c.get("/api/wf/tasks", headers=H, params={"agent": a2["id"], "start": "2020-01-01", "end": "2999-01-01T00:00:00+00:00"}))["total"] == 3
    assert c.get("/api/wf/tasks", headers=H, params={"start": "nope"}).status_code == 400
    csv_ = c.get("/api/wf/tasks.csv", headers=H)
    assert csv_.status_code == 200 and "result_hash" in csv_.text.splitlines()[0]
    detail = ok(c.get(f"/api/wf/tasks/{goal['id']}", headers=H))
    assert detail["root"]["status"] == "review" and len(detail["graph"]["nodes"]) == 6 and detail["graph"]["edges"] and detail["approvals"]
    sub = detail["subtasks"][0]
    assert ok(c.get(f"/api/wf/tasks/{sub['id']}", headers=H))["root"]["id"] == goal["id"]

    ap = ok(c.get("/api/wf/approvals", headers=H, params={"status": "pending"}))[0]
    ok(c.post(f"/api/wf/approvals/{ap['id']}", headers=H, json={"approve": True, "anchor": True, "note": "ok"}))
    assert c.post(f"/api/wf/approvals/{ap['id']}", headers=H, json={"approve": True}).status_code == 400
    fake.mine()
    wf.run_until_idle()
    recs = ok(c.get("/api/wf/chain/records", headers=H))
    assert recs[0]["status"] == "confirmed" and recs[0]["tx_url"].startswith("https://testnet.bscscan.com/tx/0x") and recs[0]["confirmations"] == 3
    # read the anchoring transaction back and match it with the stored hash
    fake.inputs = {recs[0]["tx_hash"]: FakeWallet().sign_transaction({"data": "0x" + __import__("agent.workforce.anchor", fromlist=["x"]).anchor_calldata(recs[0]["payload_hash"], goal["id"]).hex()})["rawTransaction"]}
    v = ok(c.get(f"/api/wf/chain/verify/{recs[0]['tx_hash']}", headers=H))
    assert v["ok"] and v["onchain"]["task_id"] == goal["id"] and v["block"] == 1001
    assert c.get("/api/wf/chain/verify/0xnope", headers=H).status_code == 404
    fake.inputs = {recs[0]["tx_hash"]: "0x1234"}
    assert ok(c.get(f"/api/wf/chain/verify/{recs[0]['tx_hash']}", headers=H))["ok"] is False
    st = ok(c.get("/api/wf/chain", headers=H))
    assert st["rpc"]["ok"] and st["rpc"]["block"] == fake.head and st["anchor"]["address"] == WALLET and st["anchor"]["bnb"] == "0.1"
    assert ok(c.get("/api/wf/registry", headers=H))[0]["id"] == mgr["id"]
    assert c.post(f"/api/wf/tasks/{goal['id']}/anchor", headers=H).status_code == 400  # already anchored

    ov = ok(c.get("/api/wf/overview", headers=H, params={"range": "24h"}))
    assert ov["agents"]["total"] == 4 and ov["recent_tasks"][0]["id"] == goal["id"] and ov["onchain"] and ov["activity"]
    agents = ok(c.get("/api/wf/agents", headers=H))
    assert {a["id"] for a in agents} == {mgr["id"], a1["id"], a2["id"], a3["id"]}
    view = ok(c.get(f"/api/wf/agents/{a2['id']}", headers=H))
    assert view["tasks_completed"] == 3 and view["logs"] and view["memory"] and view["can_run"]["token_info"] is None and view["audit"]
    assert c.get("/api/wf/agents/agt_nope", headers=H).status_code == 404

    act = ok(c.get("/api/wf/activity", headers=H, params={"kinds": "chain"}))
    assert act and all(m["kind"] == "chain" for m in act)
    assert ok(c.get("/api/wf/activity", headers=H, params={"after": act[-1]["id"], "agent": a2["id"], "task": goal["id"]})) is not None
    body = c.get("/api/wf/stream", headers=H, params={"after": 0, "once": True}).text
    assert body.startswith("retry: 2000") and "event: message" in body
    last = act[0]["id"]
    resumed = c.get("/api/wf/stream", headers={**H, "Last-Event-ID": str(last - 1)}, params={"once": True}).text
    assert resumed.count("event: message") == 1 + sum(1 for m in wf.db.messages(after=last, limit=1000))


def test_cancel_retry_messages_memory_schedules_skills(setup):
    c, wf, fake = setup
    mgr = ok(c.post("/api/wf/agents", headers=H, json={"name": "M", "is_manager": True}))
    a = ok(c.post("/api/wf/agents", headers=H, json={"name": "A", "skills": ["wallet_analysis", "tx_history"], "permissions": ["chain:read", "net:etherscan"]}))
    ws = ok(c.post("/api/wf/workspaces", headers=H, json={"name": "W", "manager_id": mgr["id"], "members": [a["id"]]}))
    g = ok(c.post(f"/api/wf/workspaces/{ws['id']}/goals", headers=H, json={"text": f"check {EOA}"}))
    assert ok(c.post(f"/api/wf/tasks/{g['id']}/cancel", headers=H, json={"reason": "no"}))["status"] == "cancelled"
    assert c.post(f"/api/wf/tasks/{g['id']}/cancel", headers=H).status_code == 400
    g = ok(c.post(f"/api/wf/workspaces/{ws['id']}/goals", headers=H, json={"text": f"check {EOA}"}))
    wf.run_until_idle()
    failed = [s for s in wf.db.subtasks(g["id"]) if s["status"] == "failed"][0]
    assert ok(c.post(f"/api/wf/tasks/{failed['id']}/retry", headers=H))["status"] in ("pending", "assigned")
    m = ok(c.post("/api/wf/messages", headers=H, json={"recipient": a["id"], "text": "hi", "dedupe_key": "x"}))
    assert not m["duplicate"] and ok(c.post("/api/wf/messages", headers=H, json={"recipient": a["id"], "text": "hi", "dedupe_key": "x"}))["duplicate"]

    mem = ok(c.post("/api/wf/memory", headers=H, json={"scope": "agent", "owner": a["id"], "text": "remember the router"}))
    assert ok(c.get("/api/wf/memory", headers=H, params={"q": "router", "agent": a["id"]}))[0]["id"] == mem["id"]
    assert ok(c.get("/api/wf/memory", headers=H, params={"scope": "agent", "owner_id": a["id"]}))
    assert ok(c.put(f"/api/wf/memory/{mem['id']}", headers=H, json={"text": "remember the pair"}))["text"] == "remember the pair"
    assert c.put("/api/wf/memory/999", headers=H, json={"text": "x"}).status_code == 404
    assert ok(c.delete(f"/api/wf/memory/{mem['id']}", headers=H))["ok"] and c.delete(f"/api/wf/memory/{mem['id']}", headers=H).status_code == 404

    s = ok(c.post("/api/wf/schedules", headers=H, json={"workspace_id": ws["id"], "cron": "0 9 * * *", "goal": f"daily {EOA}"}))
    assert ok(c.post(f"/api/wf/schedules/{s['id']}/disable", headers=H))["enabled"] == 0
    assert ok(c.post(f"/api/wf/schedules/{s['id']}/run", headers=H))["ok"]
    assert c.post(f"/api/wf/schedules/{s['id']}/explode", headers=H).status_code == 400
    assert ok(c.get("/api/wf/schedules", headers=H))[0]["runs"]
    assert ok(c.delete(f"/api/wf/schedules/{s['id']}", headers=H))["ok"] and c.delete(f"/api/wf/schedules/{s['id']}", headers=H).status_code == 404

    skills = ok(c.get("/api/wf/skills", headers=H))
    assert {s["id"] for s in skills} >= {"wallet_analysis", "token_info", "contract_analysis", "tx_history", "contract_events", "onchain_monitor", "data_report"}
    assert ok(c.post("/api/wf/skills/onchain_monitor/disable", headers=H))["enabled"] is False
    assert ok(c.post("/api/wf/skills/token_info/run", headers=H, json={"input": {"address": TOKEN}}))["output"]["symbol"] == "KGU"
    assert c.post("/api/wf/skills/token_info/run", headers=H, json={"input": {"address": "x"}}).status_code == 400


def test_chain_status_errors_rate_limit_and_terminal(setup, tmp_path, monkeypatch):
    c, wf, fake = setup
    fake.fail.update({"eth_getBlockByNumber", "eth_getBalance", "eth_blockNumber"})
    st = ok(c.get("/api/wf/chain", headers=H))
    assert st["rpc"]["ok"] is False and "error" in st["anchor"]
    wf.db.add_chain_record(task_id=None, kind="task_hash", network="bsc-testnet", chain_id=97, payload_hash="0x", status="failed")
    assert ok(c.get("/api/wf/chain/records", headers=H))[0]["confirmations"] is None
    wf.reader = None
    assert c.get("/api/wf/chain/verify/0x1", headers=H).status_code == 400
    assert ok(c.get("/api/wf/chain", headers=H))["rpc"] is None
    # rate limit on writes
    lim = workforce_api.RateLimit(2, 60)
    lim.check("k")
    lim.check("k")
    with pytest.raises(Exception):
        lim.check("k")
    lim.window = 0
    lim.check("k")
    # the terminal: 404 until built, then index.html for any client route and real files when they exist
    monkeypatch.setattr(workforce_api, "TERMINAL", tmp_path / "missing")
    assert c.get("/terminal", follow_redirects=False).status_code == 307
    assert c.get("/terminal/").status_code == 404
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>t</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(workforce_api, "TERMINAL", dist)
    assert "<title>t</title>" in c.get("/terminal/workforce").text
    assert c.get("/terminal/assets/app.js").text == "console.log(1)"
    leak = c.get("/terminal/..%2f..%2fpolicy.json")
    assert leak.status_code == 200 and "<title>t</title>" in leak.text  # never a file outside dist


def test_write_rate_limit_returns_429(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    from fastapi import FastAPI

    app = FastAPI()
    wf = Workforce(emp.store)
    workforce_api.mount_workforce(app, wf, lambda: None, "owner", None, write_limit=1)
    c = TestClient(app)
    assert c.post("/api/wf/agents", json={"name": "a"}).status_code == 200
    assert c.post("/api/wf/agents", json={"name": "b"}).status_code == 429
    assert ok(c.get("/api/wf/meta"))["network"] is None
    wf.stop()
