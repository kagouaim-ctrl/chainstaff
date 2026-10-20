"""Telegram bot: forwards group messages to the finance ops employee and posts its replies.

Run:
    export TELEGRAM_BOT_TOKEN=...          # from @BotFather; never commit it
    export CHAINSTAFF_POLICY=config/policy.json
    python3 -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from agent.skills.finance_ops.employee import FinanceOpsEmployee, load_policy

# Only messages that look like a payment request are handled, so normal group chat is left alone.
PAYMENT_PREFIX = re.compile(r"^\s*(付|给|转|pay\b|send\b|transfer\b)", re.I)

HELP = (
    "我是团队的 AI 财务运营员。\n"
    "发付款请求：付 Alice 300 USDT 设计费\n"
    "/rules 查看规则\n"
    "/today 今日账本\n"
    "/pause 暂停所有付款（老板）\n"
    "/resume 恢复付款（老板）"
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

    @dp.message(Command("pause"))
    async def _pause(m: Message) -> None:
        await m.reply(employee.pause(*ids(m)))

    @dp.message(Command("resume"))
    async def _resume(m: Message) -> None:
        await m.reply(employee.resume(*ids(m)))

    @dp.message(F.text.regexp(PAYMENT_PREFIX))
    async def _payment(m: Message) -> None:
        u = m.from_user
        requester = f"@{u.username}" if u and u.username else str(u.id if u else "unknown")
        # chat id + message id: Telegram may redeliver an update, and the engine
        # blocks a request id it has already seen, so a redelivery can't pay twice.
        rid = f"tg-{m.chat.id}-{m.message_id}"
        reply = employee.handle_request(m.text, requester, rid, m.date)
        await m.reply(reply.text)

    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    policy, owners = load_policy(os.environ.get("CHAINSTAFF_POLICY", "config/policy.example.json"))
    employee = FinanceOpsEmployee(policy, owners)
    bot = Bot(token)
    await build_dispatcher(employee).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
