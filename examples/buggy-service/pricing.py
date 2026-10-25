"""A tiny pricing service used by KAGOU's end-to-end check of the bug workflow.

The bug is real: average_price() divides by the number of trades, so a token with no trades in the window raises
ZeroDivisionError and the /price endpoint returns 500. The agents have to find it, fix it and prove the fix.
"""

from decimal import Decimal


def average_price(trades: list[dict]) -> Decimal:
    """Volume-weighted average price of the trades in the window."""
    volume = sum(Decimal(str(t["amount"])) for t in trades)
    notional = sum(Decimal(str(t["amount"])) * Decimal(str(t["price"])) for t in trades)
    return notional / volume


def quote(symbol: str, trades: list[dict]) -> dict:
    return {"symbol": symbol, "price": str(average_price(trades)), "trades": len(trades)}
