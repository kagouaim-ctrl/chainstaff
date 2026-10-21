"""What the bot should post on its own right now, driven by TelegramSettings.

The bot calls due_messages() once a minute. Each message is marked as sent in the
store before it is returned, so a restart never posts the same report twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .insights import TelegramSettings

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

COMMANDS = [
    ("付 Alice 300 USDT 设计费", "发付款请求（不用斜杠）", False),
    ("/rules", "查看当前规则", False),
    ("/today", "今日账本", False),
    ("/pending", "等老板签字的付款", False),
    ("/report", "上周周报", False),
    ("/approve 编号", "老板确认已签字付款", True),
    ("/reject 编号", "老板驳回", True),
    ("/pause", "暂停所有付款", True),
    ("/resume", "恢复付款", True),
    ("/help", "显示这份说明", False),
]


def due_messages(employee, now: datetime) -> list[tuple[str, str]]:
    store = employee.store
    s = TelegramSettings.load(store)
    local = now.astimezone(ZoneInfo(employee.policy.timezone))
    out: list[tuple[str, str]] = []

    def once(key: str) -> bool:
        if store.get_setting(key) is not None:
            return False
        store.set_setting(key, now.isoformat())
        return True

    if s.weekly_report and local.weekday() == s.report_weekday and local.hour >= s.report_hour:
        year, week, _ = local.isocalendar()
        if once(f"sent:weekly:{year}-{week:02d}"):
            out.append(("weekly_report", employee.weekly_report_text(now)))

    if s.daily_digest and local.hour >= s.digest_hour and once(f"sent:digest:{local.date()}"):
        out.append(("daily_digest", employee.today_text(now)))

    if s.pending_reminder_hours and store.pending():
        last = store.get_setting("sent:pending_at")
        if last is None or now - datetime.fromisoformat(last) >= timedelta(hours=s.pending_reminder_hours):
            store.set_setting("sent:pending_at", now.isoformat())
            out.append(("pending_reminder", "⏰ 提醒：" + employee.pending_text()))
    return out
