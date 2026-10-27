"""Chats (/api/at/chats): a message becomes a real task, the answer is read back from that task, and a follow-up
carries the earlier turns to the agent as evidence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autonomy_kit import ROOT, Kit, Script, turn
from test_bsc import TOKEN_AUTH

from agent.autonomy import constants as C

H = {"Authorization": "Bearer " + TOKEN_AUTH}


@pytest.fixture
def api(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    script = Script()
    k = Kit(tmp_path, provider=script)
    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "app.db")})
    app = create_app(emp, TOKEN_AUTH, None, env={}, workforce=k.wf, autonomy=k.at)
    yield TestClient(app), k, script
    k.close()


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def test_chat_with_one_agent_is_a_task_and_the_result_comes_back(api):
    c, k, script = api
    ws = k.workspace()
    inv = k.agent("investigator", "Inv", ws)
    script.turns += [turn(("complete_task", {"summary": "No anomaly in the last 24h."})), turn(("complete_task", {"summary": "Checked again: still fine."}))]

    chat = ok(c.post("/api/at/chats", headers=H, json={"text": "检查最近 24 小时的异常", "agent_id": inv["id"], "kind": "investigate"}))
    assert chat["agent_id"] == inv["id"] and chat["title"].startswith("检查") and chat["agent_name"] == "Inv"
    [m] = chat["messages"]
    assert m["task"]["to_agent_id"] == inv["id"] and m["task"]["status"] == C.T_QUEUED and m["task"]["kind"] == "investigate"
    assert m["task"]["evidence"]["source"] == "chat" and m["task"]["evidence"]["conversation"] == []
    assert m["routing"] == {"auto": False, "agent_id": inv["id"]} and m["error"] is None

    k.drain()
    m = ok(c.get(f"/api/at/chats/{chat['id']}", headers=H))["messages"][0]
    assert m["task"]["status"] == C.T_COMPLETED and m["task"]["result"]["summary"] == "No anomaly in the last 24h."

    # A follow-up goes to the same agent and carries the first exchange as evidence.
    chat = ok(c.post(f"/api/at/chats/{chat['id']}/messages", headers=H, json={"text": "再查一次"}))
    m2 = chat["messages"][1]
    assert m2["task"]["to_agent_id"] == inv["id"]
    [h] = m2["task"]["evidence"]["conversation"]
    assert h == {"user": "检查最近 24 小时的异常", "agent": "Inv", "status": C.T_COMPLETED, "result": "No anomaly in the last 24h."}
    k.drain()
    assert "检查最近 24 小时的异常" in str(script.requests[-1]["messages"])  # the agent saw the conversation

    lst = ok(c.get("/api/at/chats", headers=H))
    assert [x["id"] for x in lst] == [chat["id"]] and lst[0]["turns"] == 2 and lst[0]["last_status"] == C.T_COMPLETED
    assert ok(c.get(f"/api/at/chats?agent_id={inv['id']}", headers=H))[0]["id"] == chat["id"]
    assert ok(c.get("/api/at/chats?agent_id=agt_other", headers=H)) == []


def test_without_an_agent_kagou_picks_one_or_says_nobody_fits(api):
    c, k, _ = api
    assert "工作区" in ok(c.post("/api/at/chats", headers=H, json={"text": "anything"}))["messages"][0]["error"]  # no workspace yet: said, not hidden

    k.workspace("Empty")  # first, but nobody works there: KAGOU picks the workspace that has agents
    ws = k.workspace()
    fixer = k.agent("fix_agent", "Fixer", ws)
    chat = ok(c.post("/api/at/chats", headers=H, json={"text": "修复 pricing 里的除零错误", "kind": "fix"}))
    m = chat["messages"][0]
    assert m["task"]["to_agent_id"] == fixer["id"] and m["routing"]["auto"] and m["routing"]["agent_id"] == fixer["id"]
    assert any(x["agent_id"] == fixer["id"] and x["eligible"] for x in m["routing"]["considered"])

    k.at.set_agent_status(fixer["id"], "pause", "user")
    m = ok(c.post(f"/api/at/chats/{chat['id']}/messages", headers=H, json={"text": "再修一个", "kind": "fix"}))["messages"][1]
    assert m["task"]["status"] == C.T_UNASSIGNED and m["routing"]["agent_id"] is None  # nobody fits: the task waits for a person to assign it


def test_chat_validation_and_archive(api):
    c, k, _ = api
    k.workspace()
    assert c.post("/api/at/chats", headers=H, json={"agent_id": "agt_nope", "text": "x"}).status_code == 400
    chat = ok(c.post("/api/at/chats", headers=H, json={"title": "Empty"}))
    assert chat["messages"] == [] and chat["title"] == "Empty"
    assert c.post(f"/api/at/chats/{chat['id']}/messages", headers=H, json={"text": " "}).status_code == 400
    assert c.post(f"/api/at/chats/{chat['id']}/messages", headers=H, json={"text": "x", "kind": "dance"}).status_code == 400
    assert c.post(f"/api/at/chats/{chat['id']}/messages", headers=H, json={"text": "x" * 4001}).status_code == 400
    assert c.get("/api/at/chats/cht_nope", headers=H).status_code == 400
    assert ok(c.put(f"/api/at/chats/{chat['id']}", headers=H, json={"title": "Renamed"}))["title"] == "Renamed"
    assert c.put(f"/api/at/chats/{chat['id']}", headers=H, json={"title": ""}).status_code == 400
    assert ok(c.put(f"/api/at/chats/{chat['id']}", headers=H, json={"archived": True}))["archived"]
    assert ok(c.get("/api/at/chats", headers=H)) == []
    assert c.get("/api/at/chats").status_code == 401
