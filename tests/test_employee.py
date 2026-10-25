from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.rules import Outcome
from agent.config import load_policy
from agent.skills.finance_ops.employee import FinanceOpsEmployee

CONFIG = Path(__file__).resolve().parent.parent / "config" / "policy.example.json"
NOW = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai


@pytest.fixture
def emp():
    policy, owners = load_policy(CONFIG)
    return FinanceOpsEmployee.fixed(policy, owners)


def send(emp, text, rid, at=NOW, who="@lina"):
    return emp.handle_request(text, who, rid, at)


def test_config_loads(emp):
    assert len(emp.policy.whitelist) == 3
    assert emp.owners == frozenset({"owner_username"})


def test_green_reply_says_dry_run(emp):
    r = send(emp, "付 Alice 300 USDT 设计费", "r1")
    assert r.decision.outcome is Outcome.AUTO_PAY
    assert "已付款" in r.text
    assert "测试模式" in r.text
    assert "300 / 2,000" in r.text


def test_amber_reply_lists_reason(emp):
    r = send(emp, "付 Bob 1,500 USDT 合约重构", "r1")
    assert r.decision.outcome is Outcome.NEEDS_APPROVAL
    assert "单笔上限" in r.text


def test_red_reply(emp):
    r = send(emp, "付 0x4c8e0000000000000000000000000000000091Aa 50 USDT 外包", "r1")
    assert r.decision.outcome is Outcome.BLOCK
    assert "没有发出任何交易" in r.text


def test_parse_error_asks_question(emp):
    r = send(emp, "付 Carol 100 USDT", "r1")
    assert r.decision is None
    assert "白名单里没有" in r.text


def test_token_assumed_is_echoed(emp):
    assert "按 USDT 处理" in send(emp, "付 Bob 20 开发", "r1").text


def test_redelivered_message_is_not_paid_twice(emp):
    assert send(emp, "付 Alice 300 USDT 设计费", "tg-1-5").decision.outcome is Outcome.AUTO_PAY
    again = send(emp, "付 Alice 300 USDT 设计费", "tg-1-5")
    assert again.decision.outcome is Outcome.BLOCK


def test_duplicate_next_day_blocked(emp):
    send(emp, "付 Alice 300 USDT 设计费", "r1")
    r = send(emp, "付 Alice 300 USDT 设计费", "r2", at=NOW + timedelta(days=1))
    assert r.decision.outcome is Outcome.BLOCK
    assert "相同金额" in r.text


def test_daily_limit_accumulates_through_employee(emp):
    for i, amt in enumerate(("500", "499", "498", "497")):
        assert send(emp, f"付 Bob {amt} USDT 开发", f"r{i}").decision.outcome is Outcome.AUTO_PAY
    r = send(emp, "付 Alice 10 USDT 设计", "r9")  # 1994 + 10 > 2000
    assert r.decision.outcome is Outcome.NEEDS_APPROVAL


def test_only_owner_can_pause(emp):
    assert "只有老板" in emp.pause("lina", 123)
    assert not emp.policy.paused
    assert "已暂停" in emp.pause("Owner_Username", 999)
    assert send(emp, "付 Alice 1 USDT 设计", "r1").decision.outcome is Outcome.BLOCK
    assert "只有老板" in emp.resume("lina")
    assert "已恢复" in emp.resume("@owner_username")
    assert send(emp, "付 Alice 1 USDT 设计", "r2").decision.outcome is Outcome.AUTO_PAY


def test_owner_by_numeric_id():
    policy, _ = load_policy(CONFIG)
    emp = FinanceOpsEmployee.fixed(policy, frozenset({"42"}))
    assert "已暂停" in emp.pause(None, 42)


def test_rules_text(emp):
    t = emp.rules_text()
    assert "USDT：单笔 ≤ 500，每日自动付 ≤ 2,000" in t
    assert "Mia（需审批）" in t
    assert "KOL" in t
    emp.pause("owner_username")
    assert "已暂停" in emp.rules_text()


def test_read_only_shows_in_rules():
    from dataclasses import replace
    policy, owners = load_policy(CONFIG)
    emp = FinanceOpsEmployee.fixed(replace(policy, read_only=True), owners)
    assert "只读" in emp.rules_text()
    assert send(emp, "付 Alice 1 USDT 设计", "r1").decision.outcome is Outcome.NEEDS_APPROVAL


def test_today_text(emp):
    assert "还没有付款请求" in emp.today_text(NOW)
    send(emp, "付 Alice 300 USDT 设计费", "r1")
    send(emp, "付 Bob 1500 USDT 重构", "r2")
    send(emp, "付 0x4c8e0000000000000000000000000000000091Aa 5 USDT", "r3")
    t = emp.today_text(NOW)
    assert "共 3 笔：自动付 1，待签 1，拦截 1" in t
    assert "还没有付款请求" in emp.today_text(NOW + timedelta(days=1))


def test_payment_prefix():
    from agent.front_desk import PAYMENT_PREFIX

    assert PAYMENT_PREFIX.match("付 Alice 300 USDT")
    assert PAYMENT_PREFIX.match("pay Bob 1 BNB")
    assert not PAYMENT_PREFIX.match("今天开会吗")
    assert not PAYMENT_PREFIX.match("payment is late")


# ---------- approvals, execution failures, Safe drafts, reports ----------

from decimal import Decimal  # noqa: E402

from agent.execution import DraftSafeProposer, TokenInfo  # noqa: E402
from agent.store import Store  # noqa: E402

OWNER = "owner_username"


def test_pending_approve_reject(emp):
    assert "没有等待签字" in emp.pending_text()
    send(emp, "付 Bob 1500 USDT 重构", "r1")
    send(emp, "付 Bob 900 USDT 审计", "r2")
    assert "r1" in emp.pending_text() and "r2" in emp.pending_text()
    assert "只有老板" in emp.approve("r1", "lina")
    assert "已记为老板签字" in emp.approve("r1", OWNER)
    assert "已驳回" in emp.reject("r2", OWNER)
    assert "只有老板" in emp.reject("r2", "lina")
    assert "当前状态是「🖊 老板已签」" in emp.approve("r1", OWNER)
    assert "找不到" in emp.approve("nope", OWNER)
    assert emp.store.get("r1").decided_by == OWNER
    assert "没有等待签字" in emp.pending_text()


def test_rejected_request_can_be_resent(emp):
    send(emp, "付 Bob 900 USDT 审计", "r1")
    emp.reject("r1", OWNER)
    r = send(emp, "付 Bob 900 USDT 审计", "r2")
    assert r.decision.outcome is Outcome.NEEDS_APPROVAL  # not a duplicate of a rejected one


def test_approved_request_counts_as_duplicate(emp):
    send(emp, "付 Bob 900 USDT 审计", "r1")
    emp.approve("r1", OWNER)
    assert send(emp, "付 Bob 900 USDT 审计", "r2").decision.outcome is Outcome.BLOCK


class Boom:
    live = True

    def pay(self, req):
        raise RuntimeError("insufficient funds")


class Ok:
    live = True

    def pay(self, req):
        return "0xabc"


def test_failed_payment_is_marked_and_does_not_use_budget():
    policy, owners = load_policy(CONFIG)
    emp = FinanceOpsEmployee.fixed(policy, owners, executor=Boom())
    r = send(emp, "付 Alice 300 USDT 设计费", "r1")
    assert "付款没有成功" in r.text and "insufficient funds" in r.text
    assert emp.store.get("r1").status == "failed"
    start, end = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert emp.store.auto_paid_total("USDT", start, end) == 0
    # A failed request isn't a duplicate source either.
    emp.executor = Ok()
    assert send(emp, "付 Alice 300 USDT 设计费", "r2").decision.outcome is Outcome.AUTO_PAY
    assert "共 2 笔：自动付 1，失败 1，待签 0，拦截 0" in emp.today_text(NOW)


def test_live_reply_links_explorer():
    policy, owners = load_policy(CONFIG)
    emp = FinanceOpsEmployee.fixed(policy, owners, executor=Ok(), explorer_tx_url="https://testnet.bscscan.com/tx/")
    r = send(emp, "付 Alice 300 USDT 设计费", "r1")
    assert "https://testnet.bscscan.com/tx/0xabc" in r.text
    assert "测试模式" not in r.text
    assert emp.store.get("r1").tx_hash == "0xabc"
    assert "测试模式" not in emp.rules_text()


def test_safe_draft_attached_to_amber():
    policy, owners = load_policy(CONFIG)
    usdt = TokenInfo("USDT", 18, "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd")
    prop = DraftSafeProposer(97, "0x1111111111111111111111111111111111111111", {"USDT": usdt}, lambda: 3)
    emp = FinanceOpsEmployee.fixed(policy, owners, proposer=prop)
    r = send(emp, "付 Bob 1500 USDT 重构", "r1")
    rec = emp.store.get("r1")
    assert rec.safe_tx_hash and rec.safe_tx_hash.startswith("0x") and rec.safe_tx_hash in r.text
    # Token without an address: proposal fails, reply falls back to manual payment.
    r2 = send(emp, "付 Bob 2 BNB 重构", "r2")
    assert "Safe 提案生成失败" in r2.text and "手动付款" in r2.text


def test_store_survives_restart(tmp_path):
    db = tmp_path / "l.db"
    policy, owners = load_policy(CONFIG)
    emp = FinanceOpsEmployee.fixed(policy, owners, store=Store(db))
    send(emp, "付 Alice 300 USDT 设计费", "tg-1-1")
    emp.pause(OWNER)
    emp2 = FinanceOpsEmployee.fixed(policy, owners, store=Store(db))
    assert emp2.policy.paused
    emp2.resume(OWNER)
    again = send(emp2, "付 Alice 300 USDT 设计费", "tg-1-1")
    assert "已经处理过" in again.text


def test_concurrent_insert_race_reports_processed(emp, monkeypatch):
    monkeypatch.setattr(emp.store, "has_request", lambda rid: False)  # simulate a race past the engine check
    send(emp, "付 Alice 300 USDT 设计费", "r1")
    assert "已经处理过" in send(emp, "付 Alice 300 USDT 设计费", "r1").text


def test_weekly_report_text(emp):
    last_week = NOW - timedelta(days=7)
    send(emp, "付 Alice 300 USDT 设计费", "r1", at=last_week)
    send(emp, "付 0x4c8e0000000000000000000000000000000091Aa 5 USDT", "r2", at=last_week)
    t = emp.weekly_report_text(NOW)
    assert "付款请求 2 笔" in t and "设计 300" in t and "被拦截" in t
    emp.treasury_balance = lambda: {"USDT": Decimal("13000")}
    assert "约可撑 10.0 个月" in emp.weekly_report_text(NOW)
    emp.treasury_balance = lambda: 1 / 0
    assert "国库余额" not in emp.weekly_report_text(NOW)


# ---------- background duties ----------

def test_treasury_alerts_are_logged_once(emp):
    from agent.background import BackgroundDuties
    from agent.skills.finance_ops.insights import TelegramSettings
    from agent.front_desk import FrontDesk
    from agent.skills.finance_ops.alerts import Transfer

    treasury = "0x2222222222222222222222222222222222222222"

    class Src:
        def transfers(self, addr, since):
            return [Transfer("0xt1", "USDT", treasury, "0x4c8e0000000000000000000000000000000091Aa", Decimal("50000"), NOW)]

    duties = BackgroundDuties(FrontDesk(emp), Src(), [treasury], {"USDT": Decimal("10000")})
    TelegramSettings.validate({"treasury_alerts": False}).save(emp.store)
    assert duties.run_once(NOW) == [] and emp.store.events(("alert",)) == []  # turned off in the console
    TelegramSettings.validate({"treasury_alerts": True}).save(emp.store)
    assert len(duties.run_once(NOW)) == 2
    assert duties.run_once(NOW) == []  # deduplicated, also across restarts (the keys are in the ledger)
    assert len(emp.store.events(("alert",))) == 2
    assert BackgroundDuties(FrontDesk(emp)).run_once(NOW) == []  # no transfer source: nothing to check
