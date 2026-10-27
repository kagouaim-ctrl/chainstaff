"""The /api/at console API and the /api/ingest endpoints, through the real FastAPI app.

The console token never works on the ingest endpoints, and a watcher's token never works on the console."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from autonomy_kit import ROOT, Kit
from test_bsc import TOKEN_AUTH

from agent.autonomy import constants as C
from agent.workforce.engine import WorkforceError

H = {"Authorization": "Bearer " + TOKEN_AUTH}


@pytest.fixture
def api(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    k = Kit(tmp_path)
    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "app.db")})
    app = create_app(emp, TOKEN_AUTH, None, env={}, workforce=k.wf, autonomy=k.at)
    c = TestClient(app)
    yield c, k
    k.close()


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def test_console_needs_the_token(api):
    c, k = api
    assert c.get("/api/at/overview").status_code == 401
    assert c.get("/api/at/overview", headers={"Authorization": "Bearer kgw_nope"}).status_code == 401


def test_agent_lifecycle_through_the_api(api):
    c, k = api
    meta = ok(c.get("/api/at/meta", headers=H))
    assert {t["id"] for t in meta["templates"]} >= {"bug_hunter", "token_watcher"} and "funds:transfer" in meta["human_only"]
    assert meta["default_permissions"] == ["delegate", "memory:write"] and meta["models"]["configured"]
    ws = ok(c.post("/api/at/workspaces", headers=H, json={"name": "Pricing", "resources": {"repos": [
        {"name": "svc", "provider": "local", "path": str(k.repo), "test_command": "python -m unittest -q"}], "endpoints": [{"name": "api", "url": "https://a.example.com"}]}}))
    assert c.post("/api/at/workspaces", headers=H, json={"name": "Bad", "resources": {"repos": [{"provider": "svn"}]}}).status_code == 400
    wss = ok(c.get("/api/at/workspaces", headers=H))
    assert wss[0]["agents"] == 0 and wss[0]["open_tasks"] == 0
    r = c.post("/api/at/agents", headers=H, json={"name": "X", "responsibility": "r", "activate": True})
    assert r.status_code == 400 and "Workspace" in r.json()["detail"]
    assert not ok(c.get("/api/at/agents", headers=H))  # nothing half-created
    a = ok(c.post("/api/at/agents", headers=H, json={"template": "investigator", "name": "Inv", "workspace_ids": [ws["id"]], "activate": True}))
    assert a["autonomous"] and a["activated_at"] and a["permissions"] == ["delegate", "memory:write", "code:read", "code:test"]
    d = ok(c.get(f"/api/at/agents/{a['id']}", headers=H))
    assert d["state"] == C.A_WAITING and d["agent"]["work_mode"] == "event"
    assert c.get("/api/at/agents/agt_nope", headers=H).status_code == 400
    a = ok(c.put(f"/api/at/agents/{a['id']}", headers=H, json={"name": "Investigator", "notify_rules": {"min_severity": "low"}}))
    assert a["name"] == "Investigator" and a["notify_rules"]["min_severity"] == "low"
    assert c.put(f"/api/at/agents/{a['id']}", headers=H, json={"work_mode": "sometimes"}).status_code == 400
    assert c.put(f"/api/at/agents/{a['id']}", headers=H, json={"provider": "nope"}).status_code == 400
    t = ok(c.post(f"/api/at/agents/{a['id']}/tasks", headers=H, json={"objective": "look at the logs", "kind": "investigate", "deadline_minutes": 30}))
    assert t["to_agent_id"] == a["id"] and t["status"] == C.T_QUEUED and not t["deduplicated"]
    assert c.post(f"/api/at/agents/{a['id']}/tasks", headers=H, json={"objective": ""}).status_code == 400
    assert c.post(f"/api/at/agents/{a['id']}/archive", headers=H).status_code == 200  # only queued work: it is handed back unassigned
    assert k.at.db.task(t["id"])["status"] == C.T_UNASSIGNED
    assert c.post(f"/api/at/agents/{a['id']}/activate", headers=H).status_code == 400
    assert c.post(f"/api/at/agents/{a['id']}/dance", headers=H).status_code == 400
    legacy = k.wf.create_agent({"name": "Old"}, "user")
    assert c.post(f"/api/at/agents/{legacy['id']}/tasks", headers=H, json={"objective": "x"}).status_code == 400
    assert ok(c.get("/api/at/agents?archived=true", headers=H))


def test_activate_rules(api):
    c, k = api
    ws = k.workspace()
    a = ok(c.post("/api/at/agents", headers=H, json={"name": "Later", "responsibility": "r", "workspace_ids": [ws["id"]]}))
    assert not a["activated_at"]
    assert k.at.db.missions(responsibility_of=a["id"]) == []
    k.at.db.update_agent(a["id"], responsibility="")
    assert "职责" in c.post(f"/api/at/agents/{a['id']}/activate", headers=H).json()["detail"]
    k.at.db.update_agent(a["id"], responsibility="r", workspace_ids=[])
    assert "Workspace" in c.post(f"/api/at/agents/{a['id']}/activate", headers=H).json()["detail"]
    k.at.db.update_agent(a["id"], workspace_ids=[ws["id"]])
    k.at.set_agent_status(a["id"], "pause", "user")
    out = ok(c.post(f"/api/at/agents/{a['id']}/activate", headers=H))
    assert out["agent"]["status"] == "active" and out["missions"] and out["warnings"] == []
    k.at.db.stop_worker(k.worker.id)
    assert any("Worker" in w for w in k.at.activate(a["id"], "user")["warnings"])
    assert k.at.runtime_health()["level"] == "down" and k.at.overview()["kpis"]["runtime_health"] == "down"


def test_archive_refused_while_working(api):
    c, k = api
    ws = k.workspace()
    a = k.agent("investigator", "Inv", ws)
    t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
    k.at.db.claim(t["id"], "w", 60)
    assert "进行中" in c.post(f"/api/at/agents/{a['id']}/archive", headers=H).json()["detail"]
    m = k.agent("bug_hunter", "Hunter", ws)
    ok(c.post(f"/api/at/agents/{m['id']}/archive", headers=H))
    assert {x["status"] for x in k.at.db.missions(responsibility_of=m["id"])} == {C.M_STOPPED}
    def locked(*a):
        raise WorkforceError("locked")

    k.wf.set_agent_status = locked
    assert c.post(f"/api/at/agents/{m['id']}/resume", headers=H).json()["detail"] == "locked"


def test_memory_endpoints(api):
    c, k = api
    ws = k.workspace()
    a = k.agent("investigator", "Inv", ws)
    b = k.agent("investigator", "Other", ws)
    m1 = k.at.db.wdb.remember("agent", a["id"], "fact one", source="t")
    m2 = k.at.db.wdb.remember("workspace", ws["id"], "shared fact", source="t")
    m3 = k.at.db.wdb.remember("agent", b["id"], "not yours", source="t")
    mem = ok(c.get(f"/api/at/agents/{a['id']}/memory", headers=H))
    assert {m["text"] for m in mem} == {"fact one", "shared fact"}
    assert c.delete(f"/api/at/agents/{a['id']}/memory/{m3}", headers=H).status_code == 404
    assert c.delete(f"/api/at/agents/{a['id']}/memory/99999", headers=H).status_code == 404
    ok(c.delete(f"/api/at/agents/{a['id']}/memory/{m1}", headers=H))
    ok(c.delete(f"/api/at/agents/{a['id']}/memory/{m2}", headers=H))
    assert ok(c.get(f"/api/at/agents/{a['id']}/memory", headers=H)) == []


def test_workspace_watcher_and_ingest_endpoints(api):
    c, k = api
    ws = k.workspace()
    k.trio(ws)
    assert c.get("/api/at/workspaces/ws_nope", headers=H).status_code == 404
    out = ok(c.put(f"/api/at/workspaces/{ws['id']}", headers=H, json={"name": "Pricing svc", "resources": {"wallets": [{"address": "0x" + "B" * 40}]}}))
    assert out["name"] == "Pricing svc" and out["resources"]["wallets"][0]["address"] == "0x" + "b" * 40
    assert c.put(f"/api/at/workspaces/{ws['id']}", headers=H, json={"resources": {"wallets": [{"address": "0x1"}]}}).status_code == 400
    assert c.put("/api/at/workspaces/ws_nope", headers=H, json={}).status_code == 400

    logs = ok(c.post("/api/at/watchers", headers=H, json={"workspace_id": ws["id"], "kind": "app_logs"}))
    ci = ok(c.post("/api/at/watchers", headers=H, json={"workspace_id": ws["id"], "kind": "ci_failure"}))
    gh = ok(c.post("/api/at/watchers", headers=H, json={"workspace_id": ws["id"], "kind": "github_webhook"}))
    assert logs["ingest_path"] == f"/api/ingest/logs/{logs['watcher']['id']}" and "secret_hash" not in json.dumps(logs["watcher"])
    assert c.post("/api/at/watchers", headers=H, json={"workspace_id": ws["id"], "kind": "fax"}).status_code == 400
    listed = ok(c.get(f"/api/at/watchers?workspace_id={ws['id']}", headers=H))
    assert len(listed) == 3 and all(w["healthy"] for w in listed) and not any("webhook_secret" in w for w in listed)
    detail = ok(c.get(f"/api/at/workspaces/{ws['id']}", headers=H))
    assert len(detail["watchers"]) == 3 and len(detail["agents"]) == 3 and detail["missions"]

    # Logs: the watcher's token only; never the console token.
    path, tok = logs["ingest_path"], logs["secret"]
    item = {"level": "error", "message": "ZeroDivisionError: division by zero", "service": "pricing"}
    assert c.post(path, json=item).status_code == 401
    assert c.post(path, json=item, headers=H).status_code == 401
    r = ok(c.post(path, json=[item, item], headers={"X-Kagou-Token": tok}))
    assert r["results"][0]["accepted"] and r["results"][1]["deduplicated"]
    assert c.post(path, content=b"{nope", headers={"Authorization": "Bearer " + tok}).status_code == 400
    assert c.post(path, json=[item] * 101, headers={"Authorization": "Bearer " + tok}).status_code == 413
    assert c.post(path, json={"level": "error", "message": ""}, headers={"Authorization": "Bearer " + tok}).status_code == 400
    assert c.post(path, content=b"x" * 1_000_001, headers={"Authorization": "Bearer " + tok}).status_code == 413
    assert c.post("/api/ingest/logs/wch_nope", json=item).status_code == 404
    assert c.post(f"/api/ingest/logs/{ci['watcher']['id']}", json=item).status_code == 404  # wrong kind
    rot = ok(c.post(f"/api/at/watchers/{logs['watcher']['id']}/rotate", headers=H))
    assert c.post(path, json=item, headers={"X-Kagou-Token": tok}).status_code == 401
    ok(c.post(path, json=item, headers={"X-Kagou-Token": rot["secret"]}))
    ok(c.post(f"/api/at/watchers/{logs['watcher']['id']}/pause", headers=H))
    assert c.post(path, json=item, headers={"X-Kagou-Token": rot["secret"]}).status_code == 409
    assert c.post(f"/api/at/watchers/{logs['watcher']['id']}/explode", headers=H).status_code == 400
    ok(c.post(f"/api/at/watchers/{logs['watcher']['id']}/resume", headers=H))
    assert ok(c.put(f"/api/at/watchers/{logs['watcher']['id']}", headers=H, json={"name": "API errors"}))["name"] == "API errors"

    # CI.
    r = ok(c.post(ci["ingest_path"], json={"status": "failed", "pipeline": "unit", "commit": "abc"}, headers={"Authorization": "Bearer " + ci["secret"]}))
    assert r["accepted"]
    # GitHub: HMAC over the raw body with the webhook secret.
    body = json.dumps({"action": "completed", "repository": {"full_name": "o/r"}, "workflow_run": {"name": "CI", "conclusion": "failure"}}).encode()
    sig = "sha256=" + hmac.new(gh["secret"].encode(), body, hashlib.sha256).hexdigest()
    gh_h = {"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d1", "Content-Type": "application/json"}
    assert c.post(gh["ingest_path"], content=body, headers={**gh_h, "X-Hub-Signature-256": "sha256=00"}).status_code == 401
    assert c.post(gh["ingest_path"], content=body, headers=gh_h).status_code == 401
    r = ok(c.post(gh["ingest_path"], content=body, headers={**gh_h, "X-Hub-Signature-256": sig}))
    assert k.at.db.event(r["event_id"])["type"] == "ci.failed"
    bad = b"not json"
    assert c.post(gh["ingest_path"], content=bad, headers={**gh_h, "X-Hub-Signature-256": "sha256=" + hmac.new(gh["secret"].encode(), bad, hashlib.sha256).hexdigest()}).status_code == 400
    assert c.post(gh["ingest_path"], content=b"x" * 2_000_001, headers=gh_h).status_code == 413

    evs = ok(c.get(f"/api/at/events?workspace_id={ws['id']}&severity=high,critical&range=24h", headers=H))
    assert evs and all(e["severity"] in ("high", "critical") for e in evs)
    assert ok(c.get(f"/api/at/events/{evs[0]['id']}", headers=H))["tasks"] == []
    assert c.get("/api/at/events/evt_nope", headers=H).status_code == 404
    man = ok(c.post("/api/at/events", headers=H, json={"workspace_id": ws["id"], "title": "Users report 500s", "severity": "high"}))
    assert man["source"] == "manual" and man["type"] == "manual.report" and not man["deduplicated"]
    assert c.post("/api/at/events", headers=H, json={"workspace_id": ws["id"], "severity": "loud"}).status_code == 400
    ok(c.delete(f"/api/at/watchers/{ci['watcher']['id']}", headers=H))
    assert c.delete(f"/api/at/watchers/{ci['watcher']['id']}", headers=H).status_code == 400


def test_missions_tasks_activity_and_approvals(api):
    c, k = api
    ws = k.workspace()
    a1, a2, a3 = k.trio(ws)
    m = ok(c.post("/api/at/missions", headers=H, json={"workspace_id": ws["id"], "name": "Audit", "goal": "audit the code", "type": "one_time", "lead_agent_id": a2["id"]}))
    assert m["run_count"] == 1
    ms = ok(c.get(f"/api/at/missions?workspace_id={ws['id']}&status=active", headers=H))
    assert {x["name"] for x in ms} >= {"Audit"} and ms[0]["lead_name"]
    md = ok(c.get(f"/api/at/missions/{m['id']}", headers=H))
    assert md["tasks"] and md["agents"][0]["name"] == "Agent 2"
    assert c.get("/api/at/missions/msn_nope", headers=H).status_code == 400
    assert c.post("/api/at/missions", headers=H, json={}).status_code == 400
    ok(c.put(f"/api/at/missions/{m['id']}", headers=H, json={"goal": "audit more"}))
    cont = ok(c.post("/api/at/missions", headers=H, json={"workspace_id": ws["id"], "name": "Keep", "goal": "g", "type": "continuous", "lead_agent_id": a2["id"]}))
    t = ok(c.post(f"/api/at/missions/{cont['id']}/run", headers=H))
    ok(c.post(f"/api/at/missions/{cont['id']}/pause", headers=H))
    assert c.post(f"/api/at/missions/{cont['id']}/run", headers=H).status_code == 400

    tasks = ok(c.get(f"/api/at/tasks?workspace_id={ws['id']}&status=queued&range=24h&q=audit", headers=H))
    assert tasks["total"] == 1 and tasks["items"][0]["mission_id"] == m["id"]
    td = ok(c.get(f"/api/at/tasks/{t['id']}", headers=H))
    assert td["task"]["id"] == t["id"] and td["root"]["id"] == t["id"]
    assert c.get("/api/at/tasks/job_nope", headers=H).status_code == 400
    ok(c.post(f"/api/at/tasks/{t['id']}/cancel", headers=H, json={"reason": "no"}))
    assert ok(c.post(f"/api/at/tasks/{t['id']}/retry", headers=H))["status"] == C.T_QUEUED
    un, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="nobody", required=["github:draft_pr"])
    assert c.post(f"/api/at/tasks/{un['id']}/assign", headers=H, json={"agent_id": a2["id"]}).status_code == 400
    assert c.get("/api/at/runs/run_nope", headers=H).status_code == 404

    k.log_error(ws)
    k.drain()
    run = k.at.db.runs(limit=1)[0]
    rd = ok(c.get(f"/api/at/runs/{run['id']}", headers=H))
    assert rd["steps"]
    acts = ok(c.get(f"/api/at/activity?workspace_id={ws['id']}&types=task.*,agent.awakened&severity=info,medium&range=24h", headers=H))
    assert acts and all(a["type"].startswith("task.") or a["type"] == "agent.awakened" for a in acts)
    named = ok(c.get("/api/at/activity?types=bug.detected", headers=H))[0]
    assert named["actor_name"].startswith("Watcher ")
    human = ok(c.get("/api/at/activity?needs_human=true", headers=H))
    assert human and all(a["needs_human"] for a in human)
    newer = ok(c.get(f"/api/at/activity?after={acts[-1]['id']}", headers=H))
    assert [a["id"] for a in newer] == sorted(a["id"] for a in newer)
    s = c.get(f"/api/at/stream?once=true&after={k.at.db.last_activity_id() - 2}", headers=H)
    assert s.status_code == 200 and s.text.startswith("retry: 2000") and s.text.count("event: activity") == 2 and '"actor_name"' in s.text
    s = c.get("/api/at/stream?once=true", headers={**H, "Last-Event-ID": str(k.at.db.last_activity_id())})
    assert "event: activity" not in s.text

    notes = ok(c.get("/api/at/notifications?unread=true", headers=H))
    assert notes["unread"] >= 1
    assert ok(c.post("/api/at/notifications/read", headers=H, json={"ids": [notes["items"][0]["id"]]}))["marked"] == 1
    ok(c.post("/api/at/notifications/read", headers=H, json={}))
    assert ok(c.get("/api/at/notifications", headers=H))["unread"] == 0

    [ap] = ok(c.get("/api/at/approvals", headers=H))
    assert ap["agent_name"] == "Agent 3"
    art = ok(c.get(f"/api/at/artifacts/{ap['artifact_id']}", headers=H))
    assert "average_price" in art["content"]
    assert c.get("/api/at/artifacts/art_nope", headers=H).status_code == 404
    assert c.post(f"/api/at/approvals/{ap['id']}", headers=H, json={"approve": "yes"}).status_code == 400
    out = ok(c.post(f"/api/at/approvals/{ap['id']}", headers=H, json={"approve": True, "note": "ship it"}))
    assert out["approval"]["status"] == "approved" and out["applied_commit"]
    assert c.post(f"/api/at/approvals/{ap['id']}", headers=H, json={"approve": True}).status_code == 400

    ov = ok(c.get("/api/at/overview", headers=H))
    assert ov["kpis"]["total_agents"] == 3 and ov["last_activity_id"] > 0
    rt = ok(c.get("/api/at/runtime", headers=H))
    assert rt["worker_alive"] and rt["limits"]["max_depth"] == 4
    use = ok(c.get("/api/at/usage?range=7d", headers=H))
    assert use["totals"]["runs"] >= 3 and use["by_agent"] and use["calls"] and use["totals"]["model_latency_avg_s"] is not None


def test_write_rate_limit(tmp_path):
    from fastapi import FastAPI

    from backend.autonomy_api import mount_autonomy

    k = Kit(tmp_path)
    try:
        app = FastAPI()
        mount_autonomy(app, k.at, lambda: None, "owner", write_limit=2)
        c = TestClient(app)
        codes = [c.post("/api/at/workspaces", json={"name": f"w{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429]
    finally:
        k.close()


def test_quick_create_name_and_appearance(api):
    """The console's New Worker dialog saves a draft with a name and an avatar; the rest is set later and activation still checks it."""
    c, k = api
    a = ok(c.post("/api/at/agents", headers=H, json={"name": "Wallet Monitor", "appearance": {"shape": "orbit", "color": "emerald"}}))
    assert a["responsibility"] == "" and not a["activated_at"] and a["appearance"] == {"shape": "orbit", "color": "emerald"}
    row = next(r for r in ok(c.get("/api/at/agents", headers=H)) if r["id"] == a["id"])
    assert row["appearance"] == {"shape": "orbit", "color": "emerald"}
    assert "职责" in c.post(f"/api/at/agents/{a['id']}/activate", headers=H).json()["detail"]
    assert "职责" in c.post("/api/at/agents", headers=H, json={"name": "Now", "activate": True}).json()["detail"]
    assert c.post("/api/at/agents", headers=H, json={"name": "X", "appearance": {"shape": "cat"}}).status_code == 400
    assert c.post("/api/at/agents", headers=H, json={"name": "X", "appearance": {"color": "pink"}}).status_code == 400
    assert c.post("/api/at/agents", headers=H, json={"name": "X", "appearance": "gold"}).status_code == 400
    a = ok(c.put(f"/api/at/agents/{a['id']}", headers=H, json={"appearance": {"shape": "hex", "color": "gold"}}))
    assert a["appearance"] == {"shape": "hex", "color": "gold"}
    assert k.wf.create_agent({"name": "Old"}, "user") and all(isinstance(r["appearance"], dict) for r in ok(c.get("/api/at/agents", headers=H)))
