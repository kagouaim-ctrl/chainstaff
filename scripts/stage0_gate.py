"""Score the stage 0 interview sheet against gate A (策划书第五章).

    python3 scripts/stage0_gate.py /mnt/project-files/stage0/访谈记录表.csv

A row is a strong lead when 额度=是, 价格=是 and 试用=是. Gate A passes with at least
3 teams that agreed to a trial in writing (试用=是). The script also prints the funnel
(listed -> messaged -> replied -> interviewed) and the median of the numeric answers,
which feed the pricing and limit defaults.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from statistics import median

GATE_TRIALS = 3
TARGET_INTERVIEWS = 15
YES = {"是", "yes", "y", "1", "true", "✅"}


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() in YES


def _number(value: str | None) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", (value or "").replace(",", ""))
    return float(m.group()) if m else None


def load(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("项目名") or "").strip()]


def score(rows: list[dict[str, str]]) -> dict:
    interviewed = [r for r in rows if (r.get("访谈日期") or "").strip()]
    trials = [r for r in interviewed if _yes(r.get("试用=是"))]
    strong = [r for r in trials if _yes(r.get("额度=是")) and _yes(r.get("价格=是"))]
    prices = [n for r in interviewed if (n := _number(r.get("愿付价格(USDT/月)"))) is not None]
    limits = [n for r in interviewed if (n := _number(r.get("愿给额度(USDT)"))) is not None]
    hours = [n for r in interviewed if (n := _number(r.get("每月付款对账小时"))) is not None]
    return {
        "listed": len(rows),
        "messaged": sum(1 for r in rows if (r.get("私信日期") or "").strip()),
        "replied": sum(1 for r in rows if _yes(r.get("回复")) or (r.get("访谈日期") or "").strip()),
        "interviewed": len(interviewed),
        "trials": [r["项目名"] for r in trials],
        "strong": [r["项目名"] for r in strong],
        "median_price": median(prices) if prices else None,
        "median_limit": median(limits) if limits else None,
        "median_hours": median(hours) if hours else None,
        "passed": len(trials) >= GATE_TRIALS,
    }


def report(s: dict) -> str:
    def fmt(v):
        return "—" if v is None else f"{v:g}"

    lines = [
        f"名单 {s['listed']} · 已私信 {s['messaged']} · 已回复 {s['replied']} · 已访谈 {s['interviewed']}/{TARGET_INTERVIEWS}",
        f"同意试用 {len(s['trials'])}/{GATE_TRIALS}：{'、'.join(s['trials']) or '无'}",
        f"强意向（额度、价格、试用都是）{len(s['strong'])}：{'、'.join(s['strong']) or '无'}",
        f"中位数：愿付 {fmt(s['median_price'])} USDT/月 · 愿给额度 {fmt(s['median_limit'])} USDT · 每月对账 {fmt(s['median_hours'])} 小时",
    ]
    if s["passed"]:
        lines.append("关卡 A：通过，可以让试用客户上线（阶段 1 第 9 周）。")
    else:
        lines.append(f"关卡 A：未通过，还差 {GATE_TRIALS - len(s['trials'])} 家书面同意试用。")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path)
    args = ap.parse_args(argv)
    s = score(load(args.csv))
    print(report(s))
    return 0 if s["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
