"""SQLite-backed ledger: every payment request, its decision and what happened to it.

A request row is written *before* any money moves (status "executing"), and
request_id is the primary key, so a crash or a redelivered chat message can
never pay the same request twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from agent.rules import Decision, LedgerEntry, Outcome, PaymentRequest, normalize_address

# Lifecycle per outcome:
#   AUTO_PAY:       executing -> paid | failed
#   NEEDS_APPROVAL: pending_approval -> approved | rejected
#   BLOCK:          blocked
EXECUTING, PAID, FAILED = "executing", "paid", "failed"
PENDING, APPROVED, REJECTED = "pending_approval", "approved", "rejected"
BLOCKED = "blocked"

# Statuses where money moved, or may have moved (a crash mid-execution is treated as spent).
_SPENT = (EXECUTING, PAID)
# Statuses that make a later identical request a duplicate.
_DUPLICATE_SOURCES = (EXECUTING, PAID, PENDING, APPROVED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    request_id    TEXT PRIMARY KEY,
    requester     TEXT NOT NULL,
    payee         TEXT NOT NULL,      -- normalized lowercase address
    amount        TEXT NOT NULL,      -- Decimal as text, never REAL
    token         TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT '',
    memo          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,      -- ISO 8601 UTC
    outcome       TEXT NOT NULL,
    reasons       TEXT NOT NULL,      -- JSON list of reason codes
    messages      TEXT NOT NULL,      -- JSON list of human-readable reasons
    status        TEXT NOT NULL,
    tx_hash       TEXT,
    safe_tx_hash  TEXT,
    error         TEXT,
    decided_by    TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_payee ON payments(payee, token, created_at);
CREATE INDEX IF NOT EXISTS payments_created ON payments(created_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Append-only log for the console: owner actions, rule changes, alerts, Telegram traffic.
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,             -- audit | alert | telegram_in | telegram_out | system
    actor  TEXT NOT NULL DEFAULT '',
    text   TEXT NOT NULL,
    data   TEXT                       -- optional JSON
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id);
-- Messages the console asks the bot to send; the bot drains it.
CREATE TABLE IF NOT EXISTS outbox (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    chat     TEXT NOT NULL,
    text     TEXT NOT NULL,
    sent_at  TEXT,
    error    TEXT
);
"""


def _iso(dt: datetime) -> str:
    # Fixed-width UTC format so string comparison in SQL equals time comparison.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


@dataclass(frozen=True)
class PaymentRecord:
    request: PaymentRequest
    outcome: Outcome
    reasons: tuple[str, ...]
    messages: tuple[str, ...]
    status: str
    tx_hash: str | None
    safe_tx_hash: str | None
    error: str | None
    decided_by: str | None
    updated_at: datetime

    def to_dict(self) -> dict:
        r = self.request
        return {
            "request_id": r.request_id,
            "requester": r.requester,
            "payee": r.payee,
            "amount": str(r.amount),
            "token": r.token,
            "category": r.category,
            "memo": r.memo,
            "created_at": _iso(r.created_at),
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "messages": list(self.messages),
            "status": self.status,
            "tx_hash": self.tx_hash,
            "safe_tx_hash": self.safe_tx_hash,
            "error": self.error,
            "decided_by": self.decided_by,
            "updated_at": _iso(self.updated_at),
        }


class _Result:
    def __init__(self, rows: list, rowcount: int, lastrowid: int | None) -> None:
        self._rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _SerializedConnection:
    """One sqlite3 connection shared by the bot and the console's worker threads.
    Every statement runs and is fully fetched under one lock, so concurrent requests
    never interleave on the connection."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn, self._lock = conn, lock

    def execute(self, sql: str, args=()) -> _Result:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return _Result(cur.fetchall(), cur.rowcount, cur.lastrowid)

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self._db = _SerializedConnection(conn, self._lock)
        self._db.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self._db.executescript(SCHEMA)

    # ---- writes ----

    def record(self, req: PaymentRequest, decision: Decision) -> str:
        """Insert a new request. Raises ValueError if the request_id exists."""
        status = {Outcome.AUTO_PAY: EXECUTING, Outcome.NEEDS_APPROVAL: PENDING, Outcome.BLOCK: BLOCKED}[decision.outcome]
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO payments (request_id, requester, payee, amount, token, category, memo, created_at,"
                    " outcome, reasons, messages, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        req.request_id,
                        req.requester,
                        normalize_address(req.payee),
                        str(req.amount),
                        req.token,
                        req.category,
                        req.memo,
                        _iso(req.created_at),
                        decision.outcome.value,
                        json.dumps([r.value for r in decision.reasons]),
                        json.dumps(list(decision.messages), ensure_ascii=False),
                        status,
                        _now(),
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"request {req.request_id} already recorded") from e
        return status

    def _transition(self, request_id: str, allowed_from: tuple[str, ...], to: str, **fields) -> bool:
        sets = ", ".join(f"{k} = ?" for k in fields)
        sql = f"UPDATE payments SET status = ?, updated_at = ?{', ' + sets if sets else ''} WHERE request_id = ? AND status IN ({','.join('?' * len(allowed_from))})"
        with self._lock:
            cur = self._db.execute(sql, (to, _now(), *fields.values(), request_id, *allowed_from))
        return cur.rowcount == 1

    def mark_paid(self, request_id: str, tx_hash: str) -> bool:
        return self._transition(request_id, (EXECUTING,), PAID, tx_hash=tx_hash)

    def mark_failed(self, request_id: str, error: str) -> bool:
        return self._transition(request_id, (EXECUTING,), FAILED, error=error[:500])

    def set_safe_tx_hash(self, request_id: str, safe_tx_hash: str) -> None:
        with self._lock:
            self._db.execute("UPDATE payments SET safe_tx_hash = ? WHERE request_id = ?", (safe_tx_hash, request_id))

    def approve(self, request_id: str, by: str) -> bool:
        return self._transition(request_id, (PENDING,), APPROVED, decided_by=by)

    def reject(self, request_id: str, by: str) -> bool:
        return self._transition(request_id, (PENDING,), REJECTED, decided_by=by)

    # ---- settings (kill switch lives here so it survives restarts and is shared with the console) ----

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @property
    def paused(self) -> bool:
        return self.get_setting("paused", "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.set_setting("paused", "1" if paused else "0")

    # ---- events and outbox ----

    def log_event(self, kind: str, text: str, actor: str = "", data: dict | None = None, at: datetime | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (at, kind, actor, text, data) VALUES (?, ?, ?, ?, ?)",
                (_iso(at) if at else _now(), kind, actor or "", text[:4000], json.dumps(data, ensure_ascii=False) if data is not None else None),
            )
        return int(cur.lastrowid)

    def events(self, kinds: tuple[str, ...] = (), limit: int = 200, before_id: int | None = None) -> list[dict]:
        where, args = [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += list(kinds)
        if before_id:
            where.append("id < ?")
            args.append(before_id)
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        rows = self._db.execute(sql, (*args, limit)).fetchall()
        return [
            {"id": r["id"], "at": r["at"], "kind": r["kind"], "actor": r["actor"], "text": r["text"], "data": json.loads(r["data"]) if r["data"] else None}
            for r in rows
        ]

    def enqueue(self, chat: str, text: str) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO outbox (at, chat, text) VALUES (?, ?, ?)", (_now(), str(chat), text[:4000]))
        return int(cur.lastrowid)

    def outbox(self, unsent_only: bool = True, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM outbox" + (" WHERE sent_at IS NULL AND error IS NULL" if unsent_only else "") + " ORDER BY id" + ("" if unsent_only else " DESC") + " LIMIT ?"
        return [dict(r) for r in self._db.execute(sql, (limit,)).fetchall()]

    def mark_sent(self, outbox_id: int, error: str | None = None) -> None:
        with self._lock:
            if error:
                self._db.execute("UPDATE outbox SET error = ? WHERE id = ?", (error[:500], outbox_id))
            else:
                self._db.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (_now(), outbox_id))

    # ---- reads (LedgerView) ----

    def has_request(self, request_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM payments WHERE request_id = ?", (request_id,)).fetchone() is not None

    def auto_paid_total(self, token: str, start: datetime, end: datetime) -> Decimal:
        rows = self._db.execute(
            f"SELECT amount FROM payments WHERE outcome = ? AND token = ? AND created_at >= ? AND created_at < ?"
            f" AND status IN ({','.join('?' * len(_SPENT))})",
            (Outcome.AUTO_PAY.value, token, _iso(start), _iso(end), *_SPENT),
        ).fetchall()
        return sum((Decimal(r["amount"]) for r in rows), Decimal(0))

    def recent_payments(self, payee: str, token: str, since: datetime) -> list[LedgerEntry]:
        rows = self._db.execute(
            f"SELECT * FROM payments WHERE payee = ? AND token = ? AND created_at >= ?"
            f" AND status IN ({','.join('?' * len(_DUPLICATE_SOURCES))})",
            (normalize_address(payee), token, _iso(since), *_DUPLICATE_SOURCES),
        ).fetchall()
        return [LedgerEntry(self._row(r).request, Outcome(r["outcome"])) for r in rows]

    # ---- reads (reports, console) ----

    def get(self, request_id: str) -> PaymentRecord | None:
        row = self._db.execute("SELECT * FROM payments WHERE request_id = ?", (request_id,)).fetchone()
        return self._row(row) if row else None

    def between(self, start: datetime, end: datetime) -> list[PaymentRecord]:
        rows = self._db.execute(
            "SELECT * FROM payments WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (_iso(start), _iso(end)),
        ).fetchall()
        return [self._row(r) for r in rows]

    def latest(self, limit: int = 100) -> list[PaymentRecord]:
        rows = self._db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def search(
        self,
        status: str | None = None,
        token: str | None = None,
        payee: str | None = None,
        q: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[PaymentRecord]:
        where, args = [], []
        if status:
            where.append(f"status IN ({','.join('?' * len(status.split(',')))})")
            args += status.split(",")
        if token:
            where.append("token = ?")
            args.append(token.upper())
        if payee:
            where.append("payee = ?")
            args.append(normalize_address(payee))
        if q:
            where.append("(request_id LIKE ? OR memo LIKE ? OR category LIKE ? OR requester LIKE ? OR payee LIKE ?)")
            args += [f"%{q}%"] * 5
        if start:
            where.append("created_at >= ?")
            args.append(_iso(start))
        if end:
            where.append("created_at < ?")
            args.append(_iso(end))
        sql = "SELECT * FROM payments" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        return [self._row(r) for r in self._db.execute(sql, (*args, limit)).fetchall()]

    def pending(self) -> list[PaymentRecord]:
        rows = self._db.execute("SELECT * FROM payments WHERE status = ? ORDER BY created_at", (PENDING,)).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> PaymentRecord:
        req = PaymentRequest(
            request_id=r["request_id"],
            requester=r["requester"],
            payee=r["payee"],
            amount=Decimal(r["amount"]),
            token=r["token"],
            created_at=datetime.fromisoformat(r["created_at"]),
            category=r["category"],
            memo=r["memo"],
        )
        return PaymentRecord(
            request=req,
            outcome=Outcome(r["outcome"]),
            reasons=tuple(json.loads(r["reasons"])),
            messages=tuple(json.loads(r["messages"])),
            status=r["status"],
            tx_hash=r["tx_hash"],
            safe_tx_hash=r["safe_tx_hash"],
            error=r["error"],
            decided_by=r["decided_by"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
