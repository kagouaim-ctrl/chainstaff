from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.rules import Outcome
from agent.skills.finance_ops.employee import FinanceOpsEmployee, load_policy

CONFIG = Path(__file__).resolve().parent.parent / "config" / "policy.example.json"
NOW = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai


@pytest.fixture
def emp():
    policy, owners = load_policy(CONFIG)
    return FinanceOpsEmployee(policy, owners)


def send(emp, text, rid, at=NOW, who="@lina"):
    return emp.handle_request(text, who, rid, at)


def test_config_loads(emp):
    assert len(emp.policy.whitelist) == 3
    assert emp.owners == frozenset({"your_telegram_username"})


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
    assert "已暂停" in emp.pause("Your_Telegram_Username", 999)
    assert send(emp, "付 Alice 1 USDT 设计", "r1").decision.outcome is Outcome.BLOCK
    assert "只有老板" in emp.resume("lina")
    assert "已恢复" in emp.resume("@your_telegram_username")
    assert send(emp, "付 Alice 1 USDT 设计", "r2").decision.outcome is Outcome.AUTO_PAY


def test_owner_by_numeric_id():
    policy, _ = load_policy(CONFIG)
    emp = FinanceOpsEmployee(policy, frozenset({"42"}))
    assert "已暂停" in emp.pause(None, 42)


def test_rules_text(emp):
    t = emp.rules_text()
    assert "USDT：单笔 ≤ 500，每日自动付 ≤ 2,000" in t
    assert "Mia（需审批）" in t
    assert "KOL" in t
    emp.pause("your_telegram_username")
    assert "已暂停" in emp.rules_text()


def test_read_only_shows_in_rules(emp):
    from dataclasses import replace
    emp.policy = replace(emp.policy, read_only=True)
    assert "只读" in emp.rules_text()


def test_today_text(emp):
    assert "还没有付款请求" in emp.today_text(NOW)
    send(emp, "付 Alice 300 USDT 设计费", "r1")
    send(emp, "付 Bob 1500 USDT 重构", "r2")
    send(emp, "付 0x4c8e0000000000000000000000000000000091Aa 5 USDT", "r3")
    t = emp.today_text(NOW)
    assert "共 3 笔：自动付 1，待签 1，拦截 1" in t
    assert "还没有付款请求" in emp.today_text(NOW + timedelta(days=1))


def test_bot_dispatcher_builds(emp):
    pytest.importorskip("aiogram")
    from bot.main import PAYMENT_PREFIX, build_dispatcher

    assert build_dispatcher(emp) is not None
    assert PAYMENT_PREFIX.match("付 Alice 300 USDT")
    assert PAYMENT_PREFIX.match("pay Bob 1 BNB")
    assert not PAYMENT_PREFIX.match("今天开会吗")
    assert not PAYMENT_PREFIX.match("payment is late")
