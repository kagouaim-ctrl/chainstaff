from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from agent.rules import Decision, Outcome, PaymentRequest
from agent.skills.finance_ops.alerts import EtherscanV2Source, Transfer, check_transfers
from agent.skills.finance_ops.reports import last_full_week, week_bounds, weekly_report
from agent.store import Store

TREASURY = "0x7reas000000000000000000000000000000000001".replace("7reas", "7ea50")
ALICE = "0xA11ce00000000000000000000000000000007e2D"
STRANGER = "0x4c8e0000000000000000000000000000000091Aa"
POISON = "0xA11ce99999999999999999999999999999997e2D"  # same first 6 / last 4 as ALICE
NOW = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)


def t(frm, to, amt, token="USDT", h="0x1"):
    return Transfer(h, token, frm, to, Decimal(amt), NOW)


TH = {"USDT": Decimal("10000")}


def test_large_outflow_to_known():
    a = check_transfers([t(TREASURY, ALICE, "40000")], [TREASURY], [ALICE], TH)
    assert [x.kind for x in a] == ["LARGE_OUTFLOW"]
    assert "40000" in a[0].text


def test_small_outflow_to_known_is_quiet():
    assert check_transfers([t(TREASURY, ALICE, "100")], [TREASURY], [ALICE], TH) == []


def test_outflow_to_unknown():
    kinds = [x.kind for x in check_transfers([t(TREASURY, STRANGER, "20000")], [TREASURY], [ALICE], TH)]
    assert kinds == ["LARGE_OUTFLOW", "UNKNOWN_COUNTERPARTY"]


def test_untracked_token_threshold_absent():
    kinds = [x.kind for x in check_transfers([t(TREASURY, ALICE, "999", "BNB")], [TREASURY], [ALICE], TH)]
    assert kinds == []


def test_address_poisoning_inflow():
    a = check_transfers([t(POISON, TREASURY, "0")], [TREASURY], [ALICE], TH)
    assert [x.kind for x in a] == ["ADDRESS_POISONING"]
    assert a[0].key == "ADDRESS_POISONING:0x1"


def test_normal_inflow_is_quiet():
    assert check_transfers([t(STRANGER, TREASURY, "5")], [TREASURY], [ALICE], TH) == []
    assert check_transfers([t(ALICE, STRANGER, "5")], [TREASURY], [ALICE], TH) == []


def test_etherscan_source_parses_and_filters():
    usdt = "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd"
    ts_new, ts_old = int(NOW.timestamp()), int((NOW - timedelta(days=2)).timestamp())
    pages = {
        "tokentx": {"result": [
            {"hash": "0xa", "contractAddress": usdt.lower(), "tokenDecimal": "18", "from": TREASURY, "to": ALICE, "value": str(5 * 10**18), "timeStamp": str(ts_new)},
            {"hash": "0xspam", "contractAddress": "0x" + "9" * 40, "tokenDecimal": "18", "from": TREASURY, "to": ALICE, "value": "1", "timeStamp": str(ts_new)},
            {"hash": "0xold", "contractAddress": usdt, "tokenDecimal": "18", "from": TREASURY, "to": ALICE, "value": "1", "timeStamp": str(ts_old)},
        ]},
        "txlist": {"result": [
            {"hash": "0xb", "from": TREASURY, "to": STRANGER, "value": str(10**18), "timeStamp": str(ts_new), "isError": "0"},
            {"hash": "0xfail", "from": TREASURY, "to": STRANGER, "value": str(10**18), "timeStamp": str(ts_new), "isError": "1"},
            {"hash": "0xzero", "from": TREASURY, "to": STRANGER, "value": "0", "timeStamp": str(ts_new), "isError": "0"},
        ]},
    }
    seen = []

    def get(url, params):
        seen.append(params)
        return pages[params["action"]]

    src = EtherscanV2Source("https://api.example/v2/api?chainid=97", "KEY", {"USDT": usdt, "BNB": None}, http_get=get)
    out = src.transfers(TREASURY, NOW - timedelta(hours=1))
    assert [(x.tx_hash, x.token, x.amount) for x in out] == [("0xa", "USDT", Decimal(5)), ("0xb", "BNB", Decimal(1))]
    assert seen[0]["apikey"] == "KEY"


def test_etherscan_source_handles_error_payload():
    src = EtherscanV2Source("u", "k", {}, http_get=lambda url, params: {"status": "0", "result": "Max rate limit reached"})
    assert src.transfers(TREASURY, NOW) == []


def test_week_bounds():
    s, e = week_bounds(date(2026, 10, 22), "Asia/Shanghai")  # Thursday
    assert s.isoformat() == "2026-10-19T00:00:00+08:00" and e - s == timedelta(days=7)
    s2, _ = last_full_week(NOW, "Asia/Shanghai")
    assert s2.date() == date(2026, 10, 12)


def test_empty_report():
    s, e = week_bounds(date(2026, 10, 12), "UTC")
    assert "本周没有付款请求" in weekly_report([], s, e)


def test_report_categories_pending_and_names():
    st = Store()
    def add(rid, amt, outcome, cat="", payee=ALICE):
        r = PaymentRequest(rid, "@l", payee, amt, "USDT", NOW, category=cat, memo="m")
        st.record(r, Decision(rid, outcome, (), ("原因",) if outcome is Outcome.BLOCK else ()))
    add("a", "100", Outcome.AUTO_PAY, "设计"); st.mark_paid("a", "0x1")
    add("b", "900", Outcome.NEEDS_APPROVAL, "开发")
    add("c", "50", Outcome.BLOCK, payee=STRANGER)
    s, e = NOW - timedelta(days=1), NOW + timedelta(days=1)
    txt = weekly_report(st.between(s, e), s, e, {ALICE.lower(): "Alice"})
    assert "USDT：100" in txt and "设计 100" in txt
    assert "还有 1 笔等老板签字：b" in txt
    assert "0x4c8e…91aa" in txt and "原因" in txt
