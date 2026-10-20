"""Data types shared by the rule engine. No floats anywhere: amounts are Decimal."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def normalize_address(addr: str) -> str:
    # Addresses are compared case-insensitively; checksum casing is display-only.
    return addr.strip().lower()


def to_amount(value: Decimal | str | int) -> Decimal:
    """Convert to Decimal, refusing float so binary rounding can never reach money."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("amount must be Decimal, str or int, never float/bool")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"not a valid amount: {value!r}") from e


class Outcome(str, Enum):
    AUTO_PAY = "AUTO_PAY"  # 绿：员工在限额内自己付
    NEEDS_APPROVAL = "NEEDS_APPROVAL"  # 黄：生成 Safe 提案，老板签字
    BLOCK = "BLOCK"  # 红：拦截


class Reason(str, Enum):
    # red
    PAUSED = "PAUSED"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    UNSUPPORTED_TOKEN = "UNSUPPORTED_TOKEN"
    INVALID_ADDRESS = "INVALID_ADDRESS"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    RISK_FLAGGED = "RISK_FLAGGED"
    NOT_WHITELISTED = "NOT_WHITELISTED"
    DUPLICATE = "DUPLICATE"
    # amber
    READ_ONLY_MODE = "READ_ONLY_MODE"
    OVER_SINGLE_LIMIT = "OVER_SINGLE_LIMIT"
    OVER_DAILY_LIMIT = "OVER_DAILY_LIMIT"
    CATEGORY_NEEDS_APPROVAL = "CATEGORY_NEEDS_APPROVAL"
    PAYEE_NEEDS_APPROVAL = "PAYEE_NEEDS_APPROVAL"


RED_REASONS = frozenset(
    {
        Reason.PAUSED,
        Reason.INVALID_AMOUNT,
        Reason.UNSUPPORTED_TOKEN,
        Reason.INVALID_ADDRESS,
        Reason.ALREADY_PROCESSED,
        Reason.RISK_FLAGGED,
        Reason.NOT_WHITELISTED,
        Reason.DUPLICATE,
    }
)


@dataclass(frozen=True)
class Payee:
    address: str
    name: str
    aliases: tuple[str, ...] = ()
    needs_approval: bool = False


@dataclass(frozen=True)
class PaymentRequest:
    request_id: str
    requester: str
    payee: str
    amount: Decimal
    token: str
    created_at: datetime
    category: str = ""
    memo: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_amount(self.amount))
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True)
class Policy:
    whitelist: Mapping[str, Payee]  # keyed by normalized address
    single_limit: Mapping[str, Decimal]  # token -> max per payment
    daily_limit: Mapping[str, Decimal]  # token -> max auto-paid per day
    token_decimals: Mapping[str, int] = field(
        default_factory=lambda: {"USDT": 18, "USDC": 18, "USD1": 18, "BNB": 18}
    )
    approval_categories: frozenset[str] = frozenset()
    risk_addresses: frozenset[str] = frozenset()  # normalized
    duplicate_window: timedelta = timedelta(days=30)
    timezone: str = "UTC"
    paused: bool = False
    read_only: bool = False

    @staticmethod
    def build(payees: list[Payee], **kw) -> "Policy":
        """Convenience constructor that normalizes every address once."""
        wl = {normalize_address(p.address): p for p in payees}
        if "risk_addresses" in kw:
            kw["risk_addresses"] = frozenset(normalize_address(a) for a in kw["risk_addresses"])
        for key in ("single_limit", "daily_limit"):
            if key in kw:
                kw[key] = {t: to_amount(v) for t, v in kw[key].items()}
        return Policy(whitelist=wl, **kw)


@dataclass(frozen=True)
class Decision:
    request_id: str
    outcome: Outcome
    reasons: tuple[Reason, ...]
    messages: tuple[str, ...]

    @property
    def is_auto_pay(self) -> bool:
        return self.outcome is Outcome.AUTO_PAY
