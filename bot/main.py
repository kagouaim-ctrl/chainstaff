"""Telegram bot: forwards group messages to the finance ops employee and posts its replies.

Run:
    export TELEGRAM_BOT_TOKEN=...            # from @BotFather; never commit it
    export CHAINSTAFF_POLICY=config/policy.json
    export CHAINSTAFF_REPORT_CHAT=-100...    # optional: group that gets the Monday report and alerts
    python3 -m bot.main

See agent/runtime.py for chain / wallet settings. Without them the bot runs in test mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from agent.skills.finance_ops.alerts import EtherscanV2Source, check_transfers
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.skills.finance_ops.insights import TelegramSettings
from agent.skills.finance_ops.schedule import HELP, due_messages

log = logging.getLogger("chainstaff.bot")

# Only messages that look like a payment request are handled, so normal group chat is left alone.
PAYMENT_PREFIX = re.compile(r"^\s*(付|给|转|pay\b|send\b|transfer\b)", re.I)



def who(m: Message) -> str:
    u = m.from_user
    return f"@{u.username}" if u and u.username else str(u.id if u else "unknown")


def build_dispatcher(employee: FinanceOpsEmployee) -> Dispatcher:
    dp = Dispatcher()
    store = employee.store

    async def answer(m: Message, text: str) -> None:
        # Everything the bot handles shows up in the console's Telegram log.
        store.log_event("telegram_in", m.text or "", who(m), {"chat": m.chat.id})
        store.set_setting("telegram_heartbeat", datetime.now(timezone.utc).isoformat())
        await m.reply(text)
        store.log_event("telegram_out", text, "bot", {"chat": m.chat.id})

    def ids(m: Message) -> tuple:
        u = m.from_user
        return (u.username, u.id) if u else ()

    @dp.message(Command("start", "help"))
    async def _help(m: Message) -> None:
        await answer(m, HELP)

    @dp.message(Command("rules"))
    async def _rules(m: Message) -> None:
        await answer(m, employee.rules_text())

    @dp.message(Command("today"))
    async def _today(m: Message) -> None:
        await answer(m, employee.today_text())

    @dp.message(Command("pending"))
    async def _pending(m: Message) -> None:
        await answer(m, employee.pending_text())

    @dp.message(Command("report"))
    async def _report(m: Message) -> None:
        await answer(m, employee.weekly_report_text())

    @dp.message(Command("pause"))
    async def _pause(m: Message) -> None:
        await answer(m, employee.pause(*ids(m)))

    @dp.message(Command("resume"))
    async def _resume(m: Message) -> None:
        await answer(m, employee.resume(*ids(m)))

    @dp.message(Command("approve"))
    async def _approve(m: Message, command: CommandObject) -> None:
        if not command.args:
            await answer(m, "用法：/approve 编号，例如 /approve tg-123-45。发 /pending 查看编号。")
            return
        await answer(m, employee.approve(command.args.strip(), *ids(m)))

    @dp.message(Command("reject"))
    async def _reject(m: Message, command: CommandObject) -> None:
        if not command.args:
            await answer(m, "用法：/reject 编号。发 /pending 查看编号。")
            return
        await answer(m, employee.reject(command.args.strip(), *ids(m)))

    @dp.message(F.text.regexp(PAYMENT_PREFIX))
    async def _payment(m: Message) -> None:
        requester = who(m)
        # chat id + message id: Telegram may redeliver an update, and a request id
        # that is already in the ledger is never executed again.
        rid = f"tg-{m.chat.id}-{m.message_id}"
        # Run off the event loop: a live payment waits on the chain.
        reply = await asyncio.to_thread(employee.handle_request, m.text, requester, rid, m.date)
        await answer(m, reply.text)

    return dp


def next_monday_9am(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    days = (7 - local.weekday()) % 7
    target = (local + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=7)
    return target


def report_chat(employee: FinanceOpsEmployee, env=None) -> int | None:
    """The console can override the group set at deploy time."""
    value = employee.store.get_setting("telegram_chat") or (os.environ if env is None else env).get("CHAINSTAFF_REPORT_CHAT")
    return int(value) if value else None


async def send_logged(bot, employee: FinanceOpsEmployee, chat_id: int, text: str, kind: str) -> None:
    await bot.send_message(chat_id, text)
    employee.store.log_event("telegram_out", text, "bot", {"chat": chat_id, "kind": kind})


async def tick(bot, employee: FinanceOpsEmployee, now: datetime, env=None) -> int:
    """One pass of the bot's own work: heartbeat, scheduled posts, console outbox. Returns messages sent."""
    store = employee.store
    store.set_setting("telegram_heartbeat", now.isoformat())
    sent = 0
    chat_id = report_chat(employee, env)
    if chat_id is not None:
        for kind, text in due_messages(employee, now):
            await send_logged(bot, employee, chat_id, text, kind)
            sent += 1
    for item in store.outbox():
        try:
            await send_logged(bot, employee, int(item["chat"]), item["text"], "console")
            store.mark_sent(item["id"])
            sent += 1
        except Exception as e:  # noqa: BLE001 - one bad message must not block the queue
            store.mark_sent(item["id"], str(e) or type(e).__name__)
    return sent


async def tick_loop(bot: Bot, employee: FinanceOpsEmployee, every: int = 5) -> None:  # pragma: no cover - endless loop
    while True:
        try:
            await tick(bot, employee, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        await asyncio.sleep(every)


async def check_alerts_once(bot, employee: FinanceOpsEmployee, chat_id: int, source, treasury, thresholds, since: datetime) -> list[str]:
    transfers = []
    for addr in treasury:
        transfers += await asyncio.to_thread(source.transfers, addr, since)
    sent = []
    for alert in check_transfers(transfers, treasury, list(employee.policy.whitelist), thresholds):
        key = "alert:" + alert.key
        if employee.store.get_setting(key) is None:  # each alert once, even across restarts
            employee.store.set_setting(key, "1")
            employee.store.log_event("alert", alert.text, "monitor", {"key": alert.key})
            await bot.send_message(chat_id, alert.text)
            sent.append(alert.text)
    return sent


async def alert_loop(bot: Bot, employee: FinanceOpsEmployee, source, treasury, thresholds, every: int = 300) -> None:  # pragma: no cover - endless loop
    while True:
        try:
            chat_id = report_chat(employee)
            if chat_id is not None and TelegramSettings.load(employee.store).treasury_alerts:
                since = datetime.now(timezone.utc) - timedelta(hours=1)
                await check_alerts_once(bot, employee, chat_id, source, treasury, thresholds, since)
        except Exception:  # noqa: BLE001
            log.exception("alert check failed")
        await asyncio.sleep(every)


async def main() -> None:  # pragma: no cover - needs a live token
    from agent.runtime import build_employee

    logging.basicConfig(level=logging.INFO)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    employee, chain = build_employee()
    bot = Bot(token)
    me = await bot.get_me()
    employee.store.set_setting("telegram_bot", json.dumps({
        "id": me.id, "username": me.username, "name": me.first_name,
        "reads_all_group_messages": bool(me.can_read_all_group_messages),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    employee.store.log_event("system", f"机器人 @{me.username} 上线（{'实盘' if employee.executor.live else '测试模式'}）", "bot")
    tasks = [asyncio.create_task(tick_loop(bot, employee))]
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if chain and chain.treasury_addresses and chain.explorer_api_url and api_key:
        source = EtherscanV2Source(chain.explorer_api_url, api_key, {s: t.address for s, t in chain.tokens.items()})
        tasks.append(asyncio.create_task(alert_loop(bot, employee, source, chain.treasury_addresses, chain.alert_outflow_threshold)))
    log.info("ChainStaff bot starting (live=%s)", employee.executor.live)
    await build_dispatcher(employee).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
