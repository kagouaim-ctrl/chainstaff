"""The AI team: roster, community support, chain monitor, announcement translator,
the front desk that routes Telegram messages, and their console APIs."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import encode
from fastapi.testclient import TestClient

from agent.execution.rpc import RpcClient
from agent.front_desk import Incoming
from agent.runtime import build_employee, build_front_desk
from agent.skills.chain_monitor.monitor import ChainMonitor, MonitorSettings, check_approvals, check_low_balance
from agent.skills.community_support.scam import check_link, detect, host_of, severity, skeleton, warning_text
from agent.skills.community_support.support import CommunitySupport, SupportSettings, match_faq
from agent.skills.finance_ops.alerts import Approval, EtherscanV2Source, Transfer, decode_approval
from agent.skills.translator.translator import Translator, TranslationError, TranslatorSettings, build_prompt, check_draft
from agent.store import Store
from agent.team import ROLES, Team
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "config" / "policy.example.json").read_text())
TOKEN = "t" * 24
H = {"Authorization": "Bearer " + TOKEN}
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)  # 11:00 in Asia/Shanghai
TREASURY = "0x2222222222222222222222222222222222222222"
ALICE = "0xa11ce00000000000000000000000000000007e2d"
STRANGER = "0x9f3c000000000000000000000000000000004a21"
SPENDER = "0x5555555555555555555555555555555555555555"
TX = "0x" + "ab" * 32

SUPPORT_CFG = {
    "project_name": "KAGOU",
    "admins": ["@kagou_lina", "cc_xbnb"],
    "official_domains": ["https://www.kagouai.com/", "pancakeswap.finance"],
    "faq": [
        {"q": "什么时候上线？", "keywords": ["上线", "launch"], "a": "主网 11 月上线，具体时间看公告频道。"},
        {"q": "合约地址是多少", "keywords": ["合约", "contract", "CA"], "a": "合约地址只以官网为准：kagouai.com"},
    ],
    "answer_mode": "questions",
    "welcome": "欢迎 {name}！先看置顶的 FAQ。",
}


@pytest.fixture
def env(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(EXAMPLE))
    return {"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")}


@pytest.fixture
def store():
    return Store()


# ---------- team ----------

def test_team_defaults_to_finance_only(store):
    t = Team(store)
    assert [m.role for m in t.members()] == ["finance_ops"] and t.is_on("finance_ops")
    assert not t.is_on("community_support")
    assert "财务运营员" in t.help_text() and "/pause" in t.help_text()
    assert {c["cmd"] for c in t.commands()} >= {"/rules", "/help", "/team"}


def test_team_hire_update_dismiss(store):
    t = Team(store)
    m = t.hire("community_support", "  小客  ")
    assert m.name == "小客" and t.is_on("community_support")
    with pytest.raises(ValueError, match="已经在团队"):
        t.hire("community_support")
    with pytest.raises(ValueError, match="没有这个岗位"):
        t.hire("cfo")
    with pytest.raises(ValueError, match="还没上线"):
        t.hire("market_brief")
    t.update("community_support", enabled=False, name="客服一号")
    assert not t.is_on("community_support") and t.get("community_support").name == "客服一号"
    t.update("community_support")  # no change, no audit line
    with pytest.raises(ValueError):
        t.update("chain_monitor", enabled=True)
    with pytest.raises(ValueError):
        t.update("cfo", enabled=True)
    t.dismiss("community_support")
    assert t.get("community_support") is None
    with pytest.raises(ValueError, match="只能停用"):
        t.dismiss("finance_ops")
    with pytest.raises(ValueError):
        t.dismiss("chain_monitor")
    texts = [e["text"] for e in store.events(("audit",))]
    assert "解雇 社区客服（设置保留，可随时重新雇佣）" in texts and "雇佣 小客" in texts
    t.update("finance_ops", enabled=False)
    assert "没有在岗" in t.help_text() and "没有在岗" in t.roster_text()
    assert t.commands()[-1]["cmd"] == "/team"


def test_roles_catalog_is_consistent():
    ids = [r.id for r in ROLES]
    assert len(ids) == len(set(ids)) == 10
    assert [r.id for r in ROLES if r.touches_money] == ["finance_ops", "campaign_payouts"]
    assert sum(r.status == "live" for r in ROLES) == 4


def test_events_employee_column_and_migration(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, data TEXT)")
    conn.execute("INSERT INTO events (at, kind, text) VALUES ('2026-01-01T00:00:00.000000+00:00', 'audit', 'old')")
    conn.commit()
    conn.close()
    s = Store(db)
    s.log_event("support", "new", employee="community_support")
    assert [e["text"] for e in s.events(employee="")] == ["old"]
    assert s.events(employee="community_support")[0]["employee"] == "community_support"
    assert s.event_counts(datetime(2025, 1, 1, tzinfo=timezone.utc)) == {"": {"audit": 1}, "community_support": {"support": 1}}
    Store(db)  # second open: no migration needed


def test_tickets_and_translations_store(store):
    tid = store.open_ticket("@a", "help", "原因", "-1")
    assert store.tickets("open")[0]["id"] == tid
    assert store.close_ticket(tid, "boss", "done") and not store.close_ticket(tid, "boss")
    assert store.tickets("closed")[0]["note"] == "done" and len(store.tickets()) == 1
    d = store.add_translation("原文", {"en": "x"}, ["w"], "boss")
    assert store.translation(d)["outputs"] == {"en": "x"} and store.translation(999) is None
    assert store.update_translation(d, {"en": "y"}, [])
    assert store.decide_translation(d, "posted", "boss")
    assert not store.update_translation(d, {"en": "z"}, []) and not store.decide_translation(d, "discarded", "b")
    assert store.translations()[0]["status"] == "posted"


# ---------- scam detection ----------

def test_skeleton_folds_lookalikes():
    assert skeleton("@KAG0U_L1na") == skeleton("kagou_lina") == "kagoullna"
    assert skeleton("сс_хbnb") == skeleton("cc_xbnb")  # Cyrillic с and х
    assert skeleton("rnod") == "mod"


@pytest.mark.parametrize("text,kind", [
    ("请把你的助记词发给我，我帮你验证钱包", "SECRET_REQUEST"),
    ("Please verify your seed phrase here to fix the sync", "SECRET_REQUEST"),
    ("Claim your airdrop at https://kagouai-claim.xyz/connect", "PHISHING_LINK"),
    ("官网是 kag0uai.com 快去", "PHISHING_LINK"),
    ("go to https://xn--kagouai-9za.com now", "PHISHING_LINK"),
    ("free bonus: https://random-drop.io/claim", "SUSPICIOUS_LINK"),
    ("Having issues? DM me and I will help", "DM_BAIT"),
    ("Send 1 BNB to this address and get 2x back!", "GIVEAWAY"),
    ("转 0.5 BNB 原路返回双倍", "GIVEAWAY"),
])
def test_detect_scam_patterns(text, kind):
    found = detect(text, "random_guy", "Random", ["kagou_lina"], ["kagouai.com"])
    assert kind in {f.kind for f in found}


@pytest.mark.parametrize("text", [
    "永远不要把助记词发给任何人",
    "Never share your private key with anyone",
    "官网 https://kagouai.com/docs 和 https://app.kagouai.com 都可以",
    "什么时候上线？",
    "看看 https://github.com/kagou",
])
def test_detect_leaves_normal_messages_alone(text):
    assert detect(text, "member1", "Member", ["kagou_lina"], ["kagouai.com"]) == []


def test_detect_fake_admin_and_admin_names():
    f = detect("hi", "kag0u_lina", "", ["kagou_lina"], [])
    assert f[0].kind == "FAKE_ADMIN" and severity(f) == "high"
    assert detect("hi", "x", "KAGOU Lina", ["kagou_lina"], [])[0].kind == "FAKE_ADMIN"
    assert detect("hi", "kagou_linaa", "", ["kagou_lina"], [])[0].kind == "FAKE_ADMIN"  # one extra letter
    f = detect("hi", "helper", "KAGOU Support", ["kagou_lina"], [])
    assert f[0].kind == "ADMIN_NAME" and severity(f) == "medium"
    assert detect("send your seed phrase", "kagou_lina", "", ["@kagou_lina"], []) == []  # real admin
    assert severity([]) is None
    w = warning_text(f, ["kagouai.com"])
    assert "kagouai.com" in w and "私聊" in w and "官方网址" not in warning_text(f, [])


def test_check_link_edges():
    assert host_of("HTTPS://WWW.Example.com:443/a?b") == "example.com"
    assert check_link("https://docs.kagouai.com", ["kagouai.com"]) is None
    assert check_link("https://abc.io", ["ab.io"]) is None  # official name too short to compare
    assert check_link("https://pancakeswep.finance", ["pancakeswap.finance"]).kind == "PHISHING_LINK"
    assert check_link("localhost", []) is None


# ---------- community support ----------

def support(store, **kw):
    SupportSettings.validate(SUPPORT_CFG).save(store)
    return CommunitySupport(store, **kw)


def test_support_settings_validate():
    s = SupportSettings.validate(SUPPORT_CFG)
    assert s.admins == ["kagou_lina", "cc_xbnb"] and s.official_domains == ["kagouai.com", "pancakeswap.finance"]
    assert SupportSettings.validate({"admins": ["a_b_c", "@A_B_C"], "official_domains": ["x.com", "x.com"]}).admins == ["a_b_c"]
    for bad, msg in [({"admins": "x"}, "列表"), ({"admins": ["a b"]}, "用户名"), ({"official_domains": ["not a domain"]}, "域名"),
                     ({"faq": [{"q": "", "a": "x"}]}, "都要填"), ({"faq": ["x"]}, "格式"), ({"faq": [{}] * 201}, "200"),
                     ({"answer_mode": "all"}, "answer_mode"), ({"scam_action": "ban"}, "scam_action"),
                     ({"escalate_unknown": "yes"}, "开或关")]:
        with pytest.raises(ValueError, match=msg):
            SupportSettings.validate(bad)
    s = SupportSettings.validate({"scam_action": "delete", "escalate_unknown": False, "welcome": " hi ", "project_name": "P"})
    assert (s.scam_action, s.escalate_unknown, s.welcome) == ("delete", False, "hi")


def test_match_faq():
    faq = SupportSettings.validate(SUPPORT_CFG).faq
    assert match_faq("请问什么时候 launch", faq)[0] == 0
    assert match_faq("合约地址是多少？", faq)[0] == 1
    assert match_faq("今天天气不错", faq) is None


def test_support_answers_faq_and_logs(store):
    s = support(store)
    r = s.handle("项目什么时候上线？", "member1", "M", mentioned=False, chat="-1")
    assert r.action == "faq" and "11 月" in r.text and r.faq_index == 0
    assert s.handle("我们上线了", "member1").action == "none"  # not a question in questions mode
    st = s.stats()
    assert st["counts"]["faq"] == 1 and st["top_faq"][0]["q"] == "什么时候上线？" and st["answer_rate"] == 1.0
    assert s.handle("今天怎么样？", "m").action == "none"  # unprompted, no FAQ hit: stay quiet


def test_support_scam_warns_and_deletes(store):
    s = support(store)
    r = s.handle("DM me for support, admin here", "kag0u_lina", "KAGOU Lina")
    assert r.action == "scam" and not r.delete and "FAKE_ADMIN" in [f.kind for f in r.findings]
    SupportSettings.validate({**SUPPORT_CFG, "scam_action": "delete"}).save(store)
    r = s.handle("send your seed phrase to verify", "x1")
    assert r.delete and r.to_dict()["findings"][0]["kind"] == "SECRET_REQUEST"
    st = s.stats()
    assert st["counts"]["scam"] == 2 and st["scam_kinds"]["SECRET_REQUEST"] == 1
    SupportSettings.validate({**SUPPORT_CFG, "scam_action": "off"}).save(store)
    assert s.scan("send your seed phrase to verify") == []


def test_support_tx_lookup(store):
    calls = []

    def lookup(h):
        calls.append(h)
        return {"tx_" + "0" * 62: {}}.get(h) or {"status": {"0x" + "ab" * 32: "success"}.get(h, "failed"), "block": 9}

    s = support(store, tx_lookup=lookup, explorer_tx_url="https://bscscan.com/tx/")
    r = s.handle(f"我的交易 {TX} 到了吗", "m")
    assert r.action == "tx" and "已成功" in r.text and "bscscan.com/tx/" + TX in r.text
    assert "执行失败" in s.tx_text("0x" + "cd" * 32)
    for status, word in [("pending", "等待打包"), ("not_found", "找不到")]:
        s.tx_lookup = lambda h, st=status: {"status": st}
        assert word in s.tx_text(TX)

    def boom(h):
        raise RuntimeError("down")

    s.tx_lookup = boom
    assert "暂时查不到" in s.tx_text(TX)
    assert "没有配置 RPC" in CommunitySupport(store).tx_text(TX)
    assert s.handle(f"txid {TX} lol", "m").action == "none"  # just mentioned in passing


def test_support_escalates_to_human(store):
    s = support(store)
    r = s.handle("@bot 我要找人工", "m", mentioned=True, chat="-1")
    assert r.action == "ticket" and "@kagou_lina" in r.text and r.ticket_id
    r = s.handle("@bot 我的币丢了怎么办", "m", mentioned=True)
    assert r.action == "ticket" and store.tickets("open")[0]["reason"] == "FAQ 里没有答案"
    assert "待处理工单 2 个" in s.tickets_text()
    assert s.handle("@bot 我要找人工", "m", mentioned=True, simulate=True).ticket_id is None
    SupportSettings.validate({**SUPPORT_CFG, "escalate_unknown": False}).save(store)
    r = s.handle("@bot 随便问问怎么办", "m", mentioned=True)
    assert r.action == "unknown" and "/faq" in r.text
    assert "#3" in s.human("需要帮助", "m") and s.human("", "m")
    SupportSettings.validate({"admins": []}).save(store)
    assert "还没有设置管理员" in s.human("x", "m")
    assert s.stats()["tickets_open"] == 4


def test_support_texts(store):
    s = CommunitySupport(store)
    assert "还没有配置" in s.faq_text() and s.welcome_text("A") is None and s.tickets_text() == "没有待处理的工单。"
    assert s.stats()["answer_rate"] is None
    s = support(store)
    assert s.faq_text().startswith("KAGOU 常见问题") and "2. 合约地址是多少" in s.faq_text()
    assert s.welcome_text("Ann") == "欢迎 Ann！先看置顶的 FAQ。"
    assert s.is_admin("@KAGOU_LINA") and not s.is_admin(None)
    SupportSettings.validate({"faq": SUPPORT_CFG["faq"]}).save(store)
    assert s.faq_text().startswith("常见问题")


# ---------- chain monitor ----------

def approve_row(selector, args, types, frm=TREASURY, to=ALICE, ts=None, err="0"):
    return {"hash": "0x" + "11" * 32, "from": frm, "to": to, "isError": err,
            "timeStamp": str(int((ts or NOW).timestamp())), "input": selector + encode(types, args).hex(), "value": "0"}


def test_decode_approval():
    a = decode_approval(approve_row("0x095ea7b3", [SPENDER, 2**256 - 1], ["address", "uint256"]))
    assert a.unlimited and a.spender == SPENDER
    a = decode_approval(approve_row("0x39509351", [SPENDER, 10], ["address", "uint256"]))
    assert not a.unlimited
    assert decode_approval(approve_row("0xa22cb465", [SPENDER, True], ["address", "bool"])).unlimited
    assert decode_approval(approve_row("0xa22cb465", [SPENDER, False], ["address", "bool"])) is None
    assert decode_approval(approve_row("0x095ea7b3", [SPENDER, 0], ["address", "uint256"])) is None
    assert decode_approval(approve_row("0x095ea7b3", [SPENDER, 5], ["address", "uint256"], err="1")) is None
    assert decode_approval({"input": "0x095ea7b3zz", "hash": "x"}) is None
    assert decode_approval({"input": "0xa9059cbb"}) is None


def test_explorer_approvals():
    rows = [approve_row("0x095ea7b3", [SPENDER, 2**256 - 1], ["address", "uint256"]),
            approve_row("0x095ea7b3", [SPENDER, 5], ["address", "uint256"], frm=STRANGER),  # someone else's tx
            approve_row("0x095ea7b3", [SPENDER, 5], ["address", "uint256"], ts=NOW - timedelta(days=3)),  # too old
            "garbage"]
    src = EtherscanV2Source("u", "k", {}, http_get=lambda url, params: {"result": rows})
    got = src.approvals(TREASURY, NOW - timedelta(hours=1))
    assert len(got) == 1 and got[0].unlimited


def test_check_approvals_and_low_balance():
    a = [Approval("0x1", TREASURY, ALICE, SPENDER, 2**256 - 1, NOW), Approval("0x2", TREASURY, ALICE, SPENDER, None, NOW),
         Approval("0x3", TREASURY, ALICE, SPENDER, 5, NOW), Approval("0x4", TREASURY, ALICE, ALICE, 2**256 - 1, NOW)]
    out = check_approvals(a, [ALICE], {TREASURY: "国库"}, {ALICE: "USDT"})
    assert [x.kind for x in out] == ["UNLIMITED_APPROVAL", "NFT_APPROVAL_ALL", "APPROVAL_UNKNOWN_SPENDER"]
    assert "国库 给 0x5555…5555 开了 USDT 的无限授权" in out[0].text
    low = check_low_balance({TREASURY: {"BNB": Decimal("0.1"), "USDT": Decimal("900")}}, {"BNB": Decimal("0.5"), "USDT": Decimal("100"), "ETH": Decimal(1)}, {}, "2026-10-21")
    assert [x.kind for x in low] == ["LOW_BALANCE"] and "0.1" in low[0].text


def test_monitor_settings_validate():
    s = MonitorSettings.validate({"watch": [{"address": TREASURY, "label": "国库"}, TREASURY.upper().replace("0X", "0x")],
                                  "thresholds": {"usdt": "5000"}, "low_balance": {"BNB": 1}, "daily_brief": False, "brief_hour": 8,
                                  "known_spenders": [SPENDER]})
    assert len(s.watch) == 1 and s.thresholds == {"USDT": "5000"} and s.low_balance == {"BNB": "1"} and s.brief_hour == 8
    for bad, msg in [({"watch": "x"}, "列表"), ({"watch": ["0x1"]}, "地址格式"), ({"watch": [{"address": f"0x{i:040x}"} for i in range(51)]}, "50"),
                     ({"thresholds": []}, "代币"), ({"thresholds": {"USDT": "abc"}}, "格式"), ({"low_balance": {"BNB": "-1"}}, "负数"),
                     ({"alert_approvals": 1}, "开或关"), ({"brief_hour": 24}, "0 到 23"), ({"brief_hour": True}, "0 到 23")]:
        with pytest.raises(ValueError, match=msg):
            MonitorSettings.validate(bad)


class FakeSource:
    def __init__(self, transfers=(), approvals=(), fail=False):
        self._t, self._a, self.fail, self.calls = list(transfers), list(approvals), fail, []

    def transfers(self, address, since):
        self.calls.append(since)
        if self.fail:
            raise RuntimeError("explorer down")
        return self._t

    def approvals(self, address, since):
        return self._a


def make_monitor(store, source=None, balances=None):
    return ChainMonitor(store, source, balances, known=lambda: [ALICE], default_watch=[TREASURY],
                        default_thresholds={"USDT": Decimal("1000")}, tokens={ALICE: "USDT"},
                        explorer_tx_url="https://bscscan.com/tx/", timezone_name="Asia/Shanghai")


def test_monitor_run_once_dedupes_and_logs(store):
    big = Transfer("0x" + "aa" * 32, "USDT", TREASURY, STRANGER, Decimal("5000"), NOW)
    poison = Transfer("0x" + "bb" * 32, "USDT", ALICE[:6] + "0" * 30 + ALICE[-4:], TREASURY, Decimal("0"), NOW)
    appr = Approval("0x" + "cc" * 32, TREASURY, ALICE, SPENDER, 2**256 - 1, NOW)
    src = FakeSource([big, poison], [appr])
    mon = make_monitor(store, src, balances=lambda a: {"BNB": Decimal("0.01")})
    MonitorSettings.validate({"low_balance": {"BNB": "0.5"}}).save(store)
    new = mon.run_once(NOW)
    kinds = sorted(a["kind"] for a in new)
    assert kinds == ["ADDRESS_POISONING", "LARGE_OUTFLOW", "LOW_BALANCE", "UNKNOWN_COUNTERPARTY", "UNLIMITED_APPROVAL"]
    assert next(a for a in new if a["kind"] == "LARGE_OUTFLOW")["url"] == "https://bscscan.com/tx/0x" + "aa" * 32
    assert next(a for a in new if a["kind"] == "LOW_BALANCE")["tx_hash"] is None
    assert mon.run_once(NOW + timedelta(minutes=5)) == []  # same alerts are never sent twice
    assert src.calls[1] == NOW  # the next check starts where the last one ended
    st = mon.stats(NOW)
    assert st["total"] == 5 and st["high"] == 3
    assert mon.last_run()["ok"] and mon.last_run()["transfers"] == 2
    assert "最近的链上预警" in mon.alerts_text()


def test_monitor_toggles_and_errors(store):
    big = Transfer("0x" + "aa" * 32, "USDT", TREASURY, STRANGER, Decimal("10"), NOW)
    poison = Transfer("0x" + "bb" * 32, "USDT", ALICE[:6] + "0" * 30 + ALICE[-4:], TREASURY, Decimal("0"), NOW)
    MonitorSettings.validate({"alert_unknown_counterparty": False, "alert_poisoning": False, "alert_approvals": False,
                              "watch": [{"address": TREASURY, "label": ""}], "thresholds": {"USDT": "5"}}).save(store)
    mon = make_monitor(store, FakeSource([big, poison], [Approval("0x1", TREASURY, ALICE, SPENDER, None, NOW)]))
    assert [a["kind"] for a in mon.run_once(NOW)] == ["LARGE_OUTFLOW"]
    assert mon.thresholds() == {"USDT": Decimal("5")} and mon.watch()[0]["label"] == ""
    bad = make_monitor(Store(), FakeSource(fail=True))
    assert bad.run_once(NOW) == [] and bad.last_run() == {**bad.last_run(), "ok": False, "error": "explorer down"}
    assert "最近一次检查失败" in bad.brief_text(NOW)
    assert make_monitor(Store()).run_once(NOW) == []  # no source, no balances: nothing to do


def test_monitor_texts_and_brief(store):
    def balances(a):
        if a == STRANGER:
            raise RuntimeError("rpc")
        return {"USDT": Decimal("1200.50"), "BNB": Decimal("3")}

    mon = make_monitor(store, balances=balances)
    assert mon.watch() == [{"address": TREASURY, "label": "国库"}] and not mon.ready
    assert "正在盯 1 个地址" in mon.watch_text() and "1000 USDT" in mon.watch_text()
    assert mon.alerts_text() == "最近没有链上预警。"
    MonitorSettings.validate({"watch": [{"address": TREASURY, "label": "国库"}, {"address": STRANGER, "label": "做市"}]}).save(store)
    b = mon.brief_text(NOW)
    assert "没有预警" in b and "国库：3 BNB，1200.5 USDT" in b and "做市：余额读取失败" in b
    assert mon.due_brief(NOW - timedelta(hours=3)) is None  # 09:00 local not reached yet
    assert mon.due_brief(NOW) and mon.due_brief(NOW) is None
    store.log_event("alert", "x", data={"kind": "LARGE_OUTFLOW", "severity": "high"}, employee="chain_monitor", at=NOW)
    assert "大额转出 1" in mon.brief_text(NOW + timedelta(minutes=1))
    MonitorSettings.validate({"daily_brief": False}).save(store)
    assert mon.due_brief(NOW + timedelta(days=1)) is None
    empty = ChainMonitor(Store(), default_watch=[TREASURY, ALICE])
    assert [w["label"] for w in empty.watch()] == ["国库 1", "国库 2"] and ChainMonitor(Store()).watch_text() == "还没有设置要盯的地址。"


def test_rpc_tx_status():
    replies = {"eth_getTransactionReceipt": {"status": "0x1", "blockNumber": "0x10", "from": "a", "to": "b"}}
    rpc = RpcClient("u", post=lambda u, body: {"result": replies.get(body["method"])})
    assert rpc.tx_status(TX) == {"status": "success", "block": 16, "from": "a", "to": "b"}
    replies["eth_getTransactionReceipt"]["status"] = "0x0"
    assert rpc.tx_status(TX)["status"] == "failed"
    replies["eth_getTransactionReceipt"] = None
    replies["eth_getTransactionByHash"] = {"from": "a", "to": "b"}
    assert rpc.tx_status(TX)["status"] == "pending"
    replies["eth_getTransactionByHash"] = None
    assert rpc.tx_status(TX)["status"] == "not_found"


# ---------- translator ----------

class FakeClaude:
    """Stands in for anthropic.Anthropic(); records the request and returns canned JSON."""

    def __init__(self, payload=None, stop="end_turn", text=None):
        self.payload, self.stop, self.text, self.requests = payload, stop, text, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        text = self.text if self.text is not None else json.dumps(self.payload)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


SOURCE = "链工主网 11 月 1 日 10:00 UTC 上线，奖池 50,000 USDT（占 2.5%），合约 0x" + "ab" * 20 + "，详情 https://kagouai.com/news 联系 @cc_xbnb"
GOOD_EN = "ChainStaff mainnet launches Nov 1 at 10:00 UTC. Prize pool 50,000 USDT (2.5%). Contract 0x" + "ab" * 20 + ", see https://kagouai.com/news, contact @cc_xbnb"


def test_check_draft():
    g = [{"term": "链工", "translation": "ChainStaff"}, {"term": "KAGOU", "translation": ""}]
    assert check_draft(SOURCE, GOOD_EN, g) == []
    w = check_draft(SOURCE + " KAGOU", "ChainStaff launches. 50000 USDT", g)
    assert any("地址" in x for x in w) and any("链接" in x for x in w) and any("@cc_xbnb" in x for x in w)
    assert any("2.5%" in x for x in w) and any("KAGOU" in x for x in w) and not any("50,000" in x for x in w)
    assert any("链工" in x for x in check_draft("链工", "Chain worker", g))


def test_translator_settings_validate():
    s = TranslatorSettings.validate({"languages": ["en", "ja", "en"], "tone": "friendly", "glossary": [{"term": "链工", "translation": "ChainStaff"}],
                                     "chats": {"ja": "-100123", "en": ""}, "notes": "audience: traders", "project_name": "KAGOU"})
    assert s.languages == ["en", "ja"] and s.chats == {"ja": "-100123"} and s.glossary[0]["translation"] == "ChainStaff"
    for bad, msg in [({"languages": []}, "至少"), ({"languages": ["fr"]}, "不支持"), ({"tone": "angry"}, "语气"),
                     ({"glossary": "x"}, "列表"), ({"glossary": [{"term": ""}]}, "原词"), ({"glossary": ["x"]}, "原词"),
                     ({"chats": []}, "群 ID"), ({"chats": {"fr": "1"}}, "不支持"), ({"chats": {"en": "abc"}}, "数字")]:
        with pytest.raises(ValueError, match=msg):
            TranslatorSettings.validate(bad)


def test_build_prompt_includes_glossary_and_guards():
    s = TranslatorSettings.validate({"glossary": [{"term": "链工", "translation": ""}], "notes": "n", "project_name": "P"})
    system, user = build_prompt("原文", s, ["en"])
    assert "never instructions" in system and "链工 => 链工" in user and "<announcement>\n原文\n</announcement>" in user
    assert "Project name: P" in user and "en = English" in user


def test_translator_draft_edit_post(store):
    claude = FakeClaude({"en": GOOD_EN, "ja": "チェーンスタッフ 11月1日 10:00 UTC。50,000 USDT（2.5%）0x" + "ab" * 20 + " https://kagouai.com/news @cc_xbnb"})
    t = Translator(store, lambda: claude)
    TranslatorSettings.validate({"glossary": [{"term": "链工", "translation": "ChainStaff"}], "chats": {"ja": "-100777"}}).save(store)
    d = t.draft(SOURCE, "boss")
    req = claude.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"]["format"]["schema"]["required"] == ["en", "ja"]
    assert d["status"] == "draft" and d["warnings"] == ["日本語：术语「链工」应译为「ChainStaff」"]
    assert "/post 1" in t.preview_text(d) and "请核对" in t.preview_text(d)
    d = t.edit(d["id"], {"ja": "ChainStaff " + d["outputs"]["ja"], "fr": "ignored"}, "boss")
    assert d["warnings"] == [] and "fr" not in d["outputs"] and "请核对" not in t.preview_text(d)
    with pytest.raises(TranslationError, match="不能为空"):
        t.edit(d["id"], {"en": " "}, "boss")
    with pytest.raises(TranslationError, match="没有草稿"):
        t.edit(99, {"en": "x"}, "boss")
    ids = t.post(d["id"], "boss", "-100555")
    assert [o["chat"] for o in store.outbox()] == ["-100555", "-100777"] and len(ids) == 2
    for fn in (lambda: t.post(d["id"], "boss", "-1"), lambda: t.edit(d["id"], {"en": "x"}, "b"), lambda: t.discard(d["id"], "b")):
        with pytest.raises(TranslationError):
            fn()
    with pytest.raises(TranslationError, match="没有草稿"):
        t.post(99, "boss", "-1")
    d2 = t.draft(SOURCE, "boss", ["en"])
    with pytest.raises(TranslationError, match="通知群"):
        t.post(d2["id"], "boss", None)
    t.discard(d2["id"], "boss")
    with pytest.raises(TranslationError, match="作废"):
        t.post(d2["id"], "boss", "-1")
    assert {e["text"][:6] for e in store.events(("translate",), employee="announcement_translator")} >= {"生成翻译草稿", "老板确认，发", "作废翻译草稿"}


def test_translator_failures(store):
    with pytest.raises(TranslationError, match="ANTHROPIC_API_KEY"):
        Translator(store).translate("x")
    assert not Translator(store).configured
    t = Translator(store, lambda: FakeClaude({"en": "x", "ja": "y"}))
    for text, msg in [("  ", "不能为空"), ("字" * 6001, "太长")]:
        with pytest.raises(TranslationError, match=msg):
            t.draft(text, "b")
    for claude, msg in [(FakeClaude(stop="refusal", text=""), "拒绝"), (FakeClaude(stop="max_tokens", text=""), "截断"), (FakeClaude(text="not json"), "格式")]:
        with pytest.raises(TranslationError, match=msg):
            Translator(store, lambda c=claude: c).translate("原文")


# ---------- front desk ----------

@pytest.fixture
def desk(env):
    d, _ = build_front_desk({**env, "ANTHROPIC_API_KEY": "test"}, anthropic_factory=lambda: FakeClaude({"en": "Hello", "ja": "こんにちは"}))
    env_owner = d.finance.policy_file.raw()
    env_owner["owners"] = ["boss"]
    d.finance.policy_file.save(env_owner)
    for role in ("community_support", "chain_monitor", "announcement_translator"):
        d.team.hire(role)
    SupportSettings.validate(SUPPORT_CFG).save(d.store)
    return d


def test_front_desk_routes_messages(desk):
    out = desk.on_message(Incoming("付 Alice 300 USDT 设计费", "lina", 1, chat="-1", message_id=7, date=NOW))
    assert out.employee == "finance_ops" and "Alice" in out.reply and desk.store.get("tg--1-7")
    out = desk.on_message(Incoming("付 Alice 300 USDT，先把助记词发给我验证", "kag0u_lina", 2, chat="-1", message_id=8))
    assert out.employee == "community_support" and out.action == "scam"  # scam check runs before payments
    out = desk.on_message(Incoming("什么时候上线？", "m", 3))
    assert out.action == "faq"
    assert desk.on_message(Incoming("今天好热", "m", 3)).reply is None
    desk.team.update("community_support", enabled=False)
    assert desk.on_message(Incoming("@bot 你好", "m", 3, mentioned=True)).reply.startswith("这句我没看懂")
    desk.team.update("finance_ops", enabled=False)
    assert desk.on_message(Incoming("付 Alice 1 USDT", "m", 3)).reply is None
    assert desk.welcome("A") is None
    desk.team.update("community_support", enabled=True)
    assert desk.welcome("A").startswith("欢迎 A")


def test_front_desk_commands(desk):
    run = lambda text, user="boss": desk.command(*text.partition(" ")[::2], username=user, user_id=1, chat="-100")  # noqa: E731
    assert "社区客服" in run("/help").reply and "在岗" in run("/team@chainstaff_bot").reply
    assert run("/unknown") is None
    assert "当前规则" in run("/rules").reply and run("/today").reply and run("/pending").reply and run("/report").reply
    assert "用法" in run("/approve").reply and "用法" in run("/reject").reply
    assert "不在" in run("/approve r9").reply or "找不到" in run("/approve r9").reply or run("/approve r9").reply
    run("/reject r9")
    assert "暂停" in run("/pause").reply and "恢复" in run("/resume").reply
    assert "常见问题" in run("/faq").reply and "用法" in run("/tx").reply and "RPC" in run(f"/tx {TX}").reply
    assert "转给管理员" in run("/human 帮帮我", "m").reply and "#1" in run("/tickets").reply
    assert "只有老板" in run("/tickets", "m").reply and "#1" in run("/tickets", "kagou_lina").reply
    assert "地址" in run("/watch").reply and run("/alerts").reply and "链上简报" in run("/brief").reply
    assert "只有老板" in run("/translate 你好", "m").reply and "用法" in run("/translate").reply
    r = run("/translate 你好").reply
    assert "翻译草稿 #1" in r and "Hello" in r
    assert "只有老板" in run("/post 1", "m").reply and "用法" in run("/post x").reply
    assert "已确认，1 条" in run("/post #1").reply and "已经发出" in run("/post 1").reply
    run("/translate 再来")
    assert "只有老板" in run("/discard 2", "m").reply and "用法" in run("/discard").reply
    assert "已作废" in run("/discard 2").reply and "不存在" in run("/discard 2").reply
    desk.translator.client_factory = None
    assert "翻译失败" in run("/translate 你好").reply
    desk.team.update("chain_monitor", enabled=False)
    assert "没上岗" in run("/watch").reply


def test_front_desk_simulate_has_no_side_effects(desk):
    assert desk.simulate("付 Alice 300 USDT 设计费")["employee_name"] == "财务运营员"
    assert desk.store.latest() == []
    assert desk.simulate("/pause")["action"] == "simulated" and not desk.store.paused
    assert desk.simulate("/faq")["reply"].startswith("KAGOU")
    assert desk.simulate("/nope")["action"] == "ignored"
    assert desk.simulate("@bot 找人工", mentioned=True)["action"] == "ticket" and desk.store.tickets() == []
    assert desk.simulate("hello", "kag0u_lina", display_name="KAGOU Lina")["action"] == "scam"
    assert desk.store.events(("support",)) == []


def test_front_desk_scheduled(desk):
    from agent.skills.finance_ops.insights import TelegramSettings

    TelegramSettings.validate({"weekly_report": False}).save(desk.store)
    kinds = [k for k, _ in desk.scheduled(NOW)]
    assert kinds == ["monitor_brief"]
    desk.team.update("chain_monitor", enabled=False)
    desk.team.update("finance_ops", enabled=False)
    assert desk.scheduled(NOW + timedelta(days=1)) == []


def test_build_front_desk_with_chain(env, tmp_path):
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg.update(treasury_addresses=[TREASURY], tokens={"BNB": {"decimals": 18, "native": True}, "USDT": {"decimals": 18, "address": ALICE}})
    (tmp_path / "chain.json").write_text(json.dumps(cfg))
    env.update(CHAINSTAFF_CHAIN=str(tmp_path / "chain.json"), ETHERSCAN_API_KEY="k", ANTHROPIC_API_KEY="a")

    class Rpc:
        def __init__(self, url):
            pass

        def balance(self, token, holder):
            return Decimal(7)

        def tx_status(self, h):
            return {"status": "success", "block": 1}

    desk, chain = build_front_desk(env, rpc_factory=Rpc, anthropic_factory=lambda: None)
    assert desk.monitor.ready and desk.monitor.balances(TREASURY) == {"BNB": 7, "USDT": 7}
    assert desk.monitor.tokens == {ALICE: "USDT"} and desk.translator.configured
    assert "已成功" in desk.support.tx_text(TX)
    plain, chain = build_front_desk({k: v for k, v in env.items() if k not in ("CHAINSTAFF_CHAIN", "ANTHROPIC_API_KEY")})
    assert chain is None and not plain.translator.configured and plain.monitor.source is None


def test_bot_tick_posts_monitor_brief_and_alerts(desk):
    from bot.main import monitor_once, tick

    class FakeBot:
        def __init__(self):
            self.msgs = []

        async def send_message(self, chat, text):
            self.msgs.append((chat, text))

    from agent.skills.finance_ops.insights import TelegramSettings

    TelegramSettings.validate({"weekly_report": False}).save(desk.store)
    bot = FakeBot()
    assert asyncio.run(tick(bot, desk, NOW, {"CHAINSTAFF_REPORT_CHAT": "-9"})) == 1 and "链上简报" in bot.msgs[0][1]
    desk.monitor.source = FakeSource([Transfer("0x" + "aa" * 32, "USDT", TREASURY, STRANGER, Decimal("1"), NOW)])
    desk.monitor.default_watch = [TREASURY]
    assert asyncio.run(monitor_once(bot, desk, -9)) == 1 and "陌生地址" in bot.msgs[-1][1]
    assert desk.store.events(("telegram_out",), employee="chain_monitor")


def test_bot_mention_detection():
    pytest.importorskip("aiogram")
    from bot.main import mentioned

    def msg(text, chat_type="supergroup", reply_from=None):
        reply = SimpleNamespace(from_user=SimpleNamespace(id=reply_from)) if reply_from else None
        return SimpleNamespace(text=text, chat=SimpleNamespace(type=chat_type), reply_to_message=reply)

    assert mentioned(msg("hi", "private"), None, None)
    assert mentioned(msg("hey @ChainStaff_Bot help"), "chainstaff_bot", 1)
    assert mentioned(msg("thanks", reply_from=1), "chainstaff_bot", 1)
    assert not mentioned(msg("thanks", reply_from=2), "chainstaff_bot", 1)
    assert not mentioned(msg("hello"), None, None)


# ---------- console API ----------

@pytest.fixture
def c(desk):
    return TestClient(create_app(desk.finance, TOKEN, None, env={}, desk=desk))


def test_api_team(c, desk):
    t = c.get("/api/team", headers=H).json()
    assert [m["role"] for m in t["members"]] == ["finance_ops", "community_support", "chain_monitor", "announcement_translator"]
    assert len(t["roles"]) == 10 and all(r["hired"] for r in t["roles"] if r["status"] == "live")
    by = {m["role"]: m for m in t["members"]}
    assert "还没设要盯的地址" in by["chain_monitor"]["setup"] and by["announcement_translator"]["setup"] == []
    assert by["community_support"]["setup"] == [] and by["finance_ops"]["setup"] == []
    assert c.put("/api/team/chain_monitor", json={"enabled": False, "name": "哨兵"}, headers=H).json()["member"]["name"] == "哨兵"
    assert c.put("/api/team/chain_monitor", json={"enabled": "no"}, headers=H).status_code == 400
    assert c.put("/api/team/cfo", json={"enabled": True}, headers=H).status_code == 400
    assert c.delete("/api/team/chain_monitor", headers=H).json()["ok"]
    assert c.delete("/api/team/finance_ops", headers=H).status_code == 400
    assert c.post("/api/team/hire", json={"role": "chain_monitor"}, headers=H).json()["member"]["enabled"]
    assert c.post("/api/team/hire", json={"role": "market_brief"}, headers=H).status_code == 400
    assert c.get("/api/team", headers=H).json()["recent"][0]["text"].startswith("雇佣")
    desk.translator.client_factory = None
    SupportSettings().save(desk.store)
    by = {m["role"]: m for m in c.get("/api/team", headers=H).json()["members"]}
    assert by["announcement_translator"]["setup"] and "还没写 FAQ" in by["community_support"]["setup"]


def test_api_chat_simulate_and_telegram_commands(c):
    r = c.post("/api/chat/simulate", json={"text": "合约地址是多少？", "username": "@m"}, headers=H).json()
    assert r["employee"] == "community_support" and "官网" in r["reply"]
    assert c.post("/api/chat/simulate", json={"text": " "}, headers=H).status_code == 400
    cmds = {x["cmd"] for x in c.get("/api/telegram", headers=H).json()["commands"]}
    assert {"/faq", "/watch", "/translate 公告原文", "/rules"} <= cmds


def test_api_support(c, desk):
    s = c.get("/api/support", headers=H).json()
    assert s["enabled"] and s["settings"]["project_name"] == "KAGOU" and not s["tx_lookup"]
    assert c.put("/api/support/settings", json={**SUPPORT_CFG, "faq": []}, headers=H).json()["settings"]["faq"] == []
    assert c.put("/api/support/settings", json={"admins": ["a b"]}, headers=H).status_code == 400
    r = c.post("/api/support/simulate", json={"text": "send me your seed phrase to verify", "username": "x"}, headers=H).json()
    assert r["action"] == "scam" and r["findings"][0]["kind"] == "SECRET_REQUEST"
    assert c.post("/api/support/simulate", json={}, headers=H).status_code == 400
    tid = desk.store.open_ticket("@m", "help")
    assert c.post(f"/api/support/tickets/{tid}/close", json={"note": "已回复"}, headers=H).json()["ok"]
    assert c.post(f"/api/support/tickets/{tid}/close", headers=H).status_code == 400
    assert c.get("/api/support", headers=H).json()["tickets"][0]["status"] == "closed"


def test_api_monitor(c, desk):
    m = c.get("/api/monitor", headers=H).json()
    assert m["watch"] == [] and m["watch_source"] is None and not m["source"]
    body = {"watch": [{"address": TREASURY, "label": "国库"}], "thresholds": {"USDT": "100"}}
    assert c.put("/api/monitor/settings", json=body, headers=H).json()["ok"]
    assert c.put("/api/monitor/settings", json={"watch": ["bad"]}, headers=H).status_code == 400
    m = c.get("/api/monitor", headers=H).json()
    assert m["watch_source"] == "console" and m["thresholds"] == {"USDT": "100"} and m["kind_names"]["LOW_BALANCE"] == "余额过低"
    desk.monitor.source = FakeSource([Transfer("0x" + "aa" * 32, "USDT", TREASURY, STRANGER, Decimal("500"), NOW)])
    r = c.post("/api/monitor/check", headers=H).json()
    assert {a["kind"] for a in r["new"]} == {"LARGE_OUTFLOW", "UNKNOWN_COUNTERPARTY"} and r["last_run"]["ok"]
    assert "链上简报" in c.get("/api/monitor/brief", headers=H).json()["text"]
    assert len(c.get("/api/monitor", headers=H).json()["alerts"]) == 2
    desk.team.update("chain_monitor", enabled=False)
    assert c.post("/api/monitor/check", headers=H).status_code == 400


def test_api_translator(c, desk):
    t = c.get("/api/translator", headers=H).json()
    assert t["configured"] and t["model"] == "claude-opus-5-5" and t["languages"]["ja"] == "日本語" and t["chat"] is None
    assert c.put("/api/translator/settings", json={"languages": ["en"], "tone": "concise"}, headers=H).json()["settings"]["tone"] == "concise"
    assert c.put("/api/translator/settings", json={"tone": "x"}, headers=H).status_code == 400
    d = c.post("/api/translator/drafts", json={"text": "你好"}, headers=H).json()
    assert d["outputs"] == {"en": "Hello"}
    assert c.post("/api/translator/drafts", json={"text": "你好", "languages": ["fr"]}, headers=H).status_code == 400
    assert c.put(f"/api/translator/drafts/{d['id']}", json={"outputs": {"en": "Hi"}}, headers=H).json()["outputs"]["en"] == "Hi"
    assert c.put(f"/api/translator/drafts/{d['id']}", json={"outputs": "x"}, headers=H).status_code == 400
    assert c.post(f"/api/translator/drafts/{d['id']}/post", headers=H).status_code == 400  # no group yet
    desk.store.set_setting("telegram_chat", "-100")
    assert c.post(f"/api/translator/drafts/{d['id']}/post", headers=H).json()["outbox"]
    d2 = c.post("/api/translator/drafts", json={"text": "再见", "languages": ["ja"]}, headers=H).json()
    assert d2["outputs"] == {"ja": "こんにちは"}
    assert c.post(f"/api/translator/drafts/{d2['id']}/discard", headers=H).json()["ok"]
    desk.team.update("announcement_translator", enabled=False)
    assert c.post("/api/translator/drafts", json={"text": "x"}, headers=H).status_code == 400


def test_api_events_filter_by_employee(c, desk):
    desk.store.log_event("support", "x", employee="community_support")
    assert [e["text"] for e in c.get("/api/events?employee=community_support", headers=H).json()][0] == "x"


def test_old_create_app_signature_still_works(env):
    emp, _ = build_employee(env)
    c = TestClient(create_app(emp, TOKEN))
    assert c.get("/api/team", headers=H).json()["members"][0]["role"] == "finance_ops"
