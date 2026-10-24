"""Persistence for the AI workforce: agents, workspaces, tasks, the message bus, approvals,
memory, schedules, skill state and on-chain records.

Lives in the same SQLite file as the ledger and goes through the ledger's serialized
connection, so the bot, the console and the workforce engine never interleave statements.
Every state change is a row update; nothing important lives only in memory, so a restart
picks up where it left off.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from agent.store import Store, _iso, _now

# ---- task lifecycle ----
PENDING, ASSIGNED, RUNNING, WAITING, REVIEW = "pending", "assigned", "running", "waiting", "review"
COMPLETED, FAILED, CANCELLED = "completed", "failed", "cancelled"
TASK_STATUSES = (PENDING, ASSIGNED, RUNNING, WAITING, REVIEW, COMPLETED, FAILED, CANCELLED)
FINAL = (COMPLETED, FAILED, CANCELLED)

# ---- agent lifecycle ----
AGENT_ACTIVE, AGENT_PAUSED, AGENT_ARCHIVED = "active", "paused", "archived"

# ---- timeline entry kinds (never all rendered as chat bubbles) ----
MSG_KINDS = ("user", "agent", "system", "task", "tool", "chain")
# Agent-to-agent message lifecycle
QUEUED, DELIVERED, PROCESSED, MSG_FAILED, EXPIRED = "queued", "delivered", "processed", "failed", "expired"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT 'none',      -- none | anthropic
    model         TEXT NOT NULL DEFAULT '',
    model_config  TEXT NOT NULL DEFAULT '{}',
    skills        TEXT NOT NULL DEFAULT '[]',
    permissions   TEXT NOT NULL DEFAULT '[]',
    is_manager    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',    -- active | paused | archived
    budget_usd    TEXT NOT NULL DEFAULT '0',         -- 0 = no per-agent cap
    cost_usd      TEXT NOT NULL DEFAULT '0',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    onchain       TEXT NOT NULL DEFAULT '{}',        -- {"agent_id": ERC-8004 id, "address": ...}
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wf_workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    manager_id  TEXT,
    members     TEXT NOT NULL DEFAULT '[]',          -- agent ids
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wf_tasks (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    parent_id     TEXT,
    root_id       TEXT,
    kind          TEXT NOT NULL DEFAULT 'goal',      -- goal | subtask
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    skill         TEXT,
    input         TEXT NOT NULL DEFAULT '{}',
    assigned_agent TEXT,
    priority      INTEGER NOT NULL DEFAULT 2,        -- 0 urgent .. 3 low
    status        TEXT NOT NULL,
    depends_on    TEXT NOT NULL DEFAULT '[]',
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 2,
    result        TEXT,
    result_hash   TEXT,
    error         TEXT,
    cost_usd      TEXT NOT NULL DEFAULT '0',
    tokens        INTEGER NOT NULL DEFAULT 0,
    steps         INTEGER NOT NULL DEFAULT 0,
    max_steps     INTEGER NOT NULL DEFAULT 20,
    budget_usd    TEXT NOT NULL DEFAULT '1',
    timeout_s     INTEGER NOT NULL DEFAULT 600,
    deadline_at   TEXT,
    planner       TEXT,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wf_tasks_root ON wf_tasks(root_id, status);
CREATE INDEX IF NOT EXISTS wf_tasks_ws ON wf_tasks(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS wf_tasks_status ON wf_tasks(status, priority);
CREATE TABLE IF NOT EXISTS wf_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    task_id      TEXT,
    kind         TEXT NOT NULL,                      -- user | agent | system | task | tool | chain
    event        TEXT NOT NULL DEFAULT '',           -- e.g. task.assigned, tool.done, agent.started
    sender       TEXT NOT NULL DEFAULT '',           -- agent id, 'user' or 'system'
    recipient    TEXT,                               -- agent id for directed messages
    text         TEXT NOT NULL DEFAULT '',
    data         TEXT,
    status       TEXT,                               -- for directed messages: queued | delivered | processed | failed | expired
    dedupe_key   TEXT UNIQUE,
    attempts     INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS wf_messages_ws ON wf_messages(workspace_id, id);
CREATE INDEX IF NOT EXISTS wf_messages_task ON wf_messages(task_id, id);
CREATE INDEX IF NOT EXISTS wf_messages_inbox ON wf_messages(recipient, status, id);
CREATE TABLE IF NOT EXISTS wf_approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    task_id      TEXT,
    kind         TEXT NOT NULL,                      -- task_result | over_budget | chain_write
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | rejected
    requested_by TEXT NOT NULL DEFAULT '',
    decided_by   TEXT,
    decided_at   TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS wf_approvals_status ON wf_approvals(status, id);
CREATE TABLE IF NOT EXISTS wf_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,                       -- agent | workspace | project | user
    owner       TEXT NOT NULL DEFAULT '',            -- agent id / workspace id / '' for project and user
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    expires_at  TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS wf_memory_fts USING fts5(text, content='wf_memory', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS wf_memory_ai AFTER INSERT ON wf_memory BEGIN
  INSERT INTO wf_memory_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS wf_memory_ad AFTER DELETE ON wf_memory BEGIN
  INSERT INTO wf_memory_fts(wf_memory_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS wf_memory_au AFTER UPDATE ON wf_memory BEGIN
  INSERT INTO wf_memory_fts(wf_memory_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO wf_memory_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS wf_schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    cron         TEXT NOT NULL,
    goal         TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    next_run_at  TEXT,
    last_run_at  TEXT,
    last_task_id TEXT,
    last_status  TEXT,
    failures     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wf_schedule_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id  INTEGER NOT NULL,
    at           TEXT NOT NULL,
    task_id      TEXT,
    status       TEXT NOT NULL,
    error        TEXT
);
CREATE TABLE IF NOT EXISTS wf_skill_state (
    skill_id     TEXT PRIMARY KEY,
    installed    INTEGER NOT NULL DEFAULT 1,
    enabled      INTEGER NOT NULL DEFAULT 1,
    version      TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wf_chain_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    task_id      TEXT,
    kind         TEXT NOT NULL,                      -- task_hash
    network      TEXT NOT NULL,
    chain_id     INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    from_addr    TEXT,
    to_addr      TEXT,
    tx_hash      TEXT,
    status       TEXT NOT NULL,                      -- pending | confirmed | failed
    block        INTEGER,
    gas_used     INTEGER,
    gas_price    TEXT,
    fee_wei      TEXT,
    error        TEXT,
    confirmed_at TEXT
);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _load(v: str | None, default: Any = None) -> Any:
    return json.loads(v) if v else default


AGENT_JSON = ("model_config", "skills", "permissions", "onchain")
TASK_JSON = ("input", "depends_on", "result")


class WorkforceDB:
    def __init__(self, store: Store) -> None:
        self.store = store
        # Shares the ledger's serialized connection and lock (same process, same file).
        self._db = store._db
        self._lock = store._lock
        self._db.executescript(SCHEMA)

    # ---- agents ----

    def add_agent(self, **f: Any) -> dict:
        aid = f.pop("id", None) or new_id("agt")
        now = _now()
        row = {"id": aid, "created_at": now, "updated_at": now, **f}
        for k in AGENT_JSON:
            if k in row:
                row[k] = _j(row[k])
        cols = ",".join(row)
        self._db.execute(f"INSERT INTO wf_agents ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return self.agent(aid)

    def update_agent(self, aid: str, **f: Any) -> dict | None:
        if not f:
            return self.agent(aid)
        for k in AGENT_JSON:
            if k in f:
                f[k] = _j(f[k])
        f["updated_at"] = _now()
        sets = ",".join(f"{k} = ?" for k in f)
        self._db.execute(f"UPDATE wf_agents SET {sets} WHERE id = ?", (*f.values(), aid))
        return self.agent(aid)

    def delete_agent(self, aid: str) -> bool:
        return self._db.execute("DELETE FROM wf_agents WHERE id = ?", (aid,)).rowcount == 1

    def agent(self, aid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_agents WHERE id = ?", (aid,)).fetchone()
        return self._agent(r) if r else None

    def agents(self, include_archived: bool = True) -> list[dict]:
        sql = "SELECT * FROM wf_agents" + ("" if include_archived else " WHERE status != 'archived'") + " ORDER BY is_manager DESC, created_at"
        return [self._agent(r) for r in self._db.execute(sql).fetchall()]

    def add_agent_usage(self, aid: str, cost_usd: str, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            r = self._db.execute("SELECT cost_usd FROM wf_agents WHERE id = ?", (aid,)).fetchone()
            if r is None:
                return
            from decimal import Decimal

            total = Decimal(r["cost_usd"]) + Decimal(cost_usd)
            self._db.execute(
                "UPDATE wf_agents SET cost_usd = ?, tokens_in = tokens_in + ?, tokens_out = tokens_out + ? WHERE id = ?",
                (str(total), tokens_in, tokens_out, aid),
            )

    @staticmethod
    def _agent(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in AGENT_JSON:
            d[k] = _load(d[k], {} if k in ("model_config", "onchain") else [])
        d["is_manager"] = bool(d["is_manager"])
        return d

    # ---- workspaces ----

    def add_workspace(self, name: str, description: str, manager_id: str | None, members: list[str]) -> dict:
        wid = new_id("ws")
        self._db.execute(
            "INSERT INTO wf_workspaces (id, name, description, manager_id, members, created_at) VALUES (?,?,?,?,?,?)",
            (wid, name, description, manager_id, _j(members), _now()),
        )
        return self.workspace(wid)

    def update_workspace(self, wid: str, **f: Any) -> dict | None:
        if "members" in f:
            f["members"] = _j(f["members"])
        if f:
            self._db.execute(f"UPDATE wf_workspaces SET {','.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), wid))
        return self.workspace(wid)

    def workspace(self, wid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_workspaces WHERE id = ?", (wid,)).fetchone()
        return self._ws(r) if r else None

    def workspaces(self) -> list[dict]:
        return [self._ws(r) for r in self._db.execute("SELECT * FROM wf_workspaces WHERE archived = 0 ORDER BY created_at").fetchall()]

    @staticmethod
    def _ws(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["members"] = _load(d["members"], [])
        d["archived"] = bool(d["archived"])
        return d

    # ---- tasks ----

    def add_task(self, **f: Any) -> dict:
        tid = f.pop("id", None) or new_id("tsk")
        now = _now()
        row = {"id": tid, "created_at": now, "updated_at": now, "root_id": f.get("root_id") or tid, **f}
        row["root_id"] = row["root_id"] or tid
        for k in TASK_JSON:
            if k in row and row[k] is not None:
                row[k] = _j(row[k])
        self._db.execute(f"INSERT INTO wf_tasks ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return self.task(tid)

    def transition(self, tid: str, allowed_from: Iterable[str], to: str, **f: Any) -> bool:
        """Compare-and-set on status, so two workers can never both claim a task."""
        allowed = tuple(allowed_from)
        for k in TASK_JSON:
            if k in f and f[k] is not None:
                f[k] = _j(f[k])
        f["status"], f["updated_at"] = to, _now()
        if to == RUNNING:
            f.setdefault("started_at", f["updated_at"])
        if to in FINAL:
            f.setdefault("finished_at", f["updated_at"])
        sets = ",".join(f"{k} = ?" for k in f)
        sql = f"UPDATE wf_tasks SET {sets} WHERE id = ? AND status IN ({','.join('?' * len(allowed))})"
        return self._db.execute(sql, (*f.values(), tid, *allowed)).rowcount == 1

    def update_task(self, tid: str, **f: Any) -> None:
        for k in TASK_JSON:
            if k in f and f[k] is not None:
                f[k] = _j(f[k])
        f["updated_at"] = _now()
        self._db.execute(f"UPDATE wf_tasks SET {','.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), tid))

    def bump_task(self, tid: str, steps: int = 0, cost_usd: str = "0", tokens: int = 0) -> dict:
        from decimal import Decimal

        with self._lock:
            r = self._db.execute("SELECT cost_usd FROM wf_tasks WHERE id = ?", (tid,)).fetchone()
            total = Decimal(r["cost_usd"]) + Decimal(cost_usd)
            self._db.execute(
                "UPDATE wf_tasks SET steps = steps + ?, tokens = tokens + ?, cost_usd = ?, updated_at = ? WHERE id = ?",
                (steps, tokens, str(total), _now(), tid),
            )
        return self.task(tid)

    def task(self, tid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_tasks WHERE id = ?", (tid,)).fetchone()
        return self._task(r) if r else None

    def subtasks(self, root_id: str) -> list[dict]:
        rows = self._db.execute("SELECT * FROM wf_tasks WHERE root_id = ? AND id != ? ORDER BY created_at, id", (root_id, root_id)).fetchall()
        return [self._task(r) for r in rows]

    def tasks(self, *, workspace_id: str | None = None, status: list[str] | None = None, agent: str | None = None,
              kind: str | None = None, q: str | None = None, priority: int | None = None, since: datetime | None = None,
              until: datetime | None = None, sort: str = "created_at", desc: bool = True, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        where, args = [], []
        if workspace_id:
            where.append("workspace_id = ?")
            args.append(workspace_id)
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            args += status
        if agent:
            where.append("assigned_agent = ?")
            args.append(agent)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if priority is not None:
            where.append("priority = ?")
            args.append(priority)
        if q:
            where.append("(id LIKE ? OR title LIKE ? OR description LIKE ?)")
            args += [f"%{q}%"] * 3
        if since:
            where.append("created_at >= ?")
            args.append(_iso(since))
        if until:
            where.append("created_at < ?")
            args.append(_iso(until))
        sort = sort if sort in ("created_at", "priority", "status", "title", "cost_usd", "finished_at", "steps") else "created_at"
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._db.execute(f"SELECT COUNT(*) AS n FROM wf_tasks{w}", tuple(args)).fetchone()["n"]
        order = f"CAST({sort} AS REAL)" if sort == "cost_usd" else sort
        rows = self._db.execute(f"SELECT * FROM wf_tasks{w} ORDER BY {order} {'DESC' if desc else 'ASC'}, id LIMIT ? OFFSET ?",
                                (*args, limit, offset)).fetchall()
        return [self._task(r) for r in rows], int(total)

    def task_counts(self, since: datetime | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM wf_tasks" + (" WHERE created_at >= ?" if since else "") + " GROUP BY status"
        return {r["status"]: r["n"] for r in self._db.execute(sql, (_iso(since),) if since else ()).fetchall()}

    def agent_task_stats(self, since: datetime | None = None) -> dict[str, dict]:
        sql = ("SELECT assigned_agent AS a, status, COUNT(*) AS n, SUM(CAST(cost_usd AS REAL)) AS cost,"
               " AVG(CASE WHEN finished_at IS NOT NULL AND started_at IS NOT NULL THEN (julianday(finished_at) - julianday(started_at)) * 86400.0 END) AS secs"
               " FROM wf_tasks WHERE kind = 'subtask'" + (" AND created_at >= ?" if since else "") + " GROUP BY assigned_agent, status")
        out: dict[str, dict] = {}
        for r in self._db.execute(sql, (_iso(since),) if since else ()).fetchall():
            s = out.setdefault(r["a"] or "", {"by_status": {}, "secs": [], "cost": 0.0})
            s["by_status"][r["status"]] = r["n"]
            s["cost"] += r["cost"] or 0.0
            if r["secs"] is not None and r["status"] == COMPLETED:
                s["secs"].append(r["secs"])
        return out

    def stale_tasks(self, statuses: tuple[str, ...], older_than: datetime) -> list[dict]:
        rows = self._db.execute(
            f"SELECT * FROM wf_tasks WHERE status IN ({','.join('?' * len(statuses))}) AND updated_at < ?",
            (*statuses, _iso(older_than)),
        ).fetchall()
        return [self._task(r) for r in rows]

    def open_goals(self) -> list[dict]:
        rows = self._db.execute(f"SELECT * FROM wf_tasks WHERE kind = 'goal' AND status NOT IN ({','.join('?' * len(FINAL))}) ORDER BY priority, created_at", FINAL).fetchall()
        return [self._task(r) for r in rows]

    @staticmethod
    def _task(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["input"] = _load(d["input"], {})
        d["depends_on"] = _load(d["depends_on"], [])
        d["result"] = _load(d["result"])
        return d

    # ---- messages / timeline ----

    def post(self, *, workspace_id: str, kind: str, text: str = "", sender: str = "system", event: str = "",
             task_id: str | None = None, recipient: str | None = None, data: Any = None, dedupe_key: str | None = None,
             status: str | None = None) -> int | None:
        """Append a timeline entry. Returns None when dedupe_key was already used (the message is a duplicate)."""
        assert kind in MSG_KINDS, kind
        try:
            cur = self._db.execute(
                "INSERT INTO wf_messages (at, workspace_id, task_id, kind, event, sender, recipient, text, data, status, dedupe_key, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), workspace_id, task_id, kind, event, sender, recipient, text[:8000],
                 _j(data) if data is not None else None, status or (QUEUED if recipient else None), dedupe_key, _now()),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)

    def message(self, mid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_messages WHERE id = ?", (mid,)).fetchone()
        return self._msg(r) if r else None

    def messages(self, *, workspace_id: str | None = None, task_id: str | None = None, root_id: str | None = None,
                 after: int = 0, before: int | None = None, kinds: list[str] | None = None, sender: str | None = None,
                 limit: int = 200, newest_first: bool = False) -> list[dict]:
        where, args = ["id > ?"], [after]
        if before:
            where.append("id < ?")
            args.append(before)
        if workspace_id:
            where.append("workspace_id = ?")
            args.append(workspace_id)
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        if root_id:
            where.append("task_id IN (SELECT id FROM wf_tasks WHERE root_id = ?)")
            args.append(root_id)
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if sender:
            where.append("(sender = ? OR recipient = ?)")
            args += [sender, sender]
        sql = f"SELECT * FROM wf_messages WHERE {' AND '.join(where)} ORDER BY id {'DESC' if newest_first else 'ASC'} LIMIT ?"
        return [self._msg(r) for r in self._db.execute(sql, (*args, limit)).fetchall()]

    def last_message_id(self) -> int:
        r = self._db.execute("SELECT MAX(id) AS m FROM wf_messages").fetchone()
        return int(r["m"] or 0)

    def inbox(self, recipient: str, limit: int = 20) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM wf_messages WHERE recipient = ? AND status IN (?, ?) ORDER BY id LIMIT ?",
            (recipient, QUEUED, DELIVERED, limit),
        ).fetchall()
        return [self._msg(r) for r in rows]

    def set_message_status(self, mid: int, allowed_from: tuple[str, ...], to: str, bump: bool = False) -> bool:
        sql = (f"UPDATE wf_messages SET status = ?, updated_at = ?{', attempts = attempts + 1' if bump else ''}"
               f" WHERE id = ? AND status IN ({','.join('?' * len(allowed_from))})")
        return self._db.execute(sql, (to, _now(), mid, *allowed_from)).rowcount == 1

    def stale_messages(self, older_than: datetime) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM wf_messages WHERE recipient IS NOT NULL AND status IN (?, ?) AND updated_at < ?",
            (QUEUED, DELIVERED, _iso(older_than)),
        ).fetchall()
        return [self._msg(r) for r in rows]

    @staticmethod
    def _msg(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["data"] = _load(d["data"])
        return d

    # ---- approvals ----

    def add_approval(self, *, workspace_id: str, task_id: str | None, kind: str, title: str, detail: dict, requested_by: str) -> int:
        cur = self._db.execute(
            "INSERT INTO wf_approvals (at, workspace_id, task_id, kind, title, detail, requested_by) VALUES (?,?,?,?,?,?,?)",
            (_now(), workspace_id, task_id, kind, title, _j(detail), requested_by),
        )
        return int(cur.lastrowid)

    def decide_approval(self, aid: int, status: str, by: str, note: str = "") -> bool:
        return self._db.execute(
            "UPDATE wf_approvals SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ? AND status = 'pending'",
            (status, by, _now(), note[:500] or None, aid),
        ).rowcount == 1

    def approval(self, aid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_approvals WHERE id = ?", (aid,)).fetchone()
        return self._approval(r) if r else None

    def approvals(self, status: str | None = None, task_id: str | None = None, limit: int = 100) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if task_id:
            where.append("task_id IN (SELECT id FROM wf_tasks WHERE root_id = ?)")
            args.append(task_id)
        sql = "SELECT * FROM wf_approvals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return [self._approval(r) for r in self._db.execute(sql, (*args, limit)).fetchall()]

    @staticmethod
    def _approval(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["detail"] = _load(d["detail"], {})
        return d

    # ---- memory ----

    def remember(self, scope: str, owner: str, text: str, source: str = "", ttl_days: int | None = None) -> int:
        expires = _iso(datetime.now(timezone.utc) + timedelta(days=ttl_days)) if ttl_days else None
        cur = self._db.execute(
            "INSERT INTO wf_memory (scope, owner, text, source, created_at, expires_at) VALUES (?,?,?,?,?,?)",
            (scope, owner, text[:8000], source, _now(), expires),
        )
        return int(cur.lastrowid)

    def update_memory(self, mid: int, text: str | None = None, ttl_days: int | None = None) -> bool:
        f: dict[str, Any] = {}
        if text is not None:
            f["text"] = text[:8000]
        if ttl_days is not None:
            f["expires_at"] = _iso(datetime.now(timezone.utc) + timedelta(days=ttl_days)) if ttl_days > 0 else None
        if not f:
            return False
        return self._db.execute(f"UPDATE wf_memory SET {','.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), mid)).rowcount == 1

    def forget(self, mid: int) -> bool:
        return self._db.execute("DELETE FROM wf_memory WHERE id = ?", (mid,)).rowcount == 1

    def memory(self, mid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_memory WHERE id = ?", (mid,)).fetchone()
        return dict(r) if r else None

    def search_memory(self, scopes: list[tuple[str, str]], q: str = "", limit: int = 50) -> list[dict]:
        """Memories visible through the given (scope, owner) pairs. Expired rows are never returned."""
        if not scopes:
            return []
        vis = " OR ".join("(m.scope = ? AND m.owner = ?)" for _ in scopes)
        args: list[Any] = [x for pair in scopes for x in pair]
        now = _now()
        if q.strip() and len(q.strip()) >= 3:
            sql = (f"SELECT m.* FROM wf_memory m JOIN wf_memory_fts f ON f.rowid = m.id WHERE wf_memory_fts MATCH ?"
                   f" AND ({vis}) AND (m.expires_at IS NULL OR m.expires_at > ?) ORDER BY m.id DESC LIMIT ?")
            phrase = '"' + q.strip().replace('"', '""') + '"'
            rows = self._db.execute(sql, (phrase, *args, now, limit)).fetchall()
        else:
            like = f"%{q.strip()}%"
            sql = f"SELECT m.* FROM wf_memory m WHERE ({vis}) AND m.text LIKE ? AND (m.expires_at IS NULL OR m.expires_at > ?) ORDER BY m.id DESC LIMIT ?"
            rows = self._db.execute(sql, (*args, like, now, limit)).fetchall()
        return [dict(r) for r in rows]

    def purge_expired_memory(self) -> int:
        return self._db.execute("DELETE FROM wf_memory WHERE expires_at IS NOT NULL AND expires_at <= ?", (_now(),)).rowcount

    # ---- schedules ----

    def add_schedule(self, workspace_id: str, name: str, cron: str, goal: str, next_run_at: datetime) -> dict:
        cur = self._db.execute(
            "INSERT INTO wf_schedules (workspace_id, name, cron, goal, next_run_at, created_at) VALUES (?,?,?,?,?,?)",
            (workspace_id, name, cron, goal, _iso(next_run_at), _now()),
        )
        return self.schedule(int(cur.lastrowid))

    def schedule(self, sid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM wf_schedules WHERE id = ?", (sid,)).fetchone()
        return dict(r) if r else None

    def schedules(self) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM wf_schedules ORDER BY id").fetchall()]

    def update_schedule(self, sid: int, **f: Any) -> None:
        for k in ("next_run_at", "last_run_at"):
            if isinstance(f.get(k), datetime):
                f[k] = _iso(f[k])
        self._db.execute(f"UPDATE wf_schedules SET {','.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), sid))

    def delete_schedule(self, sid: int) -> bool:
        return self._db.execute("DELETE FROM wf_schedules WHERE id = ?", (sid,)).rowcount == 1

    def due_schedules(self, now: datetime) -> list[dict]:
        rows = self._db.execute("SELECT * FROM wf_schedules WHERE enabled = 1 AND next_run_at <= ? ORDER BY next_run_at", (_iso(now),)).fetchall()
        return [dict(r) for r in rows]

    def add_schedule_run(self, sid: int, task_id: str | None, status: str, error: str | None = None) -> None:
        self._db.execute("INSERT INTO wf_schedule_runs (schedule_id, at, task_id, status, error) VALUES (?,?,?,?,?)",
                         (sid, _now(), task_id, status, error))

    def schedule_runs(self, sid: int, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM wf_schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?", (sid, limit)).fetchall()]

    # ---- skill state ----

    def skill_states(self) -> dict[str, dict]:
        return {r["skill_id"]: dict(r) for r in self._db.execute("SELECT * FROM wf_skill_state").fetchall()}

    def set_skill_state(self, skill_id: str, installed: bool, enabled: bool, version: str) -> None:
        self._db.execute(
            "INSERT INTO wf_skill_state (skill_id, installed, enabled, version, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(skill_id) DO UPDATE SET installed = excluded.installed, enabled = excluded.enabled,"
            " version = excluded.version, updated_at = excluded.updated_at",
            (skill_id, int(installed), int(enabled), version, _now()),
        )

    # ---- on-chain records ----

    def add_chain_record(self, **f: Any) -> int:
        row = {"at": _now(), **f}
        cur = self._db.execute(f"INSERT INTO wf_chain_records ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return int(cur.lastrowid)

    def update_chain_record(self, rid: int, **f: Any) -> None:
        self._db.execute(f"UPDATE wf_chain_records SET {','.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), rid))

    def chain_records(self, status: str | None = None, task_id: str | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        sql = "SELECT * FROM wf_chain_records" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self._db.execute(sql, (*args, limit)).fetchall()]
