"""Deterministic rules for batch payouts (KOL fees, campaign rewards, airdrops).

Like engine.py, this decides who gets how much; no model is involved. The result is
only ever a proposal: the batch is exported for the Safe owners to sign, and the
employee never sends a batch on its own.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Iterable, Mapping

from .models import ADDRESS_RE, normalize_address

FIXED, POOL = "fixed", "pool"  # amount per row | share of a pool by score
OK, EXCLUDED = "ok", "excluded"

_SPLIT = re.compile(r"[,\t;，]+|\s+")
_HEADER_WORDS = {"address", "wallet", "地址", "钱包", "amount", "score", "points", "数量", "金额", "积分"}


@dataclass(frozen=True)
class BatchRules:
    token: str
    mode: str = FIXED
    pool: Decimal | None = None  # POOL mode: total to split
    budget: Decimal | None = None  # FIXED mode: refuse the batch above this total
    per_person_cap: Decimal | None = None
    min_amount: Decimal = Decimal(0)
    decimals: int = 18
    display_places: int = 6  # pool shares are rounded down to this many places

    def __post_init__(self) -> None:
        if self.mode not in (FIXED, POOL):
            raise ValueError("结算方式只能是 fixed（按名单金额）或 pool（按积分分奖池）")
        if self.mode == POOL and (self.pool is None or self.pool <= 0):
            raise ValueError("按积分分奖池时，奖池总额必须大于 0")
        for name, v in (("预算", self.budget), ("单人上限", self.per_person_cap)):
            if v is not None and (not v.is_finite() or v <= 0):
                raise ValueError(f"{name}必须大于 0")
        if not self.min_amount.is_finite() or self.min_amount < 0:
            raise ValueError("最低发放额不能是负数")


@dataclass
class Row:
    line: int
    address: str
    value: str  # the amount (FIXED) or the score (POOL), as written
    note: str = ""


@dataclass
class Item:
    line: int
    address: str
    amount: Decimal
    note: str
    status: str = OK
    reasons: list[str] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)  # kept, but changed (e.g. capped)

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "address": self.address,
            "amount": f"{self.amount.normalize():f}" if self.amount else "0",
            "note": self.note,
            "status": self.status,
            "reasons": self.reasons,
            "adjusted": self.adjusted,
        }


@dataclass
class BatchResult:
    items: list[Item]
    errors: list[str]  # problems that block the whole batch
    total: Decimal

    @property
    def payable(self) -> list[Item]:
        return [i for i in self.items if i.status == OK]

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.payable)

    def summary(self) -> dict:
        excluded = [i for i in self.items if i.status == EXCLUDED]
        reasons: dict[str, int] = {}
        for i in excluded:
            for r in i.reasons:
                key = r.split("：")[0]
                reasons[key] = reasons.get(key, 0) + 1
        return {
            "rows": len(self.items),
            "payable": len(self.payable),
            "excluded": len(excluded),
            "adjusted": sum(1 for i in self.payable if i.adjusted),
            "total": f"{self.total.normalize():f}" if self.total else "0",
            "excluded_reasons": reasons,
            "errors": self.errors,
        }


def parse_rows(text: str) -> tuple[list[Row], list[str]]:
    """Read a pasted list or CSV: address, amount-or-score, optional note. Header row optional."""
    rows: list[Row] = []
    problems: list[str] = []
    sample = text.strip()
    reader = csv.reader(io.StringIO(sample)) if "," in sample.split("\n", 1)[0] else None
    lines = list(reader) if reader else [_SPLIT.split(ln.strip(), maxsplit=2) for ln in sample.splitlines()]
    for n, parts in enumerate(lines, 1):
        parts = [p.strip() for p in parts if p is not None]
        if not any(parts) or parts[0].startswith("#"):
            continue
        if n == 1 and parts[0].lower() in _HEADER_WORDS:
            continue
        if len(parts) < 2 or not parts[1]:
            problems.append(f"第 {n} 行少了数量：{' '.join(p for p in parts if p)[:60]}")
            continue
        rows.append(Row(n, parts[0], parts[1], ",".join(parts[2:])[:120]))
    return rows, problems


def _num(value: str) -> Decimal | None:
    try:
        d = Decimal(value.replace("_", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def compute(
    rows: Iterable[Row],
    rules: BatchRules,
    risk: Iterable[str] = (),
    exclude: Mapping[str, str] | None = None,
    paid_before: Mapping[str, str] | None = None,
) -> BatchResult:
    """Turn a list into payable items.

    risk         addresses on the owner's risk list (always excluded)
    exclude      address -> reason, e.g. the airdrop sybil check's removals
    paid_before  address -> where it was already paid in this campaign
    """
    risk_set = {normalize_address(a) for a in risk}
    exclude = {normalize_address(k): v for k, v in (exclude or {}).items()}
    paid_before = {normalize_address(k): v for k, v in (paid_before or {}).items()}
    items: list[Item] = []
    seen: dict[str, int] = {}
    values: list[Decimal | None] = []
    for r in rows:
        addr = r.address.strip()
        item = Item(r.line, normalize_address(addr), Decimal(0), r.note)
        v = _num(r.value)
        if not ADDRESS_RE.match(addr):
            item.reasons.append(f"地址格式不对：{addr[:50]}")
        elif item.address in seen:
            item.reasons.append(f"重复地址：和第 {seen[item.address]} 行相同")
        if v is None or v <= 0:
            item.reasons.append(f"数量无效：{r.value[:30]}")
        if item.address in risk_set:
            item.reasons.append("风险地址：在老板的风险名单里")
        if item.address in exclude:
            item.reasons.append(f"反女巫剔除：{exclude[item.address]}")
        if item.address in paid_before:
            item.reasons.append(f"已经发过：{paid_before[item.address]}")
        if ADDRESS_RE.match(addr):
            seen.setdefault(item.address, r.line)
        items.append(item)
        values.append(v)

    errors: list[str] = []
    places = Decimal(1).scaleb(-min(rules.decimals, rules.display_places))
    if rules.mode == POOL:
        scores = [v for i, v in zip(items, values) if not i.reasons]
        total_score = sum(scores, Decimal(0))
        for i, v in zip(items, values):
            if not i.reasons:
                i.amount = (rules.pool * v / total_score).quantize(places, rounding=ROUND_DOWN)
    else:
        for i, v in zip(items, values):
            if not i.reasons:
                i.amount = v
                if v.as_tuple().exponent < -rules.decimals:
                    i.reasons.append(f"小数位太多：{rules.token} 最多 {rules.decimals} 位")

    for i in items:
        if i.reasons:
            continue
        if rules.per_person_cap is not None and i.amount > rules.per_person_cap:
            if rules.mode == POOL:
                i.adjusted.append(f"超过单人上限，按上限 {rules.per_person_cap.normalize():f} 发放（原 {i.amount.normalize():f}）")
                i.amount = rules.per_person_cap
            else:
                i.reasons.append(f"超过单人上限：{i.amount.normalize():f} > {rules.per_person_cap.normalize():f}")
        if not i.reasons and i.amount < rules.min_amount:
            i.reasons.append(f"低于最低发放额：{i.amount.normalize():f} < {rules.min_amount.normalize():f}")
        if not i.reasons and i.amount <= 0:
            i.reasons.append("分到的数量为 0")
    for i in items:
        if i.reasons:
            i.status, i.amount = EXCLUDED, Decimal(0)

    total = sum((i.amount for i in items if i.status == OK), Decimal(0))
    if rules.mode == FIXED and rules.budget is not None and total > rules.budget:
        errors.append(f"合计 {total.normalize():f} {rules.token} 超过预算 {rules.budget.normalize():f}，请删减名单或提高预算")
    if rules.mode == POOL and total > rules.pool:  # pragma: no cover - rounding down makes this impossible
        errors.append("分配合计超过奖池")
    if not any(i.status == OK for i in items):
        errors.append("没有可以发放的地址")
    return BatchResult(items, errors, total)
