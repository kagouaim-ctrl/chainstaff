"""Read-only demo login (KAGOU_DEMO_TOKEN): a visitor sees every page and stream and can change nothing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.bsc import ChainService
from agent.config import load_chain
from agent.workforce import Workforce
from test_bsc import ROOT, TOKEN_AUTH, WALLET, FakeBSC, pool

DEMO = "d" * 24
OWNER_H = {"Authorization": "Bearer " + TOKEN_AUTH}
DEMO_H = {"Authorization": "Bearer " + DEMO}


@pytest.fixture
def app(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    chain = load_chain(ROOT / "config" / "chain.example.json")
    wf = Workforce(emp.store)
    svc = ChainService(emp.store, chain, wf, rpc=pool(FakeBSC()))
    yield TestClient(create_app(emp, TOKEN_AUTH, chain, workforce=wf, chain_service=svc, viewer_token=DEMO)), emp
    wf.stop()


def test_demo_login_reads_everything(app):
    c, _ = app
    assert c.get("/api/session", headers=DEMO_H).json() == {"role": "viewer"}
    assert c.get("/api/session", headers=OWNER_H).json() == {"role": "owner"}
    for path in ("/api/overview", "/api/team", "/api/wf/overview", "/api/wf/agents", "/api/wf/missions", "/api/chain/status",
                 "/api/chain/monitors", "/api/chain/events"):
        assert c.get(path, headers=DEMO_H).status_code == 200, path
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": DEMO})
        assert ws.receive_json()["type"] in ("hello", "status", "resync")


def test_demo_login_changes_nothing(app):
    c, emp = app
    writes = [("post", "/api/pause", None), ("put", "/api/rules", {}), ("post", "/api/wf/agents", {"name": "x"}),
              ("post", "/api/wf/missions", {"goal": "x"}), ("post", "/api/chain/monitors", {"kind": "address", "target": WALLET}),
              ("post", "/api/chain/health-check", None), ("delete", "/api/team/chain_monitor", None),
              ("put", "/api/treasury/alerts", {"enabled": False})]
    for method, path, body in writes:
        r = getattr(c, method)(path, headers=DEMO_H, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and "只能查看" in r.json()["detail"], (method, path)
    assert not emp.store.paused and c.get("/api/chain/monitors", headers=OWNER_H).json()["monitors"] == []
    assert c.post("/api/pause", headers=OWNER_H).status_code == 200  # the owner token still writes
    assert c.get("/api/session", headers={"Authorization": "Bearer " + "x" * 24}).status_code == 401


def test_demo_token_must_be_strong_and_distinct(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    for bad in ("short", TOKEN_AUTH):
        with pytest.raises(RuntimeError, match="KAGOU_DEMO_TOKEN"):
            create_app(emp, TOKEN_AUTH, None, viewer_token=bad)
