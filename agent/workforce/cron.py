"""Five-field cron expressions (minute hour day-of-month month day-of-week), evaluated in UTC.

Supports `*`, numbers, ranges `a-b`, steps `*/n` and `a-b/n`, and lists `a,b,c`.
Day-of-week 0 or 7 is Sunday. When both day fields are restricted, either may match (classic cron).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _field(expr: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError("步长必须大于 0")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError(f"{part} 超出范围 {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("cron 需要 5 段：分 时 日 月 周，例如 0 9 * * 1")
        try:
            f = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES)]
        except ValueError as e:
            raise ValueError(f"cron 格式不对：{e}") from None
        dow = frozenset(d % 7 for d in f[4])
        return cls(f[0], f[1], f[2], f[3], dow, parts[2] == "*", parts[4] == "*")

    def _day_ok(self, d: datetime) -> bool:
        dom_ok = d.day in self.dom
        dow_ok = (d.isoweekday() % 7) in self.dow
        if self.dom_any or self.dow_any:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        t = after.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = t + timedelta(days=366 * 5)
        while t < limit:
            if t.month not in self.month or not self._day_ok(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hour:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.minute:
                t += timedelta(minutes=1)
                continue
            return t
        raise ValueError("这个 cron 表达式在五年内都不会触发")
