"""Telegram bot: one bot for the whole AI team. Routes each group message and command
to the right employee (see agent/front_desk.py) and posts the replies.

Run:
    export TELEGRAM_BOT_TOKEN=...            # from @BotFather; never commit it
    export CHAINSTAFF_POLICY=config/policy.json
    export CHAINSTAFF_REPORT_CHAT=-100...    # optional: group that gets reports, briefs and alerts
    export ETHERSCAN_API_KEY=...             # optional: chain monitor
    export ANTHROPIC_API_KEY=...             # optional: announcement translator
    python3 -m bot.main

For community support to see every group message (not only commands and mentions), turn off
privacy mode for the bot in @BotFather (/setprivacy -> Disable). To delete scam messages it
must be a group admin with "Delete messages".
See agent/runtime.py for chain / wallet settings. Without them the bot runs in test mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from agent.front_desk import COMMAND_ROLES, PAYMENT_PREFIX, FrontDesk, Incoming  # noqa: F401  (PAYMENT_PREFIX re-exported)
from agent.skills.finance_ops.alerts import EtherscanV2Source, check_transfers
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.skills.finance_ops.insights import TelegramSettings

log = logging.getLogger("chainstaff.bot")


def who(m: Message) -> str:
    u = m.from_user
    return f"@{u.username}" if u and u.username else str(u.id if u else "unknown")


def mentioned(m: Message, bot_username: str | None, bot_id: int | None) -> bool:
    """True when the message @-mentions the bot, replies to it, or is a private chat."""
    if m.chat.type == "private":
        return True
    r = m.reply_to_message
    if r and r.from_user and bot_id and r.from_user.id == bot_id:
        return True
    return bool(bot_username) and f"@{bot_username}".lower() in (m.text or "").lower()


def build_dispatcher(desk: FrontDesk) -> Dispatcher:
    dp = Dispatcher()
    store = desk.store

    def bot_identity() -> tuple[str | None, int | None]:
        raw = store.get_setting("telegram_bot")
        info = json.loads(raw) if raw else {}
        return info.get("username"), info.get("id")

    async def answer(m: Message, text: str, employee: str = "") -> None:
        # Everything the bot handles shows up in the console's Telegram log.
        store.log_event("telegram_in", m.text or "", who(m), {"chat": m.chat.id}, employee=employee)
        store.set_setting("telegram_heartbeat", datetime.now(timezone.utc).isoformat())
        await m.reply(text)
        store.log_event("telegram_out", text, "bot", {"chat": m.chat.id}, employee=employee)

    @dp.message(Command(*(["start", "help", "team"] + list(COMMAND_ROLES))))
    async def _command(m: Message, command: CommandObject) -> None:
        u = m.from_user
        # Run off the event loop: /translate waits on the model, /approve may wait on the chain.
        out = await asyncio.to_thread(desk.command, command.command, command.args or "",
                                      u.username if u else None, u.id if u else None, str(m.chat.id))
        if out and out.reply:
            await answer(m, out.reply, out.employee)

    @dp.message(F.new_chat_members)
    async def _joined(m: Message) -> None:
        for u in m.new_chat_members or []:
            text = desk.welcome(u.first_name or u.username or "")
            if text:
                await m.reply(text)
                store.log_event("telegram_out", text, "bot", {"chat": m.chat.id, "kind": "welcome"}, employee="community_support")

    @dp.message(F.text)
    async def _text(m: Message) -> None:
        u = m.from_user
        name, bot_id = bot_identity()
        msg = Incoming(m.text or "", u.username if u else None, u.id if u else None,
                       " ".join(x for x in (u.first_name, u.last_name) if x) if u else "",
                       str(m.chat.id), m.message_id, m.date, mentioned(m, name, bot_id))
        out = await asyncio.to_thread(desk.on_message, msg)
        if out.delete:
            try:
                await m.delete()
            except Exception:  # noqa: BLE001 - no admin rights: the warning still goes out
                log.warning("could not delete scam message in %s", m.chat.id)
        if out.reply:
            if out.delete:
                await m.answer(out.reply)  # the original is gone, so reply to the chat instead
                store.log_event("telegram_out", out.reply, "bot", {"chat": m.chat.id}, employee=out.employee)
            else:
                await answer(m, out.reply, out.employee)

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


async def send_logged(bot, employee: FinanceOpsEmployee, chat_id: int, text: str, kind: str, role: str = "") -> None:
    await bot.send_message(chat_id, text)
    employee.store.log_event("telegram_out", text, "bot", {"chat": chat_id, "kind": kind}, employee=role)


async def tick(bot, desk: FrontDesk, now: datetime, env=None) -> int:
    """One pass of the bot's own work: heartbeat, scheduled posts, console outbox. Returns messages sent."""
    employee = desk.finance
    store = employee.store
    store.set_setting("telegram_heartbeat", now.isoformat())
    sent = 0
    chat_id = report_chat(employee, env)
    if chat_id is not None:
        for kind, text in desk.scheduled(now):
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


async def tick_loop(bot: Bot, desk: FrontDesk, every: int = 5) -> None:  # pragma: no cover - endless loop
    while True:
        try:
            await tick(bot, desk, datetime.now(timezone.utc))
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


async def monitor_once(bot, desk: FrontDesk, chat_id: int) -> int:
    """The chain monitor's pass: every new alert goes to the group once."""
    alerts = await asyncio.to_thread(desk.monitor.run_once)
    for a in alerts:
        await send_logged(bot, desk.finance, chat_id, a["text"], "alert", "chain_monitor")
    return len(alerts)


async def alert_loop(bot: Bot, desk: FrontDesk, source, treasury, thresholds, every: int = 300) -> None:  # pragma: no cover - endless loop
    employee = desk.finance
    while True:
        try:
            chat_id = report_chat(employee)
            if chat_id is not None and desk.on("chain_monitor") and desk.monitor.ready:
                await monitor_once(bot, desk, chat_id)
            elif chat_id is not None and source and TelegramSettings.load(employee.store).treasury_alerts:
                # Without a chain monitor on the team, the finance employee keeps its basic treasury alerts.
                since = datetime.now(timezone.utc) - timedelta(hours=1)
                await check_alerts_once(bot, employee, chat_id, source, treasury, thresholds, since)
        except Exception:  # noqa: BLE001
            log.exception("alert check failed")
        await asyncio.sleep(every)


async def main() -> None:  # pragma: no cover - needs a live token
    from agent.runtime import build_front_desk

    logging.basicConfig(level=logging.INFO)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    desk, chain = build_front_desk()
    employee = desk.finance
    bot = Bot(token)
    me = await bot.get_me()
    employee.store.set_setting("telegram_bot", json.dumps({
        "id": me.id, "username": me.username, "name": me.first_name,
        "reads_all_group_messages": bool(me.can_read_all_group_messages),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    employee.store.log_event("system", f"机器人 @{me.username} 上线（{'实盘' if employee.executor.live else '测试模式'}）", "bot")
    tasks = [asyncio.create_task(tick_loop(bot, desk))]
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    source = None
    if chain and chain.explorer_api_url and api_key:
        source = EtherscanV2Source(chain.explorer_api_url, api_key, {s: t.address for s, t in chain.tokens.items()})
    treasury = chain.treasury_addresses if chain else ()
    tasks.append(asyncio.create_task(alert_loop(bot, desk, source, treasury, chain.alert_outflow_threshold if chain else {})))
    log.info("ChainStaff bot starting (live=%s)", employee.executor.live)
    await build_dispatcher(desk).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
