"""Read-only ledger view the engine depends on, plus an in-memory implementation for tests and demos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Protocol

from .models import Decision, Outcome, PaymentRequest, normalize_address


@dataclass(frozen=True)
class LedgerEntry:
    request: PaymentRequest
    outcome: Outcome


class LedgerView(Protocol):
    def has_request(self, request_id: str) -> bool: ...

    def auto_paid_total(self, token: str, start: datetime, end: datetime) -> Decimal: ...

    def recent_payments(self, payee: str, token: str, since: datetime) -> Iterable[LedgerEntry]: ...


class InMemoryLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._ids: set[str] = set()

    def record(self, request: PaymentRequest, decision: Decision) -> None:
        if request.request_id in self._ids:
            raise ValueError(f"request {request.request_id} already recorded")
        self._ids.add(request.request_id)
        self._entries.append(LedgerEntry(request, decision.outcome))

    def has_request(self, request_id: str) -> bool:
        return request_id in self._ids

    def auto_paid_total(self, token: str, start: datetime, end: datetime) -> Decimal:
        # Only AUTO_PAY spends the employee's own budget; owner-signed payments don't.
        return sum(
            (
                e.request.amount
                for e in self._entries
                if e.outcome is Outcome.AUTO_PAY
                and e.request.token == token
                and start <= e.request.created_at < end
            ),
            Decimal(0),
        )

    def recent_payments(self, payee: str, token: str, since: datetime) -> list[LedgerEntry]:
        p = normalize_address(payee)
        # Blocked entries never moved money, so they can't make a later request a duplicate.
        return [
            e
            for e in self._entries
            if normalize_address(e.request.payee) == p
            and e.request.token == token
            and e.request.created_at >= since
            and e.outcome is not Outcome.BLOCK
        ]
