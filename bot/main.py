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

log = logging.getLogger("chainstaff.bot")

# Only messages that look like a payment request are handled, so normal group chat is left alone.
PAYMENT_PREFIX = re.compile(r"^\s*(付|给|转|pay\b|send\b|transfer\b)", re.I)

HELP = (
    "我是团队的 AI 财务运营员。\n"
    "发付款请求：付 Alice 300 USDT 设计费\n"
    "/rules 查看规则\n"
    "/today 今日账本\n"
    "/pending 等老板签字的付款\n"
    "/report 上周周报\n"
    "/approve 编号 · /reject 编号（老板）\n"
    "/pause 暂停所有付款 · /resume 恢复（老板）"
)


def build_dispatcher(employee: FinanceOpsEmployee) -> Dispatcher:
    dp = Dispatcher()

    def ids(m: Message) -> tuple:
        u = m.from_user
        return (u.username, u.id) if u else ()

    @dp.message(Command("start", "help"))
    async def _help(m: Message) -> None:
        await m.reply(HELP)

    @dp.message(Command("rules"))
    async def _rules(m: Message) -> None:
        await m.reply(employee.rules_text())

    @dp.message(Command("today"))
    async def _today(m: Message) -> None:
        await m.reply(employee.today_text())

    @dp.message(Command("pending"))
    async def _pending(m: Message) -> None:
        await m.reply(employee.pending_text())

    @dp.message(Command("report"))
    async def _report(m: Message) -> None:
        await m.reply(employee.weekly_report_text())

    @dp.message(Command("pause"))
    async def _pause(m: Message) -> None:
        await m.reply(employee.pause(*ids(m)))

    @dp.message(Command("resume"))
    async def _resume(m: Message) -> None:
        await m.reply(employee.resume(*ids(m)))

    @dp.message(Command("approve"))
    async def _approve(m: Message, command: CommandObject) -> None:
        if not command.args:
            await m.reply("用法：/approve 编号，例如 /approve tg-123-45。发 /pending 查看编号。")
            return
        await m.reply(employee.approve(command.args.strip(), *ids(m)))

    @dp.message(Command("reject"))
    async def _reject(m: Message, command: CommandObject) -> None:
        if not command.args:
            await m.reply("用法：/reject 编号。发 /pending 查看编号。")
            return
        await m.reply(employee.reject(command.args.strip(), *ids(m)))

    @dp.message(F.text.regexp(PAYMENT_PREFIX))
    async def _payment(m: Message) -> None:
        u = m.from_user
        requester = f"@{u.username}" if u and u.username else str(u.id if u else "unknown")
        # chat id + message id: Telegram may redeliver an update, and a request id
        # that is already in the ledger is never executed again.
        rid = f"tg-{m.chat.id}-{m.message_id}"
        # Run off the event loop: a live payment waits on the chain.
        reply = await asyncio.to_thread(employee.handle_request, m.text, requester, rid, m.date)
        await m.reply(reply.text)

    return dp


def next_monday_9am(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    days = (7 - local.weekday()) % 7
    target = (local + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=7)
    return target


async def weekly_report_loop(bot: Bot, employee: FinanceOpsEmployee, chat_id: int) -> None:  # pragma: no cover - endless loop
    while True:
        now = datetime.now(timezone.utc)
        await asyncio.sleep((next_monday_9am(now, employee.policy.timezone) - now).total_seconds())
        try:
            await bot.send_message(chat_id, employee.weekly_report_text())
        except Exception:  # noqa: BLE001
            log.exception("weekly report failed")


async def check_alerts_once(bot, employee: FinanceOpsEmployee, chat_id: int, source, treasury, thresholds, since: datetime) -> list[str]:
    transfers = []
    for addr in treasury:
        transfers += await asyncio.to_thread(source.transfers, addr, since)
    sent = []
    for alert in check_transfers(transfers, treasury, list(employee.policy.whitelist), thresholds):
        key = "alert:" + alert.key
        if employee.store.get_setting(key) is None:  # each alert once, even across restarts
            employee.store.set_setting(key, "1")
            await bot.send_message(chat_id, alert.text)
            sent.append(alert.text)
    return sent


async def alert_loop(bot: Bot, employee: FinanceOpsEmployee, chat_id: int, source, treasury, thresholds, every: int = 300) -> None:  # pragma: no cover - endless loop
    while True:
        try:
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
    tasks = []
    chat = os.environ.get("CHAINSTAFF_REPORT_CHAT")
    if chat:
        tasks.append(asyncio.create_task(weekly_report_loop(bot, employee, int(chat))))
        api_key = os.environ.get("ETHERSCAN_API_KEY")
        if chain and chain.treasury_addresses and chain.explorer_api_url and api_key:
            source = EtherscanV2Source(chain.explorer_api_url, api_key, {s: t.address for s, t in chain.tokens.items()})
            tasks.append(asyncio.create_task(alert_loop(bot, employee, int(chat), source, chain.treasury_addresses, chain.alert_outflow_threshold)))
    log.info("ChainStaff bot starting (live=%s)", employee.executor.live)
    await build_dispatcher(employee).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
