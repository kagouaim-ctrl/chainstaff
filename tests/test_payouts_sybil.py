"""Campaign payouts and airdrop sybil checks: rules, exports, the employees and their console APIs."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.execution.batch_export import csv_airdrop, tx_builder_json
from agent.execution.tokens import TokenInfo
from agent.rules.batch import POOL, BatchRules, Row, compute, parse_rows
from agent.runtime import build_front_desk
from agent.skills.airdrop_sybil.sybil import (
    EtherscanProfileSource,
    Profile,
    SybilChecker,
    SybilSettings,
    analyze,
    parse_addresses,
)
from agent.skills.campaign_payouts.payouts import CampaignPayouts, PayoutError
from agent.store import Store
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "config" / "policy.example.json").read_text())
RISK = EXAMPLE["risk_addresses"][0]
TOKEN = "t" * 24
H = {"Authorization": "Bearer " + TOKEN}
USDT_ADDR = "0x55d398326f99059ff775485246999027b3197955"
SAFE = "0x1111111111111111111111111111111111111111"
TX = "0x" + "ab" * 32


def a(n: int) -> str:
    return "0x" + f"{n:040x}"


USDT = TokenInfo("USDT", 18, USDT_ADDR)
BNB = TokenInfo("BNB", 18, None)


# ---------- batch rules ----------

def test_parse_rows_formats():
    rows, problems = parse_rows(f"address,amount,note\n{a(1)},100,KOL A\n\n# comment\n{a(2)},\n{a(3)},5,x,y")
    assert [(r.line, r.value, r.note) for r in rows] == [(2, "100", "KOL A"), (6, "5", "x,y")]
    assert problems == [f"第 5 行少了数量：{a(2)}"]
    rows, problems = parse_rows(f"{a(1)}\t7\n{a(2)} 8 Bob the builder\n钱包 数量")
    # a header word after line 1 is just another row; compute() rejects its address
    assert [(r.address, r.value, r.note) for r in rows] == [(a(1), "7", ""), (a(2), "8", "Bob the builder"), ("钱包", "数量", "")]
    assert problems == []
    rows, _ = parse_rows(f"地址 数量\n{a(1)} 3")
    assert len(rows) == 1


def test_batch_rules_validation():
    for kw, msg in [({"mode": "x"}, "结算方式"), ({"mode": POOL}, "奖池"), ({"budget": Decimal(0)}, "预算"),
                    ({"per_person_cap": Decimal(-1)}, "单人上限"), ({"min_amount": Decimal(-1)}, "最低")]:
        with pytest.raises(ValueError, match=msg):
            BatchRules("USDT", **kw)


def test_compute_fixed_mode():
    rows = [Row(1, a(1), "100", "A"), Row(2, a(1), "50"), Row(3, "0xbad", "5"), Row(4, a(3), "abc"), Row(5, RISK, "10"),
            Row(6, a(6), "30"), Row(7, a(7), "20"), Row(8, a(8), "1"), Row(9, a(9), "0.0000000000000000001"), Row(10, a(10), "500")]
    r = compute(rows, BatchRules("USDT", budget=Decimal(1000), per_person_cap=Decimal(200), min_amount=Decimal(5), decimals=18),
                risk=[RISK], exclude={a(6): "同一资金来源"}, paid_before={a(7): "批次 #1"})
    by = {i.line: i for i in r.items}
    assert by[1].status == "ok" and by[1].amount == 100
    assert by[2].reasons == ["重复地址：和第 1 行相同"]
    assert by[3].reasons == ["地址格式不对：0xbad"] and by[4].reasons == ["数量无效：abc"]
    assert by[5].reasons == ["风险地址：在老板的风险名单里"]
    assert by[6].reasons == ["反女巫剔除：同一资金来源"] and by[7].reasons == ["已经发过：批次 #1"]
    assert by[8].reasons[0].startswith("低于最低发放额") and by[9].reasons[0].startswith("小数位太多")
    assert by[10].reasons[0].startswith("超过单人上限") and by[10].amount == 0
    assert r.total == 100 and r.ok
    s = r.summary()
    assert s["payable"] == 1 and s["excluded"] == 9 and s["excluded_reasons"]["重复地址"] == 1
    assert by[1].to_dict()["amount"] == "100" and by[2].to_dict()["amount"] == "0"
    over = compute([Row(1, a(1), "600"), Row(2, a(2), "600")], BatchRules("USDT", budget=Decimal(1000)))
    assert not over.ok and "超过预算" in over.errors[0]
    none = compute([Row(1, "x", "1")], BatchRules("USDT"))
    assert none.errors == ["没有可以发放的地址"] and not none.ok


def test_compute_pool_mode():
    rows = [Row(1, a(1), "1"), Row(2, a(2), "1"), Row(3, a(3), "1"), Row(4, a(4), "97")]
    r = compute(rows, BatchRules("USDT", mode=POOL, pool=Decimal(1000), per_person_cap=Decimal(500), decimals=18))
    by = {i.line: i for i in r.items}
    assert by[1].amount == Decimal("10") and by[4].amount == 500 and by[4].adjusted
    assert r.total == 530 and r.summary()["adjusted"] == 1
    third = compute([Row(i, a(i), "1") for i in (1, 2, 3)], BatchRules("USDT", mode=POOL, pool=Decimal(100)))
    assert third.items[0].amount == Decimal("33.333333") and third.total <= 100
    zero = compute([Row(1, a(1), "1"), Row(2, a(2), "1000000000000000")], BatchRules("USDT", mode=POOL, pool=Decimal("0.000001")))
    assert zero.items[0].reasons == ["分到的数量为 0"]


# ---------- exports ----------

def test_tx_builder_json_and_csv():
    at = datetime(2026, 10, 21, tzinfo=timezone.utc)
    doc = json.loads(tx_builder_json(97, SAFE, USDT, [(a(1), Decimal("1.5"))], "KOL #1", "desc", at))
    assert doc["chainId"] == "97" and doc["createdAt"] == int(at.timestamp() * 1000)
    assert doc["meta"]["createdFromSafeAddress"] == "0x1111111111111111111111111111111111111111"
    tx = doc["transactions"][0]
    assert tx["to"].lower() == USDT_ADDR and tx["value"] == "0" and tx["data"].startswith("0xa9059cbb")
    assert tx["data"].endswith(hex(15 * 10**17)[2:])
    native = json.loads(tx_builder_json(97, None, BNB, [(a(2), Decimal(2))], "x"))
    assert native["transactions"][0]["value"] == str(2 * 10**18) and native["transactions"][0]["data"] == "0x"
    assert native["meta"]["createdFromSafeAddress"] == ""
    out = csv_airdrop(USDT, [(a(1), Decimal("1.50"))]).splitlines()
    assert out[0] == "token_type,token_address,receiver,amount,id" and out[1].startswith("erc20,0x55d398326f99059fF775485246999027B3197955,")
    assert out[1].endswith(",1.5,")
    assert csv_airdrop(BNB, [(a(1), Decimal(1))]).splitlines()[1].startswith("native,,")
    with pytest.raises(ValueError):
        csv_airdrop(USDT, [(a(1), Decimal(0))])


# ---------- the payouts employee ----------

def policy():
    from agent.config import policy_from_dict

    return policy_from_dict(EXAMPLE)[0]


@pytest.fixture
def payouts():
    s = Store()
    return CampaignPayouts(s, policy, {"USDT": USDT, "BNB": BNB}, chain_id=97, safe_address=SAFE)


def test_payouts_lifecycle(payouts):
    p = payouts
    assert p.token_choices() == ["BNB", "USDT"]
    text = f"{a(1)},100\n{a(2)},50\n{RISK},5"
    pv = p.preview(text, {"token": "usdt", "budget": "1000"})
    assert pv["ok"] and pv["summary"]["payable"] == 2 and pv["summary"]["total"] == "150" and pv["rules"]["budget"] == "1000"
    b = p.create("十月 KOL", text, {"token": "USDT"}, "boss")
    assert b["status"] == "draft" and b["summary"]["excluded"] == 1
    with pytest.raises(PayoutError, match="老板确认后"):
        p.export(b["id"], "safe_json")
    with pytest.raises(PayoutError, match="要先确认"):
        p.mark_executed(b["id"], "", "boss")
    p.propose(b["id"], "boss")
    with pytest.raises(PayoutError, match="不能再确认"):
        p.propose(b["id"], "boss")
    name, media, content = p.export(b["id"], "safe_json")
    assert name == f"safe-batch-{b['id']}.json" and len(json.loads(content)["transactions"]) == 2
    assert p.export(b["id"], "csv_airdrop")[2].count("\n") == 3
    assert p.export(b["id"], "list")[2] == f"address,amount\n{a(1)},100\n{a(2)},50\n"
    with pytest.raises(PayoutError, match="不支持的导出格式"):
        p.export(b["id"], "pdf")
    # the same campaign never pays an address twice
    again = p.preview(f"{a(1)},10\n{a(3)},10", {"token": "USDT"}, "十月 KOL")
    assert again["items"][0]["reasons"] == [f"已经发过：批次 #{b['id']}（已生成 Safe 提案）"]
    with pytest.raises(PayoutError, match="哈希格式"):
        p.mark_executed(b["id"], "0x12", "boss")
    done = p.mark_executed(b["id"], TX, "boss")
    assert done["status"] == "executed" and done["tx_hash"] == TX
    with pytest.raises(PayoutError, match="不能作废"):
        p.cancel(b["id"], "boss")
    b2 = p.create("十一月", f"{a(5)},1", {"token": "BNB"}, "boss")
    assert p.cancel(b2["id"], "boss", "名单错了")["note"] == "名单错了"
    ov = p.overview()
    assert ov["pending"] == 0 and ov["paid_totals"] == {"USDT": "150"} and ov["exportable"] == ["BNB", "USDT"]
    assert "batches" in ov and "items" not in ov["batches"][0]
    assert "#2 十一月" in p.payouts_text() and "已作废" in p.payouts_text()
    texts = [e["text"] for e in p.store.events(("audit",))]
    assert any("老板确认结算批次" in t for t in texts) and any("已在 Safe 执行" in t for t in texts)
    with pytest.raises(PayoutError, match="没有批次"):
        p.get(99)


def test_payouts_errors(payouts):
    p = payouts
    for args, msg in [(("", {"token": "USDT"}), "名单是空的"), ((f"{a(1)},1", {}), "请选择币种"), ((f"{a(1)},1", {"token": "DOGE"}), "不支持的币种"),
                      ((f"{a(1)},1", {"token": "USDT", "budget": "lots"}), "预算格式不对"), ((f"{a(1)},1", {"token": "USDT", "mode": "pool"}), "奖池")]:
        with pytest.raises(PayoutError, match=msg):
            p.preview(*args)
    with pytest.raises(PayoutError, match="活动名称"):
        p.create(" ", f"{a(1)},1", {"token": "USDT"}, "boss")
    with pytest.raises(PayoutError, match="格式问题"):
        p.create("x", f"{a(1)},1\n{a(2)}", {"token": "USDT"}, "boss")
    with pytest.raises(PayoutError, match="没有可以发放"):
        p.create("x", "0xbad,1", {"token": "USDT"}, "boss")
    with pytest.raises(PayoutError, match="一批最多"):
        p.preview("\n".join(f"{a(i)},1" for i in range(1, 5002)), {"token": "USDT"})
    with pytest.raises(PayoutError, match="反女巫检查 #7"):
        p.preview(f"{a(1)},1", {"token": "USDT"}, sybil_check=7)
    bare = CampaignPayouts(p.store, policy)  # no chain config: decimals come from the rules
    assert bare.token_choices() == sorted(policy().token_decimals)
    bid = _proposed(bare, "USDC")
    with pytest.raises(PayoutError, match="没有配置 USDC 的合约地址"):
        bare.export(bid, "safe_json")
    assert bare.export(bid, "list")[0] == f"batch-{bid}.csv"
    assert CampaignPayouts(Store()).token_choices() == ["USDT"]
    assert CampaignPayouts(Store()).payouts_text().startswith("还没有结算批次")


def _proposed(p, token):
    b = p.create("y", f"{a(9)},1", {"token": token}, "boss")
    p.propose(b["id"], "boss")
    return b["id"]


def test_payouts_use_sybil_results(payouts):
    p = payouts
    checker = SybilChecker(p.store)
    funder = a(999)
    for i in range(1, 6):
        p.store.save_profile(a(i), Profile(a(i), funder, "0.01", 1_700_000_000 + i, 4).to_dict())
    cid = checker.start("drop", "\n".join(a(i) for i in range(1, 8)), "boss", background=False)
    pv = p.preview("\n".join(f"{a(i)},1" for i in range(1, 8)), {"token": "USDT"}, sybil_check=cid)
    assert pv["summary"]["excluded"] == 5 and pv["items"][0]["reasons"][0].startswith("反女巫剔除：和另外 4 个地址")


# ---------- sybil analysis ----------

def test_parse_addresses():
    valid, invalid, _ = parse_addresses(f"address\n{a(1)},x\n{a(2).upper().replace('0X', '0x')}\t3\nnope\n\n# c\n{a(3)} extra")
    assert valid == [a(1), a(2), a(3)] and invalid == [{"line": 4, "text": "nope"}]


def test_analyze_rules():
    t0 = 1_760_000_000
    farm = a(900)
    profiles = {}
    for i in range(1, 6):  # shared funder
        profiles[a(i)] = Profile(a(i), farm, "0.05", t0 + i * 3600, 10)
    for i in range(10, 15):  # burst with the same funding amount, different funders
        profiles[a(i)] = Profile(a(i), a(500 + i), "0.0123", t0 + i * 60, 10)
    profiles[a(20)] = Profile(a(20), None, None, t0, 10)  # chain: 20 -> 21 -> 22
    profiles[a(21)] = Profile(a(21), a(20), "1", t0, 10)
    profiles[a(22)] = Profile(a(22), a(21), "1", t0, 10)
    profiles[a(30)] = Profile(a(30), a(31), "2", t0, 10)  # pair: medium only
    profiles[a(31)] = Profile(a(31), None, None, t0, 10)
    pat = ("0xrouter:0x38ed1739", "0xbridge:0x0f5287b0")
    for i in range(40, 45):  # same pattern
        profiles[a(i)] = Profile(a(i), a(600 + i), str(i), t0 + i * 99999, 5, pat)
    profiles[a(50)] = Profile(a(50), a(650), "9", t0, 5, pat[:1] * 2)
    profiles[a(51)] = Profile(a(51), a(651), "8", t0, 5, pat[:1] * 2)  # pattern pair: medium
    profiles[a(60)] = Profile(a(60), a(660), "7", t0, 1)  # low activity
    profiles[a(61)] = Profile(a(61), a(661), "6", 1_790_000_000, 10)  # new wallet
    profiles[a(62)] = Profile(a(62), a(800), "5", t0, 10)  # funder ignored
    for i in range(63, 67):
        profiles[a(i)] = Profile(a(i), a(800), "5", t0 + 999999 * i, 10)
    addrs = list(profiles) + [a(70), a(1), RISK.lower()]
    s = SybilSettings.from_dict({"created_after": "2026-09-01", "ignore_funders": [a(800)]})
    r = analyze(addrs, profiles, s, risk=[RISK], invalid=[{"line": 1, "text": "x"}])
    v = {x["address"]: x for x in r["addresses"]}
    codes = lambda n: {c["code"] for c in v[a(n)]["reasons"]}  # noqa: E731
    assert v[a(1)]["verdict"] == "excluded" and "SHARED_FUNDER" in codes(1) and "DUPLICATE" in codes(1)
    assert v[a(10)]["verdict"] == "excluded" and codes(10) == {"BATCH_CREATED"}
    assert {"FUNDING_CHAIN"} <= codes(21) and v[a(22)]["verdict"] == "excluded"
    assert v[a(30)]["verdict"] == "review" and codes(31) == {"FUNDING_CHAIN"}
    assert v[a(40)]["verdict"] == "excluded" and codes(40) == {"SAME_PATTERN"}
    assert v[a(50)]["verdict"] == "review"
    assert codes(60) == {"LOW_ACTIVITY"} and codes(61) == {"NEW_WALLET"} and v[a(62)]["verdict"] == "clean"
    assert codes(70) == {"NO_DATA"} and v[a(70)]["verdict"] == "clean"
    assert v[RISK.lower()]["verdict"] == "excluded"
    assert r["total"] == len(profiles) + 2 and r["duplicates"] == 1 and r["invalid"] == [{"line": 1, "text": "x"}]
    assert r["clusters"][0]["size"] == 5 and {c["funder"] for c in r["clusters"]} >= {farm, None}
    kinds = {k for c in r["clusters"] for k in c["kinds"]}
    assert kinds == {"SHARED_FUNDER", "BATCH_CREATED", "FUNDING_CHAIN", "SAME_PATTERN"}
    assert a(30) in r["clean"] and a(1) not in r["clean"]
    strict = analyze(addrs, profiles, SybilSettings.from_dict({"review_as": "exclude"}))
    assert a(30) not in strict["clean"] and a(62) not in strict["clean"]  # 62's funder isn't ignored here: 5 share it
    assert r["by_rule"]["SHARED_FUNDER"] == 5 and r["with_data"] == len(profiles)


def test_sybil_settings_validation():
    s = SybilSettings.from_dict({})
    assert s.cluster_min == 5 and s.to_dict()["review_as"] == "keep"
    for d, msg in [({"cluster_min": 1}, "团伙"), ({"window_minutes": 0}, "时间窗口"), ({"min_tx": 101}, "最少交易数"),
                   ({"created_after": "soon"}, "日期"), ({"ignore_funders": ["0x1"]}, "资金来源"), ({"review_as": "x"}, "待复核"),
                   ({"cluster_min": "many"}, "数字")]:
        with pytest.raises(ValueError, match=msg):
            SybilSettings.from_dict(d)
    assert SybilSettings.from_dict({"ignore_funders": [a(1).upper().replace("0X", "0x"), a(1)]}).ignore_funders == [a(1)]


class FakeSource:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), 0

    def profile(self, address):
        self.calls += 1
        if address in self.fail:
            raise RuntimeError("timeout")
        n = int(address, 16)
        return Profile(address, a(777) if n <= 6 else a(n + 1000), "0.1", 1_760_000_000 + n * 9999, 6)


def test_checker_run_and_cache():
    store = Store()
    src = FakeSource(fail={a(9)})
    ch = SybilChecker(store, src, risk=lambda: [a(8)])
    assert ch.ready
    cid = ch.start("", "\n".join(a(i) for i in range(1, 10)), "boss", background=False)
    c = ch.check(cid)
    assert c["status"] == "done" and c["progress"] == 9 and c["name"].startswith("空投名单")
    res = c["result"]
    assert res["counts"] == {"clean": 2, "review": 0, "excluded": 7} and res["lookup_errors"] == 1 and res["source"]
    assert src.calls == 9
    ch.start("again", "\n".join(a(i) for i in range(1, 4)), "boss", background=False)
    assert src.calls == 9  # cached
    name, body = ch.export(cid, "clean")
    assert name == f"airdrop-clean-{cid}.csv" and body.splitlines()[1:] == res["clean"]
    _, ex = ch.export(cid, "excluded")
    assert ex.count("\n") == 8 and "剔除" in ex and "\"" in ex
    assert ch.export(cid, "all")[1].count("\n") == 10
    assert "剔除 7" in ch.sybil_text()
    ov = ch.overview()
    assert ov["checks"][0]["total"] == 3 and ov["checks"][1]["counts"]["excluded"] == 7 and ov["ready"]
    with pytest.raises(ValueError, match="没有有效地址"):
        ch.start("x", "nothing here", "boss", background=False)
    with pytest.raises(ValueError, match="一次最多"):
        ch.start("x", "\n".join(a(i) for i in range(1, 3002)), "boss", background=False)
    with pytest.raises(ValueError, match="没有检查"):
        ch.check(99)


def test_checker_failures_and_text():
    store = Store()
    ch = SybilChecker(store)
    assert not ch.ready and ch.sybil_text().startswith("还没有做过")
    cid = store.add_sybil_check("running", SybilSettings().to_dict(), [a(1)], "boss")
    assert "进行中 0/1" in ch.sybil_text()
    with pytest.raises(ValueError, match="还没完成"):
        ch.export(cid, "clean")
    bad = SybilChecker(store, FakeSource(fail={a(i) for i in range(1, 21)}))
    cid2 = store.add_sybil_check("bad", SybilSettings().to_dict(), [a(i) for i in range(1, 21)], "boss")
    assert bad.run(cid2) is None and store.sybil_check(cid2)["status"] == "failed"
    assert "失败：RuntimeError" in ch.sybil_text()
    s = ch.save_settings({"cluster_min": 3}, "boss")
    assert ch.settings().cluster_min == 3 and s.cluster_min == 3
    # no source: format / duplicate / risk checks only
    cid3 = ch.start("offline", f"{a(1)}\n{a(1)}\nbad", "boss", background=False)
    r = store.sybil_check(cid3)["result"]
    assert r["counts"]["clean"] == 1 and r["duplicates"] == 1 and r["invalid"] == [{"line": 3, "text": "bad"}] and not r["source"]


def test_import_profiles():
    store = Store()
    ch = SybilChecker(store)
    n = ch.import_profiles(f"address,funder,first_seen,tx\n{a(1)},{a(9)},2026-09-03T10:00:00,4,0.01\n{a(2)},none,1760000000,\n{a(3)},{a(9)},notadate,1\nbad,{a(9)},1,1", "boss")
    assert n == 2
    p1 = Profile.from_dict(store.profile(a(1)))
    assert p1.funder == a(9) and p1.tx_count == 4 and p1.fund_amount == "0.01" and p1.first_seen == 1788429600
    p2 = store.profile(a(2))
    assert p2["funder"] is None and p2["first_seen"] == 1760000000 and p2["tx_count"] == 0
    assert ch.import_profiles(f"{a(4)},{a(9)}", "boss") == 1 and store.profile(a(4))["first_seen"] is None
    assert store.profile(a(1), max_age_s=-1) is None and store.profile(a(77)) is None


def test_etherscan_profile_source():
    me = a(1)
    rows = [
        {"from": a(5), "to": me, "value": "0", "isError": "0", "timeStamp": "100", "input": "0x"},
        {"from": a(5), "to": me, "value": str(10**16), "isError": "1", "timeStamp": "101", "input": "0x"},
        {"from": a(6), "to": me, "value": str(5 * 10**16), "isError": "0", "timeStamp": "102", "input": "0x"},
        {"from": me, "to": a(7), "value": "0", "isError": "0", "timeStamp": "103", "input": "0x38ed1739aaaa"},
        {"from": me, "to": None, "value": "0", "isError": "0", "timeStamp": "104", "input": None},
        "garbage",
    ]
    calls = []
    src = EtherscanProfileSource("https://api", "k", lambda url, params: calls.append(params) or {"status": "1", "result": rows}, pause=0)
    p = src.profile(me.upper().replace("0X", "0x"))
    assert p.funder == a(6) and p.fund_amount == "0.05" and p.first_seen == 100 and p.tx_count == 5
    assert p.pattern == (f"{a(7)}:0x38ed1739", ":0x")
    assert calls[0]["sort"] == "asc" and calls[0]["address"] == me
    empty = EtherscanProfileSource("u", "k", lambda url, params: {"status": "0", "message": "No transactions found", "result": []}, pause=0)
    assert empty.profile(me) == Profile(me, None, None, None, 0, ())
    broken = EtherscanProfileSource("u", "k", lambda url, params: {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}, pause=0)
    with pytest.raises(RuntimeError, match="Invalid API Key"):
        broken.profile(me)
    slow = EtherscanProfileSource("u", "k", lambda url, params: {"result": []}, pause=0.001)
    assert slow.profile(me).tx_count == 0


# ---------- desk, runtime and console API ----------

@pytest.fixture
def env(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({**EXAMPLE, "owners": ["boss"]}))
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg.update(safe_address=SAFE, tokens={"BNB": {"decimals": 18, "native": True}, "USDT": {"decimals": 18, "address": USDT_ADDR}})
    (tmp_path / "chain.json").write_text(json.dumps(cfg))
    return {"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db"), "CHAINSTAFF_CHAIN": str(tmp_path / "chain.json"),
            "ETHERSCAN_API_KEY": "k"}


class Rpc:
    def __init__(self, url):
        pass

    def tx_status(self, h):  # pragma: no cover - not used here
        return {}

    def balance(self, token, holder):  # pragma: no cover - not used here
        return Decimal(0)


@pytest.fixture
def built(env):
    d, chain = build_front_desk(env, rpc_factory=Rpc)
    d.sybil.source = FakeSource()
    return d, chain


@pytest.fixture
def desk(built):
    return built[0]


def test_runtime_wires_new_employees(desk):
    assert desk.payouts.safe_address == SAFE and desk.payouts.chain_id == 97 and set(desk.payouts.tokens) == {"BNB", "USDT"}
    assert desk.sybil.risk() == desk.finance.policy.risk_addresses
    plain, _ = build_front_desk({"CHAINSTAFF_DB": ":memory:", "CHAINSTAFF_POLICY": str(ROOT / "config" / "policy.example.json")})
    assert plain.sybil.source is None and plain.payouts.tokens == {} and plain.payouts.safe_address is None


def test_runtime_profile_source(env):
    d, _ = build_front_desk(env, rpc_factory=Rpc)
    assert isinstance(d.sybil.source, EtherscanProfileSource) and d.sybil.source.api_key == "k"


def test_front_desk_commands(desk):
    assert desk.command("payouts", "", "boss").reply.startswith("营销结算员现在没上岗")
    desk.team.hire("campaign_payouts")
    desk.team.hire("airdrop_sybil")
    assert desk.command("payouts", "", "stranger").reply == "只有老板可以看结算批次。"
    assert desk.command("payouts", "", "boss").reply.startswith("还没有结算批次")
    assert desk.command("sybil", "", "stranger").reply == "只有老板可以看反女巫检查。"
    assert desk.command("sybil", "", "boss").reply.startswith("还没有做过")
    assert "/payouts" in desk.team.help_text() and "/sybil" in desk.team.help_text()


@pytest.fixture
def c(built):
    desk, chain = built
    return TestClient(create_app(desk.finance, TOKEN, chain, env={}, desk=desk, sybil_background=False))


def test_api_not_hired(c):
    assert c.get("/api/payouts", headers=H).json()["hired"] is False
    assert c.post("/api/payouts/preview", headers=H, json={"text": "x"}).json()["detail"] == "营销结算员没上岗"
    assert c.post("/api/sybil/checks", headers=H, json={"text": a(1)}).json()["detail"] == "空投反女巫没上岗"
    team = c.get("/api/team", headers=H).json()
    assert {r["id"] for r in team["roles"] if r["status"] == "live" and not r["hired"]} >= {"campaign_payouts", "airdrop_sybil"}


def test_api_sybil_then_payouts(c, desk):
    for role in ("campaign_payouts", "airdrop_sybil"):
        assert c.post("/api/team/hire", headers=H, json={"role": role}).status_code == 200
    team = {m["role"]: m for m in c.get("/api/team", headers=H).json()["members"]}
    assert team["campaign_payouts"]["setup"] == [] and team["airdrop_sybil"]["setup"] == []
    assert team["airdrop_sybil"]["headline"]["value"] == "0 / 0" and team["campaign_payouts"]["headline"]["value"] == "0"
    assert c.put("/api/sybil/settings", headers=H, json={"cluster_min": 0}).status_code == 400
    assert c.put("/api/sybil/settings", headers=H, json={"cluster_min": 5}).json()["settings"]["cluster_min"] == 5
    assert c.post("/api/sybil/profiles", headers=H, json={"text": f"{a(50)},{a(51)},1760000000,9"}).json()["imported"] == 1
    r = c.post("/api/sybil/checks", headers=H, json={"name": "第一期", "text": "\n".join(a(i) for i in range(1, 11))})
    cid = r.json()["id"]
    assert c.post("/api/sybil/checks", headers=H, json={"text": "nope"}).status_code == 400
    chk = c.get(f"/api/sybil/checks/{cid}", headers=H).json()
    assert chk["status"] == "done" and chk["total"] == 10 and chk["result"]["counts"]["excluded"] == 6
    assert c.get("/api/sybil/checks/99", headers=H).status_code == 404
    ex = c.get(f"/api/sybil/checks/{cid}/export/clean", headers=H)
    assert ex.headers["content-disposition"] == f'attachment; filename="airdrop-clean-{cid}.csv"' and ex.text.count("\n") == 5
    assert c.get(f"/api/sybil/checks/{cid}/export/nope", headers=H).status_code == 404
    pending = desk.store.add_sybil_check("x", {}, [a(1)], "boss")
    assert c.get(f"/api/sybil/checks/{pending}/export/all", headers=H).status_code == 400
    sy = c.get("/api/sybil", headers=H).json()
    assert sy["hired"] and sy["rule_names"]["SHARED_FUNDER"] == "同一资金来源" and sy["checks"][1]["counts"]["excluded"] == 6
    team = {m["role"]: m for m in c.get("/api/team", headers=H).json()["members"]}
    assert team["airdrop_sybil"]["headline"]["value"] == "11 / 6" and team["airdrop_sybil"]["todo"]["value"] == 1

    pay = c.get("/api/payouts", headers=H).json()
    assert pay["tokens"] == ["BNB", "USDT"] and pay["sybil_checks"] == [{"id": cid, "name": "第一期", "excluded": 6}]
    body = {"campaign": "第一期空投", "text": "\n".join(f"{a(i)},1" for i in range(1, 11)), "sybil_check": cid,
            "rules": {"token": "USDT", "mode": "pool", "pool": "1000"}}
    pv = c.post("/api/payouts/preview", headers=H, json=body).json()
    assert pv["ok"] and pv["summary"]["payable"] == 4 and pv["summary"]["total"] == "1000"
    assert c.post("/api/payouts/preview", headers=H, json={**body, "rules": {"token": "USDT", "mode": "pool"}}).status_code == 400
    b = c.post("/api/payouts/batches", headers=H, json=body).json()
    assert c.get(f"/api/payouts/batches/{b['id']}/export/safe_json", headers=H).status_code == 400
    assert c.post(f"/api/payouts/batches/{b['id']}/propose", headers=H).json()["status"] == "proposed"
    sj = c.get(f"/api/payouts/batches/{b['id']}/export/safe_json", headers=H)
    assert sj.headers["content-type"].startswith("application/json") and len(sj.json()["transactions"]) == 4
    assert sj.json()["meta"]["createdFromSafeAddress"] == "0x1111111111111111111111111111111111111111"
    assert c.post(f"/api/payouts/batches/{b['id']}/executed", headers=H, json={"tx_hash": TX}).json()["status"] == "executed"
    got = c.get(f"/api/payouts/batches/{b['id']}", headers=H).json()
    assert got["tx_url"] == "https://testnet.bscscan.com/tx/" + TX
    b2 = c.post("/api/payouts/batches", headers=H, json={**body, "campaign": "另一个"}).json()
    assert c.get(f"/api/payouts/batches/{b2['id']}", headers=H).json()["tx_url"] is None
    assert c.post(f"/api/payouts/batches/{b2['id']}/cancel", headers=H, json={"note": "重复"}).json()["status"] == "cancelled"
    assert c.post(f"/api/payouts/batches/{b2['id']}/fly", headers=H).status_code == 404
    assert c.get("/api/payouts/batches/99", headers=H).status_code == 400
    team = {m["role"]: m for m in c.get("/api/team", headers=H).json()["members"]}
    assert team["campaign_payouts"]["headline"]["value"] == "1000 USDT"
    events = [e["text"] for e in c.get("/api/events?employee=campaign_payouts", headers=H).json()]
    assert any(t.startswith("导出结算批次") for t in events)
    assert desk.command("payouts", "", "boss").reply.count("#") == 2


def test_api_setup_hints(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({**EXAMPLE, "owners": ["boss"]}))
    d, _ = build_front_desk({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": ":memory:"})
    d.team.hire("campaign_payouts")
    d.team.hire("airdrop_sybil")
    c = TestClient(create_app(d.finance, TOKEN, None, env={}, desk=d))
    team = {m["role"]: m for m in c.get("/api/team", headers=H).json()["members"]}
    assert team["campaign_payouts"]["setup"] == ["没有配置 Safe 多签地址", "没有配置代币合约地址，只能导出名单"]
    assert team["airdrop_sybil"]["setup"] == ["没有 Etherscan API key，只能用导入的链上资料"]
