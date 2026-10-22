import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import PolicyFile, load_chain, policy_from_dict
from agent.runtime import build_employee
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "config" / "policy.example.json").read_text())
TOKEN = "t" * 24


@pytest.fixture
def env(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(EXAMPLE))
    return {"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "db" / "l.db")}


@pytest.fixture
def client(env):
    emp, _ = build_employee(env)
    emp.handle_request("付 Bob 1500 USDT 重构", "@lina", "r1", datetime(2026, 10, 20, tzinfo=timezone.utc))
    emp.handle_request("付 Alice 300 USDT 设计", "@lina", "r2", datetime(2026, 10, 20, tzinfo=timezone.utc))
    return TestClient(create_app(emp, TOKEN)), emp


H = {"Authorization": "Bearer " + TOKEN}


def test_requires_token(client):
    c, _ = client
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/").status_code == 200  # page itself is public; data isn't


def test_serves_the_browser_telegram_client(client):
    c, _ = client
    r = c.get("/vendor/gramjs.min.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert b"window.GramJS" in r.content or b"GramJS" in r.content


def test_short_token_refused(env):
    emp, _ = build_employee(env)
    with pytest.raises(RuntimeError):
        create_app(emp, "short")


def test_status_payments_report(client):
    c, _ = client
    assert c.get("/api/status", headers=H).json() == {"paused": False, "live": False, "pending": 1}
    rows = c.get("/api/payments", headers=H).json()
    assert {r["request_id"] for r in rows} == {"r1", "r2"}
    assert next(r for r in rows if r["request_id"] == "r2")["payee_name"] == "Alice"
    assert "周报" in c.get("/api/report/weekly", headers=H).json()["text"]


def test_pause_resume_from_console_blocks_bot(client):
    c, emp = client
    assert "已暂停" in c.post("/api/pause", headers=H).json()["message"]
    r = emp.handle_request("付 Alice 1 USDT 设计", "@lina", "r3", datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert "已拦截" in r.text
    assert "已恢复" in c.post("/api/resume", headers=H).json()["message"]


def test_approve_reject(client):
    c, emp = client
    assert "已记为老板签字" in c.post("/api/payments/r1/approve", headers=H).json()["message"]
    assert "不在待签字列表" in c.post("/api/payments/r1/reject", headers=H).json()["message"]


def test_console_works_without_owner_list(env):
    cfg = dict(EXAMPLE, owners=[])
    Path(env["CHAINSTAFF_POLICY"]).write_text(json.dumps(cfg))
    emp, _ = build_employee(env)
    c = TestClient(create_app(emp, TOKEN))
    assert "只有老板" in c.post("/api/pause", headers=H).json()["message"]  # nobody is owner yet: safe default


def test_edit_rules_applies_to_bot(client):
    c, emp = client
    cfg = c.get("/api/rules", headers=H).json()
    cfg["single_limit"]["USDT"] = "100"
    res = c.put("/api/rules", headers=H, json=cfg)
    assert res.status_code == 200 and "单笔 ≤ 100" in res.json()["rules_text"]
    r = emp.handle_request("付 Alice 150 USDT 设计", "@lina", "r9", datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert "需要老板签字" in r.text


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c["payees"].append({"name": "X", "address": "0x123"}), "地址格式不对"),
    (lambda c: c.__setitem__("single_limit", {"USDT": "-1"}), "负数"),
    (lambda c: c.pop("daily_limit"), "缺少字段"),
    (lambda c: c.__setitem__("risk_addresses", ["bad"]), "风险地址"),
    (lambda c: c.__setitem__("single_limit", {"USDT": "abc"}), "格式不对"),
])
def test_bad_rules_rejected_and_file_untouched(client, mutate, msg):
    c, emp = client
    cfg = c.get("/api/rules", headers=H).json()
    before = json.dumps(cfg, sort_keys=True)
    mutate(cfg)
    res = c.put("/api/rules", headers=H, json=cfg)
    assert res.status_code == 400 and msg in res.json()["detail"]
    assert json.dumps(c.get("/api/rules", headers=H).json(), sort_keys=True) == before


def test_policy_file_reload(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(EXAMPLE))
    pf = PolicyFile(p)
    a = pf.load()
    assert pf.load() is a  # cached
    pf.save(dict(EXAMPLE, read_only=True))
    assert pf.load()[0].read_only


def test_policy_from_dict_owners():
    _, owners = policy_from_dict(dict(EXAMPLE, owners=["@Boss", 12345]))
    assert owners == frozenset({"boss", "12345"})


# ---------- runtime / chain wiring ----------

def chain_file(tmp_path, **over):
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg["tokens"]["USDT"]["address"] = "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd"
    cfg.update(over)
    p = tmp_path / "chain.json"
    p.write_text(json.dumps(cfg))
    return str(p)


class FakeRpc:
    def __init__(self, chain_id=97):
        self._id = chain_id

    def __call__(self, url):
        return self

    def chain_id(self):
        return self._id

    def safe_nonce(self, safe):
        return 4

    def total_balance(self, tokens, holders):
        from decimal import Decimal
        return {"USDT": Decimal("1000")}


def test_load_chain_skips_tokens_without_address(tmp_path):
    c = load_chain(ROOT / "config" / "chain.example.json")
    assert c.chain_id == 97 and set(c.tokens) == {"BNB"}
    c2 = load_chain(chain_file(tmp_path))
    assert set(c2.tokens) == {"BNB", "USDT"}


def test_runtime_test_mode_with_chain(env, tmp_path):
    env["CHAINSTAFF_CHAIN"] = chain_file(tmp_path, safe_address="0x1111111111111111111111111111111111111111",
                                         treasury_addresses=["0x2222222222222222222222222222222222222222"])
    emp, chain = build_employee(env, rpc_factory=FakeRpc())
    assert not emp.executor.live and emp.proposer is not None
    r = emp.handle_request("付 Bob 1500 USDT 重构", "@l", "r1", datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert "Safe 交易哈希" in r.text
    assert emp.treasury_balance() == {"USDT": 1000}


class FakeWallet:
    address = "0x00000000000000000000000000000000000000e1"


def test_runtime_live_requires_wallet_kind(env, tmp_path):
    env.update(CHAINSTAFF_CHAIN=chain_file(tmp_path), CHAINSTAFF_LIVE="1", CHAINSTAFF_WALLET="evm")
    emp, _ = build_employee(env, wallet_factory=lambda kind: FakeWallet(), rpc_factory=FakeRpc())
    assert emp.executor.live


def test_runtime_live_refuses_wrong_chain(env, tmp_path):
    env.update(CHAINSTAFF_CHAIN=chain_file(tmp_path), CHAINSTAFF_LIVE="1", CHAINSTAFF_WALLET="evm")
    with pytest.raises(RuntimeError, match="链 ID"):
        build_employee(env, wallet_factory=lambda kind: FakeWallet(), rpc_factory=FakeRpc(56))


@pytest.mark.parametrize("extra,match", [({}, "主网实盘"), ({"CHAINSTAFF_ALLOW_MAINNET": "yes", "CHAINSTAFF_WALLET": "evm"}, "本地私钥")])
def test_runtime_mainnet_guards(env, tmp_path, extra, match):
    env.update(CHAINSTAFF_CHAIN=chain_file(tmp_path, chain_id=56), CHAINSTAFF_LIVE="1", **extra)
    with pytest.raises(RuntimeError, match=match):
        build_employee(env, wallet_factory=lambda kind: FakeWallet(), rpc_factory=FakeRpc(56))


def test_runtime_falls_back_to_example_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    emp, chain = build_employee({"CHAINSTAFF_DB": ":memory:"})
    assert chain is None and len(emp.policy.whitelist) == 3
