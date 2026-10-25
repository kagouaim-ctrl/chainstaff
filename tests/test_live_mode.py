"""Live mode: the console talks to a real server and must never be misled about what that server can do.

Covers the cross-origin setup (KAGOU_CORS_ORIGINS, WebSocket Origin check), the readiness probe, /api/system, and a
contract check that every API path the KAGOU Console calls exists on the server.
"""

from __future__ import annotations

import json

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent.bsc import ChainService
from agent.config import load_chain
from agent.workforce import Workforce
from backend.app import cors_origins
from test_bsc import ROOT, TOKEN_AUTH, FakeBSC, pool

H = {"Authorization": "Bearer " + TOKEN_AUTH}
CONSOLE = "https://console.example.com"


def build(tmp_path, env=None, chain_on=True, llm=None, db="l.db", compat=None, ai_env=None):
    from agent.runtime import build_employee
    from backend.app import create_app

    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / db) if db else ":memory:"})
    chain = load_chain(ROOT / "config" / "chain.example.json") if chain_on else None
    wf = Workforce(emp.store, llm=llm, compat=compat, ai_env=ai_env)
    svc = ChainService(emp.store, chain, wf, rpc=pool(FakeBSC())) if chain_on else None
    return create_app(emp, TOKEN_AUTH, chain, env=env or {}, workforce=wf, chain_service=svc), emp, wf


@pytest.fixture
def live(tmp_path):
    app, emp, wf = build(tmp_path, {"KAGOU_CORS_ORIGINS": CONSOLE + ", http://localhost:5173/"})
    yield TestClient(app), emp
    wf.stop()


# ---- CORS ----

def test_cors_origins_parsing():
    assert cors_origins(None) == [] and cors_origins(" , ") == []
    assert cors_origins("https://a.example.com/, http://127.0.0.1:5173") == ["https://a.example.com", "http://127.0.0.1:5173"]
    for bad in ("*", "a.example.com", "ftp://a.example.com", "https://a.example.com/console", "http://a.example.com", "https://a.example.com?x=1"):
        with pytest.raises(RuntimeError):
            cors_origins(bad)


def test_listed_origin_gets_cors_headers_and_others_do_not(live):
    c, _ = live
    pre = c.options("/api/wf/agents", headers={"Origin": CONSOLE, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == CONSOLE
    assert "access-control-allow-credentials" not in pre.headers  # bearer tokens only, never cookies
    r = c.get("/api/wf/agents", headers={**H, "Origin": CONSOLE})
    assert r.headers["access-control-allow-origin"] == CONSOLE
    other = c.get("/api/wf/agents", headers={**H, "Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in other.headers
    assert c.get("/api/wf/agents", headers={**H, "Origin": "http://localhost:5173"}).headers["access-control-allow-origin"] == "http://localhost:5173"


def test_no_cors_by_default(tmp_path):
    app, _, wf = build(tmp_path)
    c = TestClient(app)
    assert "access-control-allow-origin" not in c.get("/api/wf/agents", headers={**H, "Origin": CONSOLE}).headers
    wf.stop()


def test_bad_cors_config_stops_the_server(tmp_path):
    with pytest.raises(RuntimeError, match="KAGOU_CORS_ORIGINS"):
        build(tmp_path, {"KAGOU_CORS_ORIGINS": "*"})


def test_websocket_refuses_pages_from_unlisted_sites(live):
    c, _ = live
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/chain/ws", headers={"Origin": "https://evil.example.com"}) as ws:
            ws.receive_json()
    assert e.value.code == 4403
    for origin in (CONSOLE, "http://testserver", None):  # listed, same host, not a browser
        with c.websocket_connect("/api/chain/ws", headers={"Origin": origin} if origin else {}) as ws:
            ws.send_json({"type": "auth", "token": TOKEN_AUTH})
            assert ws.receive_json()["type"] == "hello"


# ---- readiness and capabilities ----

def test_readyz_reports_the_database(live):
    c, emp = live
    assert c.get("/healthz").json() == {"ok": True}
    assert c.get("/readyz").json() == {"ok": True}
    emp.store._db._conn.close()
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json() == {"ok": False}


def test_system_says_what_is_missing(live):
    c, _ = live
    assert c.get("/api/system").status_code == 401
    s = c.get("/api/system", headers=H).json()
    assert s["mode"] == "live"
    assert s["database"] == {"engine": "sqlite", "persistent": True, "file": "l.db"}  # file name only, never the path
    assert s["model"]["configured"] is False and "ANTHROPIC_API_KEY" in s["model"]["reason"] and s["model"]["providers"] == []
    assert s["chain"] == {"configured": True, "network": "bsc-testnet", "chain_id": 97, "listener": True}
    assert s["writes"] == {"payments": False, "agent_signing": False}
    assert s["auth"]["cors_origins"] == [CONSOLE, "http://localhost:5173"]
    assert "ANTHROPIC" not in str({k: v for k, v in s.items() if k != "model"})


def test_system_with_a_model_and_no_chain(tmp_path):
    app, _, wf = build(tmp_path, chain_on=False, llm=lambda **_: None, db=None)
    s = TestClient(app).get("/api/system", headers=H).json()
    assert s["model"]["configured"] is True and s["model"]["providers"] == ["anthropic"] and s["model"]["reason"] is None
    assert s["chain"]["configured"] is False and s["chain"]["listener"] is False
    assert s["database"]["persistent"] is False  # an in-memory database is called out, not passed off as storage
    wf.stop()


# ---- frontend ↔ backend contract ----

TERMINAL = ROOT / "web" / "terminal" / "src"
CALL = re.compile(r"""(?:api|post|put|del|postOnce|download|apiUrl)(?:<[^>]*>)?\(\s*[`"'](/api/[^`"'?$]*(?:\$\{[^}]*\}[^`"'?$]*)*)""")


def _frontend_paths() -> set[str]:
    out = set()
    for f in TERMINAL.rglob("*.ts*"):
        for m in CALL.finditer(f.read_text()):
            path = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1)).rstrip("/")
            out.add(re.sub(r"(?<=[a-z])\{x\}$", "", path))  # `/api/wf/squads${query}`: a query string, not a segment
    out.add("/api/chain/ws")
    return out


def test_every_console_api_path_exists_on_the_server(live):
    c, _ = live
    routes = [re.sub(r"\{[^}]+\}", "{x}", r.path) for r in c.app.routes if getattr(r, "path", "").startswith("/api/")]
    paths = _frontend_paths()
    assert len(paths) > 40  # the scan itself works
    missing = sorted(p for p in paths if not any(re.fullmatch(re.escape(r).replace(re.escape("{x}"), "[^/]+"), p.replace("{x}", "X"))
                                                 for r in routes))
    assert missing == []


def test_system_with_qwen_reports_service_and_spend(tmp_path):
    from agent.workforce.openai_compat import from_env

    qwen = from_env({"AI_PROVIDER": "qwen", "AI_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                     "AI_API_KEY": "sk-secret", "AI_MODEL": "qwen-plus"})
    app, _, wf = build(tmp_path, chain_on=False, db=None, compat=qwen, ai_env={"AI_DAILY_BUDGET_USD": "1", "AI_MONTHLY_BUDGET_USD": "10"})
    c = TestClient(app)
    wf.gateway.record("openai_compat", "qwen-plus", 1000, 200, None)
    s = c.get("/api/system", headers=H).json()
    assert s["model"]["configured"] is True and s["model"]["providers"] == ["openai_compat"] and s["model"]["default_model"] is None
    assert s["model"]["compat"] == {"service": "qwen", "model": "qwen-plus", "host": "dashscope-intl.aliyuncs.com"}
    assert s["ai_usage"]["daily_budget_usd"] == "1" and s["ai_usage"]["monthly_budget_usd"] == "10"
    assert s["ai_usage"]["unpriced_calls_this_month"] == 1 and "days" not in s["ai_usage"]
    assert "sk-secret" not in json.dumps(s)
    u = c.get("/api/wf/ai-usage", headers=H).json()
    assert u["days"][0]["model"] == "qwen-plus" and u["days"][0]["calls"] == 1 and u["days"][0]["input_tokens"] == 1000
    wf.stop()

