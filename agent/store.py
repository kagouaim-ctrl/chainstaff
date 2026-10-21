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
-- Community support hand-offs to a human admin.
CREATE TABLE IF NOT EXISTS tickets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    chat       TEXT NOT NULL DEFAULT '',
    user       TEXT NOT NULL,
    text       TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',   -- open | closed
    closed_at  TEXT,
    closed_by  TEXT,
    note       TEXT
);
-- Announcement translations: drafted by the translator, posted only after the owner approves.
CREATE TABLE IF NOT EXISTS translations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    source      TEXT NOT NULL,
    outputs     TEXT NOT NULL,                 -- JSON {lang: text}
    warnings    TEXT NOT NULL DEFAULT '[]',    -- JSON list
    status      TEXT NOT NULL DEFAULT 'draft', -- draft | posted | discarded
    created_by  TEXT NOT NULL DEFAULT '',
    decided_by  TEXT,
    decided_at  TEXT
);
-- Batch payouts (campaign rewards, KOL fees): computed by agent/rules/batch.py, signed in the Safe.
CREATE TABLE IF NOT EXISTS payout_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    campaign    TEXT NOT NULL,
    token       TEXT NOT NULL,
    rules       TEXT NOT NULL,                 -- JSON
    items       TEXT NOT NULL,                 -- JSON list
    summary     TEXT NOT NULL,                 -- JSON
    source      TEXT NOT NULL DEFAULT '',      -- the list as pasted
    status      TEXT NOT NULL DEFAULT 'draft', -- draft | proposed | executed | cancelled
    created_by  TEXT NOT NULL DEFAULT '',
    decided_by  TEXT,
    decided_at  TEXT,
    tx_hash     TEXT,
    note        TEXT
);
-- Airdrop sybil checks and the on-chain profiles they read (cached).
CREATE TABLE IF NOT EXISTS sybil_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    name        TEXT NOT NULL,
    settings    TEXT NOT NULL,                 -- JSON
    addresses   TEXT NOT NULL,                 -- JSON list
    status      TEXT NOT NULL DEFAULT 'running', -- running | done | failed
    progress    INTEGER NOT NULL DEFAULT 0,
    result      TEXT,                          -- JSON
    error       TEXT,
    created_by  TEXT NOT NULL DEFAULT '',
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS address_profiles (
    address     TEXT PRIMARY KEY,
    data        TEXT NOT NULL,                 -- JSON
    fetched_at  TEXT NOT NULL
);
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
        # Databases created before the AI team existed have no employee column on events.
        if "employee" not in {r["name"] for r in self._db.execute("PRAGMA table_info(events)").fetchall()}:
            self._db.execute("ALTER TABLE events ADD COLUMN employee TEXT NOT NULL DEFAULT ''")
            self._db.execute("CREATE INDEX IF NOT EXISTS events_employee ON events(employee, id)")

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

    def log_event(self, kind: str, text: str, actor: str = "", data: dict | None = None, at: datetime | None = None, employee: str = "") -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (at, kind, actor, text, data, employee) VALUES (?, ?, ?, ?, ?, ?)",
                (_iso(at) if at else _now(), kind, actor or "", text[:4000], json.dumps(data, ensure_ascii=False) if data is not None else None, employee),
            )
        return int(cur.lastrowid)

    def events(self, kinds: tuple[str, ...] = (), limit: int = 200, before_id: int | None = None,
               employee: str | None = None, since: datetime | None = None) -> list[dict]:
        where, args = [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += list(kinds)
        if employee is not None:
            where.append("employee = ?")
            args.append(employee)
        if since:
            where.append("at >= ?")
            args.append(_iso(since))
        if before_id:
            where.append("id < ?")
            args.append(before_id)
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        rows = self._db.execute(sql, (*args, limit)).fetchall()
        return [
            {"id": r["id"], "at": r["at"], "kind": r["kind"], "actor": r["actor"], "text": r["text"],
             "data": json.loads(r["data"]) if r["data"] else None, "employee": r["employee"]}
            for r in rows
        ]

    def event_counts(self, since: datetime) -> dict[str, dict[str, int]]:
        """{employee: {kind: n}} since a time, for the team page."""
        rows = self._db.execute(
            "SELECT employee, kind, COUNT(*) AS n FROM events WHERE at >= ? GROUP BY employee, kind", (_iso(since),)
        ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["employee"], {})[r["kind"]] = r["n"]
        return out

    # ---- support tickets ----

    def open_ticket(self, user: str, text: str, reason: str = "", chat: str = "") -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO tickets (at, chat, user, text, reason) VALUES (?, ?, ?, ?, ?)", (_now(), str(chat), user, text[:2000], reason)
            )
        return int(cur.lastrowid)

    def tickets(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM tickets" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self._db.execute(sql, ((status, limit) if status else (limit,))).fetchall()]

    def close_ticket(self, ticket_id: int, by: str, note: str = "") -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?, note = ? WHERE id = ? AND status = 'open'",
                (_now(), by, note[:500] or None, ticket_id),
            )
        return cur.rowcount == 1

    # ---- translation drafts ----

    def add_translation(self, source: str, outputs: dict[str, str], warnings: list[str], created_by: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO translations (at, source, outputs, warnings, created_by) VALUES (?, ?, ?, ?, ?)",
                (_now(), source[:8000], json.dumps(outputs, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), created_by),
            )
        return int(cur.lastrowid)

    def translation(self, tid: int) -> dict | None:
        row = self._db.execute("SELECT * FROM translations WHERE id = ?", (tid,)).fetchone()
        return self._translation(row) if row else None

    def translations(self, limit: int = 50) -> list[dict]:
        return [self._translation(r) for r in self._db.execute("SELECT * FROM translations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def update_translation(self, tid: int, outputs: dict[str, str], warnings: list[str]) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE translations SET outputs = ?, warnings = ? WHERE id = ? AND status = 'draft'",
                (json.dumps(outputs, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), tid),
            )
        return cur.rowcount == 1

    def decide_translation(self, tid: int, status: str, by: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE translations SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'draft'",
                (status, by, _now(), tid),
            )
        return cur.rowcount == 1

    @staticmethod
    def _translation(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["outputs"] = json.loads(d["outputs"])
        d["warnings"] = json.loads(d["warnings"])
        return d

    # ---- payout batches ----

    def add_batch(self, campaign: str, token: str, rules: dict, items: list[dict], summary: dict, source: str, created_by: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO payout_batches (at, campaign, token, rules, items, summary, source, created_by) VALUES (?,?,?,?,?,?,?,?)",
                (_now(), campaign[:80], token, json.dumps(rules), json.dumps(items, ensure_ascii=False),
                 json.dumps(summary, ensure_ascii=False), source[:200_000], created_by),
            )
        return int(cur.lastrowid)

    def batch(self, bid: int) -> dict | None:
        row = self._db.execute("SELECT * FROM payout_batches WHERE id = ?", (bid,)).fetchone()
        return self._batch(row) if row else None

    def batches(self, limit: int = 50, campaign: str | None = None) -> list[dict]:
        if campaign is None:
            rows = self._db.execute("SELECT * FROM payout_batches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM payout_batches WHERE campaign = ? ORDER BY id DESC LIMIT ?", (campaign, limit)).fetchall()
        return [self._batch(r) for r in rows]

    def set_batch_status(self, bid: int, allowed_from: tuple[str, ...], status: str, by: str,
                         tx_hash: str | None = None, note: str | None = None) -> bool:
        marks = ",".join("?" for _ in allowed_from)
        with self._lock:
            cur = self._db.execute(
                f"UPDATE payout_batches SET status = ?, decided_by = ?, decided_at = ?, tx_hash = COALESCE(?, tx_hash),"
                f" note = COALESCE(?, note) WHERE id = ? AND status IN ({marks})",
                (status, by, _now(), tx_hash, note, bid, *allowed_from),
            )
        return cur.rowcount == 1

    @staticmethod
    def _batch(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("rules", "items", "summary"):
            d[k] = json.loads(d[k])
        return d

    # ---- sybil checks ----

    def add_sybil_check(self, name: str, settings: dict, addresses: list[str], created_by: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO sybil_checks (at, name, settings, addresses, created_by) VALUES (?,?,?,?,?)",
                (_now(), name[:80], json.dumps(settings, ensure_ascii=False), json.dumps(addresses), created_by),
            )
        return int(cur.lastrowid)

    def sybil_progress(self, cid: int, progress: int) -> None:
        self._db.execute("UPDATE sybil_checks SET progress = ? WHERE id = ?", (progress, cid))

    def finish_sybil_check(self, cid: int, result: dict | None, error: str | None = None) -> None:
        self._db.execute(
            "UPDATE sybil_checks SET status = ?, result = ?, error = ?, finished_at = ? WHERE id = ?",
            ("failed" if error else "done", json.dumps(result, ensure_ascii=False) if result is not None else None, error, _now(), cid),
        )

    def sybil_check(self, cid: int) -> dict | None:
        row = self._db.execute("SELECT * FROM sybil_checks WHERE id = ?", (cid,)).fetchone()
        return self._sybil(row) if row else None

    def sybil_checks(self, limit: int = 30) -> list[dict]:
        return [self._sybil(r) for r in self._db.execute("SELECT * FROM sybil_checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    @staticmethod
    def _sybil(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["settings"] = json.loads(d["settings"])
        d["addresses"] = json.loads(d["addresses"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def profile(self, address: str, max_age_s: float | None = None) -> dict | None:
        row = self._db.execute("SELECT data, fetched_at FROM address_profiles WHERE address = ?", (normalize_address(address),)).fetchone()
        if not row:
            return None
        if max_age_s is not None:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
            if age > max_age_s:
                return None
        return json.loads(row["data"])

    def save_profile(self, address: str, data: dict) -> None:
        self._db.execute(
            "INSERT INTO address_profiles (address, data, fetched_at) VALUES (?, ?, ?)"
            " ON CONFLICT(address) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
            (normalize_address(address), json.dumps(data), _now()),
        )

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
