"""Read-only numbers for the owner console: overview, per-payee totals, rule diffs,
and the Telegram notification settings the bot reads from the store."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from agent.rules import Outcome
from agent.store import APPROVED, BLOCKED, EXECUTING, FAILED, PAID, PENDING, REJECTED, PaymentRecord, Store

MOVED = (PAID, APPROVED)  # money left: by the employee or by the owner's signature


def _s(a: Decimal) -> str:
    return f"{a.normalize():f}"


def _sum_by_token(records: Iterable[PaymentRecord], statuses: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for r in records:
        if r.status in statuses:
            out[r.request.token] += r.request.amount
    return {t: _s(v) for t, v in sorted(out.items())}


def counts(records: Iterable[PaymentRecord]) -> dict[str, int]:
    c = {s: 0 for s in (PAID, FAILED, EXECUTING, PENDING, APPROVED, REJECTED, BLOCKED)}
    for r in records:
        c[r.status] = c.get(r.status, 0) + 1
    return c


def automation_rate(records: Iterable[PaymentRecord]) -> float | None:
    """Share of requests that were not blocked and needed no owner action."""
    rs = [r for r in records if r.outcome is not Outcome.BLOCK]
    if not rs:
        return None
    return round(sum(1 for r in rs if r.status == PAID) / len(rs), 4)


def overview(store: Store, tz: str, daily_limit: dict[str, Decimal], now: datetime, days: int = 14) -> dict:
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    today = datetime.combine(local.date(), time.min, tzinfo=zone)
    month = today.replace(day=1)
    first = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)
    span = store.between(min(first, month), end)

    def within(start: datetime, stop: datetime) -> list[PaymentRecord]:
        return [r for r in span if start <= r.request.created_at < stop]

    this_month = within(month, end)
    today_rows = within(today, end)
    series = []
    for i in range(days):
        d0 = first + timedelta(days=i)
        rows = within(d0, d0 + timedelta(days=1))
        series.append({
            "date": d0.date().isoformat(),
            "auto": sum(1 for r in rows if r.outcome is Outcome.AUTO_PAY),
            "approval": sum(1 for r in rows if r.outcome is Outcome.NEEDS_APPROVAL),
            "block": sum(1 for r in rows if r.outcome is Outcome.BLOCK),
            "moved": _sum_by_token(rows, MOVED),
        })
    spent_today = _sum_by_token([r for r in today_rows if r.outcome is Outcome.AUTO_PAY], (PAID, EXECUTING))
    budget = [
        {"token": t, "spent": spent_today.get(t, "0"), "limit": _s(limit)}
        for t, limit in sorted(daily_limit.items())
    ]
    return {
        "month": {
            "start": month.date().isoformat(),
            "requests": len(this_month),
            "counts": counts(this_month),
            "moved": _sum_by_token(this_month, MOVED),
            "auto_paid": _sum_by_token(this_month, (PAID,)),
            "blocked_value": _sum_by_token(this_month, (BLOCKED,)),
            "automation_rate": automation_rate(this_month),
        },
        "today": {"date": local.date().isoformat(), "requests": len(today_rows), "counts": counts(today_rows), "budget": budget},
        "series": series,
        "top_payees": payee_stats(this_month)[:5],
        "pending": len(store.pending()),
    }


def payee_stats(records: Iterable[PaymentRecord]) -> list[dict]:
    by: dict[str, dict] = {}
    for r in records:
        p = by.setdefault(r.request.payee, {"payee": r.request.payee, "requests": 0, "blocked": 0, "moved": defaultdict(Decimal), "last_at": None})
        p["requests"] += 1
        p["blocked"] += r.status == BLOCKED
        if r.status in MOVED:
            p["moved"][r.request.token] += r.request.amount
        at = r.request.created_at.isoformat()
        p["last_at"] = max(p["last_at"] or at, at)
    out = []
    for p in by.values():
        p["moved"] = {t: _s(v) for t, v in sorted(p["moved"].items())}
        out.append(p)
    return sorted(out, key=lambda p: (-sum(Decimal(v) for v in p["moved"].values()), -p["requests"]))


def rules_diff(before: dict, after: dict) -> list[str]:
    """Readable list of what changed between two policy JSON documents."""
    changes: list[str] = []
    for key in ("single_limit", "daily_limit"):
        label = "单笔上限" if key == "single_limit" else "每日上限"
        b, a = before.get(key, {}), after.get(key, {})
        for t in sorted(set(b) | set(a)):
            if str(b.get(t)) != str(a.get(t)):
                changes.append(f"{t} {label}：{b.get(t, '无')} → {a.get(t, '无')}")
    bp = {p["address"].lower(): p for p in before.get("payees", [])}
    ap = {p["address"].lower(): p for p in after.get("payees", [])}
    for addr in ap.keys() - bp.keys():
        changes.append(f"新增收款方 {ap[addr]['name']}（{addr[:6]}…{addr[-4:]}）")
    for addr in bp.keys() - ap.keys():
        changes.append(f"删除收款方 {bp[addr]['name']}（{addr[:6]}…{addr[-4:]}）")
    for addr in ap.keys() & bp.keys():
        if json.dumps(ap[addr], sort_keys=True) != json.dumps(bp[addr], sort_keys=True):
            changes.append(f"修改收款方 {ap[addr]['name']}")
    simple = {
        "read_only": "试用期只读",
        "approval_categories": "需审批类别",
        "duplicate_window_days": "重复检测天数",
        "timezone": "时区",
        "owners": "老板",
        "risk_addresses": "风险地址",
    }
    for key, label in simple.items():
        if json.dumps(before.get(key), sort_keys=True) != json.dumps(after.get(key), sort_keys=True):
            changes.append(f"{label}：{_brief(before.get(key))} → {_brief(after.get(key))}")
    return changes


def _brief(v) -> str:
    if isinstance(v, bool):
        return "开" if v else "关"
    if isinstance(v, list):
        return "、".join(map(str, v)) if len(v) <= 4 else f"{len(v)} 项"
    return "无" if v is None else str(v)


@dataclass
class TelegramSettings:
    """What the bot posts on its own. Stored in the ledger DB so the console can change it live."""

    weekly_report: bool = True
    report_weekday: int = 0  # Monday
    report_hour: int = 9
    daily_digest: bool = False
    digest_hour: int = 21
    treasury_alerts: bool = True
    pending_reminder_hours: int = 0  # 0 = off; otherwise remind owners about unsigned payments
    quiet_auto_pay: bool = False  # True: green payments get a one-line reply instead of the full receipt

    KEY = "telegram_settings"

    @classmethod
    def load(cls, store: Store) -> "TelegramSettings":
        raw = store.get_setting(cls.KEY)
        if not raw:
            return cls()
        data = json.loads(raw)
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    @classmethod
    def validate(cls, data: dict) -> "TelegramSettings":
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            v = data[f.name]
            if f.type == "bool":
                if not isinstance(v, bool):
                    raise ValueError(f"{f.name} 必须是开或关")
            else:
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(f"{f.name} 必须是整数")
                hi = {"report_weekday": 6, "report_hour": 23, "digest_hour": 23, "pending_reminder_hours": 168}[f.name]
                if not 0 <= v <= hi:
                    raise ValueError(f"{f.name} 要在 0 到 {hi} 之间")
            setattr(s, f.name, v)
        return s

    def save(self, store: Store) -> None:
        store.set_setting(self.KEY, json.dumps(asdict(self)))
