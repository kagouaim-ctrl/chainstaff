"""What the BSC listener keeps: the recent canonical blocks, the monitors, the events they matched, and the
cursor. Same SQLite file and serialized connection as the ledger and the workforce, new tables only.

Blocks are kept only for the last few hundred heights, enough to find a common ancestor after a reorg.
Events stay; one removed by a reorg is marked `reorged`, never deleted, so the timeline stays honest.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from agent.store import Store, _now
from agent.workforce.db import new_id

PENDING, CONFIRMED, REORGED = "pending", "confirmed", "reorged"
MONITOR_KINDS = {"address": "地址监控（BNB 转账 + 代币转入转出）", "contract": "合约事件", "token_transfer": "代币转账"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_blocks (
    number      INTEGER PRIMARY KEY,
    hash        TEXT NOT NULL,
    parent_hash TEXT NOT NULL,
    timestamp   INTEGER NOT NULL DEFAULT 0,
    tx_count    INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_monitors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,                 -- address | contract | token_transfer
    target      TEXT NOT NULL,                 -- lower-case address
    topics      TEXT NOT NULL DEFAULT '[]',    -- contract: topic0 filter (empty = every event)
    min_value   TEXT NOT NULL DEFAULT '0',     -- token_transfer / address: ignore smaller raw amounts
    enabled     INTEGER NOT NULL DEFAULT 1,
    trigger     TEXT NOT NULL DEFAULT '{}',    -- {"workspace_id", "goal", "on": "confirmed"|"pending"}: start a mission per event
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,                 -- native_transfer | token_transfer | log
    block       INTEGER NOT NULL,
    block_hash  TEXT NOT NULL,
    tx_hash     TEXT NOT NULL,
    log_index   INTEGER NOT NULL DEFAULT -1,   -- -1 for a native transfer
    address     TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending',
    task_id     TEXT,                          -- mission this event started, if its monitor has a trigger
    seen_ms     REAL NOT NULL DEFAULT 0,       -- wall clock (ms) when the listener first saw the block
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (monitor_id, block_hash, tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS chain_events_block ON chain_events(block);
CREATE INDEX IF NOT EXISTS chain_events_monitor ON chain_events(monitor_id, id);
CREATE INDEX IF NOT EXISTS chain_events_tx ON chain_events(tx_hash);
CREATE TABLE IF NOT EXISTS chain_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ChainStore:
    def __init__(self, store: Store) -> None:
        self._db = store._db
        self._lock = store._lock
        self._db.executescript(SCHEMA)

    # ---- cursor ----

    def get(self, key: str, default: Any = None) -> Any:
        r = self._db.execute("SELECT value FROM chain_state WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, key: str, value: Any) -> None:
        self._db.execute("INSERT INTO chain_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    # ---- blocks ----

    def put_block(self, number: int, hash_: str, parent: str, timestamp: int, tx_count: int) -> None:
        self._db.execute("INSERT INTO chain_blocks(number,hash,parent_hash,timestamp,tx_count,received_at) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(number) DO UPDATE SET hash=excluded.hash, parent_hash=excluded.parent_hash, "
                         "timestamp=excluded.timestamp, tx_count=excluded.tx_count, received_at=excluded.received_at",
                         (number, hash_.lower(), parent.lower(), timestamp, tx_count, _now()))

    def block_hash(self, number: int) -> str | None:
        r = self._db.execute("SELECT hash FROM chain_blocks WHERE number=?", (number,)).fetchone()
        return r["hash"] if r else None

    def head(self) -> dict | None:
        r = self._db.execute("SELECT * FROM chain_blocks ORDER BY number DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def drop_blocks_above(self, number: int) -> None:
        self._db.execute("DELETE FROM chain_blocks WHERE number>?", (number,))

    def prune_blocks(self, keep: int) -> None:
        h = self.head()
        if h:
            self._db.execute("DELETE FROM chain_blocks WHERE number<?", (h["number"] - keep,))

    # ---- monitors ----

    def add_monitor(self, *, name: str, kind: str, target: str, topics: list[str] | None = None, min_value: str = "0",
                    trigger: dict | None = None, enabled: bool = True) -> dict:
        mid = new_id("mon")
        now = _now()
        self._db.execute("INSERT INTO chain_monitors(id,name,kind,target,topics,min_value,enabled,trigger,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)", (mid, name, kind, target.lower(), json.dumps([t.lower() for t in topics or []]),
                                                        str(min_value), int(enabled), json.dumps(trigger or {}, ensure_ascii=False), now, now))
        return self.monitor(mid)

    def update_monitor(self, mid: str, **f: Any) -> dict | None:
        if "topics" in f:
            f["topics"] = json.dumps([t.lower() for t in f["topics"] or []])
        if "trigger" in f:
            f["trigger"] = json.dumps(f["trigger"] or {}, ensure_ascii=False)
        if "enabled" in f:
            f["enabled"] = int(bool(f["enabled"]))
        if "target" in f:
            f["target"] = f["target"].lower()
        if f:
            cols = ",".join(f"{k}=?" for k in f)
            self._db.execute(f"UPDATE chain_monitors SET {cols}, updated_at=? WHERE id=?", (*f.values(), _now(), mid))
        return self.monitor(mid)

    def delete_monitor(self, mid: str) -> bool:
        return self._db.execute("DELETE FROM chain_monitors WHERE id=?", (mid,)).rowcount > 0

    def monitor(self, mid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM chain_monitors WHERE id=?", (mid,)).fetchone()
        return self._mon(r) if r else None

    def monitors(self, enabled_only: bool = False) -> list[dict]:
        q = "SELECT * FROM chain_monitors" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY created_at"
        return [self._mon(r) for r in self._db.execute(q).fetchall()]

    @staticmethod
    def _mon(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["topics"] = json.loads(d["topics"])
        d["trigger"] = json.loads(d["trigger"])
        d["enabled"] = bool(d["enabled"])
        return d

    # ---- events ----

    def add_event(self, *, monitor_id: str, kind: str, block: int, block_hash: str, tx_hash: str, log_index: int, address: str,
                  data: dict, seen_ms: float | None = None) -> dict | None:
        """Insert once; returns None when the same event (same monitor, block hash, tx, log) is already stored."""
        now = _now()
        cur = self._db.execute("INSERT OR IGNORE INTO chain_events(monitor_id,kind,block,block_hash,tx_hash,log_index,address,data,status,"
                               "seen_ms,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (monitor_id, kind, block, block_hash.lower(), tx_hash.lower(), log_index, address.lower(),
                                json.dumps(data, ensure_ascii=False), PENDING, seen_ms or time.time() * 1000, now, now))
        if not cur.rowcount:
            return None
        return self.event(cur.lastrowid)

    def event(self, eid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM chain_events WHERE id=?", (eid,)).fetchone()
        return self._ev(r) if r else None

    def set_event_task(self, eid: int, task_id: str) -> None:
        self._db.execute("UPDATE chain_events SET task_id=?, updated_at=? WHERE id=?", (task_id, _now(), eid))

    def confirm_up_to(self, block: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM chain_events WHERE status=? AND block<=? ORDER BY id", (PENDING, block)).fetchall()
            if rows:
                self._db.execute("UPDATE chain_events SET status=?, updated_at=? WHERE status=? AND block<=?", (CONFIRMED, _now(), PENDING, block))
        return [{**self._ev(r), "status": CONFIRMED} for r in rows]

    def reorg_above(self, block: int) -> list[dict]:
        """Every not-yet-reorged event in a block above `block` is no longer on the canonical chain."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM chain_events WHERE status!=? AND block>? ORDER BY id", (REORGED, block)).fetchall()
            if rows:
                self._db.execute("UPDATE chain_events SET status=?, updated_at=? WHERE status!=? AND block>?", (REORGED, _now(), REORGED, block))
        return [{**self._ev(r), "status": REORGED} for r in rows]

    def events(self, *, monitor_id: str | None = None, address: str | None = None, tx_hash: str | None = None, status: str | None = None,
               kind: str | None = None, from_block: int | None = None, to_block: int | None = None, after_id: int | None = None,
               limit: int = 100) -> list[dict]:
        where, args = [], []
        for col, v in (("monitor_id", monitor_id), ("tx_hash", tx_hash and tx_hash.lower()), ("status", status), ("kind", kind)):
            if v:
                where.append(f"{col}=?")
                args.append(v)
        if address:
            a = address.lower()
            where.append("(address=? OR json_extract(data,'$.from')=? OR json_extract(data,'$.to')=?)")
            args += [a, a, a]
        if from_block is not None:
            where.append("block>=?")
            args.append(from_block)
        if to_block is not None:
            where.append("block<=?")
            args.append(to_block)
        if after_id is not None:
            where.append("id>?")
            args.append(after_id)
        q = "SELECT * FROM chain_events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return [self._ev(r) for r in self._db.execute(q, (*args, max(1, min(limit, 1000)))).fetchall()]

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._db.execute("SELECT status, COUNT(*) n FROM chain_events GROUP BY status").fetchall()}

    @staticmethod
    def _ev(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["data"] = json.loads(d["data"])
        return d
