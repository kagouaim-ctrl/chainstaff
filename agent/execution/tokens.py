"""Token registry and exact conversion between human amounts and on-chain base units."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from agent.rules import to_amount


@dataclass(frozen=True)
class TokenInfo:
    symbol: str
    decimals: int
    address: str | None  # None = chain's native coin (BNB)

    @property
    def is_native(self) -> bool:
        return self.address is None


def to_base_units(amount: Decimal | str | int, decimals: int) -> int:
    """Exact conversion; refuses amounts that would need rounding."""
    a = to_amount(amount)
    if not a.is_finite() or a <= 0:
        raise ValueError(f"amount must be positive: {amount}")
    scaled = a.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than {decimals} decimal places")
    return int(scaled)


def from_base_units(units: int, decimals: int) -> Decimal:
    return Decimal(units).scaleb(-decimals)
