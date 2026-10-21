"""Console v2: events, outbox, search, overview, payees, simulate, Telegram settings and scheduling."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import load_chain
from agent.runtime import build_employee
from agent.skills.finance_ops.insights import TelegramSettings, automation_rate, payee_stats, rules_diff
from agent.skills.finance_ops.schedule import due_messages
from agent.store import Store
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "config" / "policy.example.json").read_text())
TOKEN = "t" * 24
H = {"Authorization": "Bearer " + TOKEN}
NOW = datetime.now(timezone.utc).replace(microsecond=0)
ALICE = "0xa11ce00000000000000000000000000000007e2d"
UNKNOWN = "0x9f3c000000000000000000000000000000004a21"


@pytest.fixture
def env(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(EXAMPLE))
    return {"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")}


@pytest.fixture
def emp(env):
    e, _ = build_employee(env)
    e.handle_request("付 Alice 300 USDT 设计费", "@lina", "r1", NOW)
    e.handle_request("付 Bob 120 USDT 翻译", "@lina", "r2", NOW)
    e.handle_request("付 Alice 800 USDT 视频", "@lina", "r3", NOW)
    e.handle_request(f"付 {UNKNOWN} 1500 USDT 紧急", "@eve", "r4", NOW)
    return e


@pytest.fixture
def chain(tmp_path):
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg.update(safe_address="0x1111111111111111111111111111111111111111", treasury_addresses=["0x2222222222222222222222222222222222222222"])
    p = tmp_path / "chain.json"
    p.write_text(json.dumps(cfg))
    return load_chain(p)


@pytest.fixture
def c(emp, chain):
    return TestClient(create_app(emp, TOKEN, chain, env={"CHAINSTAFF_AGENT_ID": "2648", "CHAINSTAFF_WALLET_ADDRESS": "0xBd6d546183d10d65b6e9e1706BF34E418eAbc5Ae"}))


# ---------- store ----------

def test_events_filter_and_paging():
    s = Store()
    ids = [s.log_event("audit", f"a{i}", "boss", {"i": i}) for i in range(3)]
    s.log_event("alert", "x")
    assert [e["text"] for e in s.events(("audit",))] == ["a2", "a1", "a0"]
    assert [e["text"] for e in s.events(("audit",), before_id=ids[2])] == ["a1", "a0"]
    assert s.events(limit=1)[0]["kind"] == "alert" and s.events(limit=1)[0]["data"] is None
    assert s.events(("audit",), limit=1)[0]["data"] == {"i": 2}


def test_outbox():
    s = Store()
    a, b = s.enqueue("-100", "hi"), s.enqueue("-100", "bad")
    s.mark_sent(a)
    s.mark_sent(b, "boom")
    assert s.outbox() == []
    rows = s.outbox(unsent_only=False)
    assert [r["id"] for r in rows] == [b, a] and rows[0]["error"] == "boom" and rows[1]["sent_at"]


def test_search_filters(emp):
    s = emp.store
    assert {r.request.request_id for r in s.search(status="pending_approval")} == {"r3"}
    assert {r.request.request_id for r in s.search(status="paid,blocked")} == {"r1", "r2", "r4"}
    assert {r.request.request_id for r in s.search(payee=ALICE.upper().replace("0X", "0x"))} == {"r1", "r3"}
    assert {r.request.request_id for r in s.search(q="翻译")} == {"r2"}
    assert len(s.search(token="usdt", start=NOW - timedelta(minutes=1), end=NOW + timedelta(minutes=1))) == 4
    assert s.search(start=NOW + timedelta(days=1)) == []


# ---------- insights ----------

def test_payee_stats_and_rate(emp):
    rows = emp.store.search()
    stats = {p["payee"]: p for p in payee_stats(rows)}
    assert stats[ALICE]["moved"] == {"USDT": "300"} and stats[ALICE]["requests"] == 2
    assert stats[UNKNOWN]["blocked"] == 1
    assert automation_rate(rows) == round(2 / 3, 4)
    assert automation_rate([]) is None


def test_rules_diff():
    before = json.loads(json.dumps(EXAMPLE))
    after = json.loads(json.dumps(EXAMPLE))
    after["single_limit"]["USDT"] = "100"
    after["daily_limit"].pop("BNB")
    after["payees"] = after["payees"][1:] + [{"name": "Zed", "address": "0x" + "ab" * 20}]
    after["payees"][0]["needs_approval"] = True
    after["read_only"] = True
    after["approval_categories"] = ["KOL", "a", "b", "c", "d"]
    after["timezone"] = "UTC"
    out = "\n".join(rules_diff(before, after))
    for want in ("USDT 单笔上限：500 → 100", "BNB 每日上限：1 → 无", "新增收款方 Zed", "删除收款方 Alice", "修改收款方 Bob", "试用期只读：关 → 开", "5 项", "时区：Asia/Shanghai → UTC"):
        assert want in out
    assert rules_diff(before, before) == []


def test_telegram_settings_roundtrip_and_validation():
    s = Store()
    assert TelegramSettings.load(s) == TelegramSettings()
    TelegramSettings.validate({"report_hour": 18, "quiet_auto_pay": True, "junk": 1}).save(s)
    t = TelegramSettings.load(s)
    assert t.report_hour == 18 and t.quiet_auto_pay
    for bad in ({"report_hour": 24}, {"report_hour": "9"}, {"weekly_report": 1}, {"report_weekday": True}):
        with pytest.raises(ValueError):
            TelegramSettings.validate(bad)


def test_due_messages_once(emp):
    tz = emp.policy.timezone
    from zoneinfo import ZoneInfo
    TelegramSettings.validate({"daily_digest": True, "digest_hour": 0, "pending_reminder_hours": 2, "report_hour": 0,
                               "report_weekday": NOW.astimezone(ZoneInfo(tz)).weekday()}).save(emp.store)
    kinds = [k for k, _ in due_messages(emp, NOW)]
    assert kinds == ["weekly_report", "daily_digest", "pending_reminder"]
    assert due_messages(emp, NOW + timedelta(minutes=1)) == []
    assert [k for k, _ in due_messages(emp, NOW + timedelta(hours=2))] == ["pending_reminder"]


def test_due_messages_off_by_default_until_monday(emp):
    TelegramSettings.validate({"weekly_report": False}).save(emp.store)
    assert due_messages(emp, NOW) == []


# ---------- employee ----------

def test_simulate_does_not_record(emp):
    r = emp.simulate("付 Alice 100 USDT 设计")
    assert r["outcome"] == "AUTO_PAY" and "试算" in r["reply"] and r["payee_name"] == "Alice"
    r = emp.simulate(f"付 {UNKNOWN} 10 USDT x")
    assert r["outcome"] == "BLOCK" and "NOT_WHITELISTED" in r["reasons"]
    assert emp.simulate("你好")["parsed"] is False
    assert len(emp.store.search()) == 4


def test_quiet_auto_pay(emp):
    TelegramSettings.validate({"quiet_auto_pay": True}).save(emp.store)
    text = emp.handle_request("付 Bob 1 USDT 杂费", "@lina", "r9", NOW).text
    assert text.startswith("✅ r9 已付") and "\n" not in text


def test_owner_actions_are_audited(emp):
    emp.pause("lina")
    emp.pause("your_telegram_username")
    emp.approve("r3", "your_telegram_username")
    texts = [e["text"] for e in emp.store.events(("audit",))]
    assert texts == ["签字确认 r3", "暂停所有付款", "非老板尝试暂停付款，已拒绝"]


# ---------- API ----------

def test_overview(c):
    o = c.get("/api/overview", headers=H).json()
    assert o["month"]["requests"] == 4 and o["month"]["moved"] == {"USDT": "420"}
    assert o["month"]["blocked_value"] == {"USDT": "1500"} and o["pending"] == 1
    assert len(o["series"]) == 14 and o["series"][-1]["auto"] == 2
    usdt = next(b for b in o["today"]["budget"] if b["token"] == "USDT")
    assert usdt == {"token": "USDT", "spent": "420", "limit": "2000"}
    assert o["top_payees"][0]["payee_name"] == "Alice" and o["status"]["live"] is False


def test_payments_filters_detail_csv(c):
    assert [r["request_id"] for r in c.get("/api/payments?status=blocked", headers=H).json()] == ["r4"]
    assert c.get("/api/payments?start=bad", headers=H).status_code == 400
    assert c.get("/api/payments?start=2000-01-01", headers=H).json()  # naive date is read as UTC
    c.post("/api/payments/r3/approve", headers=H)
    d = c.get("/api/payments/r3", headers=H).json()
    assert d["status"] == "approved" and d["history"][0]["text"] == "签字确认 r3"
    assert d["payee_url"].endswith("/address/" + ALICE) and d["tx_url"] is None
    assert c.get("/api/payments/nope", headers=H).status_code == 404
    res = c.get("/api/payments.csv?token=USDT", headers=H)
    assert res.headers["content-type"].startswith("text/csv") and "ledger-" in res.headers["content-disposition"]
    lines = res.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("created_at,request_id") and len(lines) == 5


def test_payees(c):
    p = c.get("/api/payees", headers=H).json()
    alice = next(x for x in p["whitelist"] if x["name"] == "Alice")
    assert alice["moved"] == {"USDT": "300"} and alice["requests"] == 2 and alice["url"]
    mia = next(x for x in p["whitelist"] if x["name"] == "Mia")
    assert mia["requests"] == 0
    assert [u["payee"] for u in p["unknown"]] == [UNKNOWN]
    assert p["risk_addresses"] == ["0xdead00000000000000000000000000000000beef"]


def test_rules_put_logs_diff(c):
    cfg = c.get("/api/rules", headers=H).json()
    cfg["single_limit"]["USDT"] = "250"
    res = c.put("/api/rules", headers=H, json=cfg).json()
    assert res["changes"] == ["USDT 单笔上限：500 → 250"]
    assert c.put("/api/rules", headers=H, json=cfg).json()["changes"] == []
    ev = c.get("/api/events?kind=audit", headers=H).json()
    assert len(ev) == 1 and "250" in ev[0]["text"]


def test_simulate_endpoint(c):
    assert c.post("/api/simulate", headers=H, json={"text": "付 Alice 700 USDT 视频"}).json()["outcome"] == "NEEDS_APPROVAL"
    assert c.post("/api/simulate", headers=H, json={"text": " "}).status_code == 400


def test_reports(c):
    assert "周报" in c.get("/api/report/weekly?weeks_ago=3", headers=H).json()["text"]
    assert "账本" in c.get("/api/report/today", headers=H).json()["text"]


def test_employee_and_treasury(c, emp):
    e = c.get("/api/employee", headers=H).json()
    assert e["agent_id"] == "2648" and e["wallet_url"].startswith("https://testnet.bscscan.com/address/0xBd6d")
    assert e["wallet_kind"] == "test" and e["network"] == "bsc-testnet" and e["safe_url"]
    assert next(x for x in e["limits"] if x["token"] == "USDT")["spent_today"] == "420"
    t = c.get("/api/treasury", headers=H).json()
    assert t["balance"] is None and t["monitoring"] is False and t["addresses"][0]["url"]
    emp.treasury_balance = lambda: {"USDT": Decimal("5")}
    assert c.get("/api/treasury", headers=H).json()["balance"] == {"USDT": "5"}
    emp.treasury_balance = lambda: 1 / 0
    assert "division" in c.get("/api/treasury", headers=H).json()["error"]


def test_employee_without_chain(emp):
    e = TestClient(create_app(emp, TOKEN, None, env={})).get("/api/employee", headers=H).json()
    assert e["network"] is None and e["wallet_url"] is None and "USDT" in e["tokens"]


def test_telegram_endpoints(c, emp):
    t = c.get("/api/telegram", headers=H).json()
    assert t["online"] is False and t["chat"] is None and t["settings"]["weekly_report"] is True
    assert any(x["cmd"] == "/pause" and x["owner_only"] for x in t["commands"])
    assert c.post("/api/telegram/send", headers=H, json={"kind": "report"}).status_code == 400  # no chat yet
    assert c.put("/api/telegram/chat", headers=H, json={"chat": "abc"}).status_code == 400
    assert c.put("/api/telegram/chat", headers=H, json={"chat": "-1001234"}).json()["chat"] == "-1001234"
    for body in ({"kind": "report"}, {"kind": "pending"}, {"kind": "custom", "text": "你好"}):
        assert c.post("/api/telegram/send", headers=H, json=body).json()["ok"]
    assert c.post("/api/telegram/send", headers=H, json={"kind": "custom", "text": ""}).status_code == 400
    assert c.post("/api/telegram/send", headers=H, json={"kind": "nope"}).status_code == 400
    assert len(emp.store.outbox()) == 3
    assert c.put("/api/telegram/settings", headers=H, json={"report_hour": 30}).status_code == 400
    assert c.put("/api/telegram/settings", headers=H, json={"report_hour": 18}).json()["settings"]["report_hour"] == 18
    emp.store.set_setting("telegram_bot", json.dumps({"username": "kagou_bot"}))
    emp.store.set_setting("telegram_heartbeat", datetime.now(timezone.utc).isoformat())
    t = c.get("/api/telegram", headers=H).json()
    assert t["online"] and t["bot"]["username"] == "kagou_bot" and t["chat_source"] == "console" and len(t["outbox"]) == 3


# ---------- bot ----------

class FakeBot:
    def __init__(self, fail_on=None):
        self.msgs, self.fail_on = [], fail_on

    async def send_message(self, chat, text):
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("chat not found")
        self.msgs.append((chat, text))


def test_tick_sends_scheduled_and_outbox(emp):
    from agent.front_desk import FrontDesk
    from bot.main import report_chat, tick

    desk = FrontDesk(emp)
    assert report_chat(emp, {}) is None
    emp.store.enqueue("-100", "来自控制台")
    emp.store.enqueue("-100", "坏消息")
    bot = FakeBot(fail_on="坏")
    TelegramSettings.validate({"weekly_report": False, "pending_reminder_hours": 1}).save(emp.store)
    assert asyncio.run(tick(bot, desk, NOW, {"CHAINSTAFF_REPORT_CHAT": "-200"})) == 2
    assert bot.msgs[0][0] == -200 and "提醒" in bot.msgs[0][1] and bot.msgs[1] == (-100, "来自控制台")
    assert emp.store.outbox() == []
    assert emp.store.get_setting("telegram_heartbeat") == NOW.isoformat()
    out = [e for e in emp.store.events(("telegram_out",))]
    assert len(out) == 2
    emp.store.set_setting("telegram_chat", "-300")
    assert report_chat(emp, {"CHAINSTAFF_REPORT_CHAT": "-200"}) == -300
    assert asyncio.run(tick(bot, desk, NOW, {})) == 0


def test_preview_commands(c):
    assert "/pause" in c.get("/api/preview/help", headers=H).json()["text"]
    assert "当前规则" in c.get("/api/preview/rules", headers=H).json()["text"]
    assert "r3" in c.get("/api/preview/pending", headers=H).json()["text"]
    assert c.get("/api/preview/nope", headers=H).status_code == 404


def test_store_is_safe_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    s = Store(tmp_path / "x.db")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: (s.log_event("audit", str(i)), s.events(limit=5), s.search(limit=5), s.get_setting("k")), range(200)))
    assert len(s.events(limit=500)) == 200
