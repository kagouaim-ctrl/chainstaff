"""Persistence for autonomous agents: missions, structured tasks (the queue), runs and their steps, events,
watchers, activity, notifications, approvals, artifacts and worker heartbeats.

Same SQLite file and serialized connection as the ledger and the workforce. Agent and workspace configuration
stay in wf_agents / wf_workspaces (new columns added here), so agents created before this module keep working.
Every write that matters is a row, and every claim is a compare-and-set, so the API process and any number of
worker processes can share the file and a crashed worker loses nothing but its lease.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from agent.store import Store, _iso, _now
from agent.workforce.db import WorkforceDB, new_id

from . import constants as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS at_missions (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    goal          TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL,                     -- one_time | continuous | event_driven | scheduled
    lead_agent_id TEXT,                              -- woken first when the mission triggers
    agent_ids     TEXT NOT NULL DEFAULT '[]',
    triggers      TEXT NOT NULL DEFAULT '[]',        -- [{"watcher_id"?, "kind"?, "types"?: [...], "min_severity", "task_kind"}]
    schedule_cron TEXT,
    next_run_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'active',    -- active | paused | stopped | completed | failed
    permissions   TEXT NOT NULL DEFAULT '[]',        -- extra limits for the mission's tasks: a subset of what agents hold
    budget_usd    TEXT NOT NULL DEFAULT '0',         -- 0 = no mission cap
    cost_usd      TEXT NOT NULL DEFAULT '0',
    result        TEXT,
    run_count     INTEGER NOT NULL DEFAULT 0,
    last_run_at   TEXT,
    responsibility_of TEXT,                          -- agent id when this is that agent's standing responsibility
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS at_missions_ws ON at_missions(workspace_id, status);

CREATE TABLE IF NOT EXISTS at_tasks (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    mission_id    TEXT,
    from_agent_id TEXT,                              -- NULL: the system (an event) or the user
    to_agent_id   TEXT,                              -- NULL while unassigned
    kind          TEXT NOT NULL DEFAULT 'general',
    objective     TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 2,        -- 0 urgent .. 3 low
    evidence      TEXT NOT NULL DEFAULT '{}',
    required      TEXT NOT NULL DEFAULT '[]',        -- permissions the target must hold
    status        TEXT NOT NULL,
    result        TEXT,
    error         TEXT,
    parent_id     TEXT,
    root_id       TEXT NOT NULL,
    depth         INTEGER NOT NULL DEFAULT 0,
    chain         TEXT NOT NULL DEFAULT '[]',        -- agent ids from the root down to this task's sender
    follow_up     INTEGER NOT NULL DEFAULT 0,        -- wake the sender again with this task's result
    waiting_on    TEXT NOT NULL DEFAULT '[]',        -- child task ids this task's run is waiting for
    event_id      TEXT,
    fingerprint   TEXT,                              -- what the work is about (an issue), for dedupe and fix caps
    dedupe_key    TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 2,
    budget_usd    TEXT NOT NULL DEFAULT '0.5',
    cost_usd      TEXT NOT NULL DEFAULT '0',
    tokens        INTEGER NOT NULL DEFAULT 0,
    lease_owner   TEXT,
    lease_until   TEXT,
    not_before    TEXT,                              -- a retried task waits until then
    checkpoint    TEXT,                              -- the run's conversation so far, to resume after a wait or a crash
    deadline_at   TEXT,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS at_tasks_queue ON at_tasks(status, priority, created_at);
CREATE INDEX IF NOT EXISTS at_tasks_agent ON at_tasks(to_agent_id, status);
CREATE INDEX IF NOT EXISTS at_tasks_root ON at_tasks(root_id);
CREATE INDEX IF NOT EXISTS at_tasks_parent ON at_tasks(parent_id);
CREATE INDEX IF NOT EXISTS at_tasks_mission ON at_tasks(mission_id, created_at);
CREATE INDEX IF NOT EXISTS at_tasks_dedupe ON at_tasks(dedupe_key, status);

CREATE TABLE IF NOT EXISTS at_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    workspace_id  TEXT NOT NULL,
    mission_id    TEXT,
    worker_id     TEXT,
    attempt       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL,                     -- running | completed | failed | waiting | interrupted | cancelled
    resumed       INTEGER NOT NULL DEFAULT 0,        -- started from a checkpoint
    provider      TEXT,
    model         TEXT,
    model_calls   INTEGER NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cost_usd      TEXT NOT NULL DEFAULT '0',
    summary       TEXT,
    error         TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS at_runs_task ON at_runs(task_id, started_at);
CREATE INDEX IF NOT EXISTS at_runs_agent ON at_runs(agent_id, started_at);

CREATE TABLE IF NOT EXISTS at_run_steps (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL,
    at        TEXT NOT NULL,
    kind      TEXT NOT NULL,                         -- model | tool | note | error
    name      TEXT NOT NULL DEFAULT '',
    ok        INTEGER NOT NULL DEFAULT 1,
    secs      REAL,
    input     TEXT,
    output    TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd  TEXT NOT NULL DEFAULT '0'
);
CREATE INDEX IF NOT EXISTS at_run_steps_run ON at_run_steps(run_id, id);

CREATE TABLE IF NOT EXISTS at_events (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,                     -- watcher kind, or manual / system
    watcher_id    TEXT,
    workspace_id  TEXT NOT NULL,
    type          TEXT NOT NULL,                     -- app.error, ci.failed, health.down, github.push, bsc.transfer, ...
    severity      TEXT NOT NULL DEFAULT 'info',
    title         TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '{}',
    fingerprint   TEXT,
    dedupe_key    TEXT UNIQUE,
    occurrences   INTEGER NOT NULL DEFAULT 1,
    occurred_at   TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'received',
    chain_status  TEXT,                              -- pending | confirmed | reorged, for chain events
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error         TEXT,
    task_ids      TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS at_events_status ON at_events(processing_status, received_at);
CREATE INDEX IF NOT EXISTS at_events_ws ON at_events(workspace_id, received_at);
CREATE INDEX IF NOT EXISTS at_events_fp ON at_events(workspace_id, fingerprint);

CREATE TABLE IF NOT EXISTS at_watchers (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    name          TEXT NOT NULL,
    config        TEXT NOT NULL DEFAULT '{}',
    secret_hash   TEXT,                              -- sha256 of the ingest token / webhook secret; the secret itself is shown once
    webhook_secret TEXT,                             -- GitHub webhook secret (HMAC key); a webhook cannot be checked with only a hash
    status        TEXT NOT NULL DEFAULT 'active',    -- active | paused | error
    state         TEXT NOT NULL DEFAULT '{}',        -- health: up/down, consecutive failures, last code; bsc: monitor id
    last_heartbeat_at TEXT,
    last_event_at TEXT,
    last_error    TEXT,
    next_check_at TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS at_activity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    type          TEXT NOT NULL,                     -- agent.created, watcher.started, bug.detected, agent.awakened, task.delegated, ...
    severity      TEXT NOT NULL DEFAULT 'info',
    workspace_id  TEXT,
    agent_id      TEXT,                              -- who did it (or whose state changed)
    target_agent_id TEXT,                            -- who it was handed to
    mission_id    TEXT,
    task_id       TEXT,
    run_id        TEXT,
    event_id      TEXT,
    actor         TEXT NOT NULL DEFAULT 'system',    -- agent id, user, system, worker id, watcher id
    title         TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',          -- why it happened
    result        TEXT NOT NULL DEFAULT '',          -- what came of it
    needs_human   INTEGER NOT NULL DEFAULT 0,
    data          TEXT
);
CREATE INDEX IF NOT EXISTS at_activity_agent ON at_activity(agent_id, id);
CREATE INDEX IF NOT EXISTS at_activity_ws ON at_activity(workspace_id, id);
CREATE INDEX IF NOT EXISTS at_activity_task ON at_activity(task_id, id);

CREATE TABLE IF NOT EXISTS at_notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'info',
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    agent_id     TEXT,
    workspace_id TEXT,
    task_id      TEXT,
    mission_id   TEXT,
    approval_id  INTEGER,
    dedupe_key   TEXT UNIQUE,
    read_at      TEXT
);

CREATE TABLE IF NOT EXISTS at_approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id      TEXT,
    agent_id     TEXT,
    kind         TEXT NOT NULL,                      -- patch | assign
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '{}',
    artifact_id  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | rejected
    decided_by   TEXT,
    decided_at   TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS at_approvals_status ON at_approvals(status, id);

CREATE TABLE IF NOT EXISTS at_artifacts (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    run_id       TEXT,
    workspace_id TEXT NOT NULL,
    agent_id     TEXT,
    kind         TEXT NOT NULL,                      -- patch
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',           -- description
    content      TEXT NOT NULL DEFAULT '',           -- unified diff
    meta         TEXT NOT NULL DEFAULT '{}',         -- repo, files, tests, pr_url, branch
    status       TEXT NOT NULL DEFAULT 'pending_review',  -- pending_review | applied | rejected | pr_open | merged | failed
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS at_workers (
    id           TEXT PRIMARY KEY,
    host         TEXT NOT NULL DEFAULT '',
    pid          INTEGER,
    version      TEXT,
    concurrency  INTEGER NOT NULL DEFAULT 1,
    running      TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'running',    -- running | stopped
    started_at   TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stats        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS at_token_flows (
    watcher_id   TEXT NOT NULL,
    from_addr    TEXT NOT NULL,
    to_addr      TEXT NOT NULL,
    transfers    INTEGER NOT NULL DEFAULT 0,
    total_raw    TEXT NOT NULL DEFAULT '0',
    first_block  INTEGER,
    last_block   INTEGER,
    last_tx      TEXT,
    PRIMARY KEY (watcher_id, from_addr, to_addr)
);
CREATE TABLE IF NOT EXISTS at_token_holders (
    watcher_id   TEXT NOT NULL,
    address      TEXT NOT NULL,
    balance_raw  TEXT,                               -- balanceOf at last_block, read from the chain
    first_block  INTEGER,
    last_block   INTEGER,
    PRIMARY KEY (watcher_id, address)
);
"""

# New columns on the workforce tables, added on start if missing.
MIGRATIONS = (
    ("wf_agents", "responsibility", "TEXT NOT NULL DEFAULT ''"),
    ("wf_agents", "work_mode", "TEXT NOT NULL DEFAULT 'event'"),
    ("wf_agents", "schedule_cron", "TEXT"),
    ("wf_agents", "workspace_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("wf_agents", "approval_rules", "TEXT NOT NULL DEFAULT '{}'"),
    ("wf_agents", "notify_rules", "TEXT NOT NULL DEFAULT '{}'"),
    ("wf_agents", "template", "TEXT NOT NULL DEFAULT ''"),
    ("wf_agents", "autonomous", "INTEGER NOT NULL DEFAULT 0"),
    ("wf_agents", "activated_at", "TEXT"),
    ("wf_agents", "wake_on", "TEXT"),                    # event types that wake the agent; NULL = the default problem events
    ("wf_workspaces", "resources", "TEXT NOT NULL DEFAULT '{}'"),
)

JSON_COLS = {
    "at_missions": ("agent_ids", "triggers", "permissions", "result"),
    "at_tasks": ("evidence", "required", "result", "chain", "waiting_on", "checkpoint"),
    "at_events": ("payload", "task_ids"),
    "at_watchers": ("config", "state"),
    "at_activity": ("data",),
    "at_approvals": ("detail",),
    "at_artifacts": ("meta",),
    "at_workers": ("running", "stats"),
}
AGENT_JSON = ("workspace_ids", "approval_rules", "notify_rules", "wake_on")


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _load(v: Any, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def iso(dt: datetime) -> str:
    return _iso(dt)


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def parse(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class AutonomyDB:
    def __init__(self, store: Store, wdb: WorkforceDB | None = None) -> None:
        self.store = store
        self.wdb = wdb or WorkforceDB(store)
        self._db = store._db
        self._lock = store._lock
        with self._lock:
            self._db.execute("PRAGMA busy_timeout = 15000")  # the API process and the worker share the file
            self._db.executescript(SCHEMA)
            for table, col, ddl in MIGRATIONS:
                cols = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in cols:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    def atomic(self):
        return self.wdb.atomic()

    # ------------------------------------------------------------------ generic row helpers

    def _row(self, table: str, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS.get(table, ()):
            if k in d:
                d[k] = _load(d[k], [] if k in ("agent_ids", "triggers", "permissions", "required", "chain", "waiting_on", "task_ids", "running") else
                             ({} if k not in ("result", "checkpoint", "data") else None))
        return d

    def _insert(self, table: str, row: dict) -> None:
        row = {k: (_j(v) if k in JSON_COLS.get(table, ()) and v is not None else v) for k, v in row.items()}
        self._db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    def _update(self, table: str, key: str, value: Any, f: dict, where: str = "", args: tuple = ()) -> int:
        if not f:
            return 0
        f = {k: (_j(v) if k in JSON_COLS.get(table, ()) and v is not None else v) for k, v in f.items()}
        sql = f"UPDATE {table} SET {','.join(f'{k} = ?' for k in f)} WHERE {key} = ?" + (f" AND {where}" if where else "")
        return self._db.execute(sql, (*f.values(), value, *args)).rowcount

    def _get(self, table: str, key: str, value: Any) -> dict | None:
        return self._row(table, self._db.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone())

    def _list(self, table: str, where: list[str], args: list, order: str, limit: int, offset: int = 0) -> list[dict]:
        w = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.execute(f"SELECT * FROM {table}{w} ORDER BY {order} LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return [self._row(table, r) for r in rows]

    def _count(self, table: str, where: list[str], args: list) -> int:
        w = (" WHERE " + " AND ".join(where)) if where else ""
        return int(self._db.execute(f"SELECT COUNT(*) AS n FROM {table}{w}", tuple(args)).fetchone()["n"])

    # ------------------------------------------------------------------ agents (wf_agents + autonomy columns)

    def agent(self, aid: str) -> dict | None:
        a = self.wdb.agent(aid)
        return self._agent(a) if a else None

    def agents(self, include_archived: bool = False) -> list[dict]:
        return [self._agent(a) for a in self.wdb.agents(include_archived=include_archived)]

    @staticmethod
    def _agent(a: dict) -> dict:
        for k in AGENT_JSON:
            a[k] = _load(a.get(k), [] if k == "workspace_ids" else {}) if isinstance(a.get(k), (str, type(None))) else a[k]
        a["autonomous"] = bool(a.get("autonomous"))
        return a

    def update_agent(self, aid: str, **f: Any) -> dict | None:
        for k in AGENT_JSON:
            if k in f:
                f[k] = _j(f[k])
        self.wdb.update_agent(aid, **f)
        return self.agent(aid)

    # ------------------------------------------------------------------ workspaces

    def workspace(self, wid: str) -> dict | None:
        w = self.wdb.workspace(wid)
        if w:
            r = self._db.execute("SELECT resources FROM wf_workspaces WHERE id = ?", (wid,)).fetchone()
            w["resources"] = _load(r["resources"] if r else None, {})
        return w

    def workspaces(self) -> list[dict]:
        out = []
        for w in self.wdb.workspaces():
            r = self._db.execute("SELECT resources FROM wf_workspaces WHERE id = ?", (w["id"],)).fetchone()
            w["resources"] = _load(r["resources"] if r else None, {})
            out.append(w)
        return out

    def set_resources(self, wid: str, resources: dict) -> None:
        self._db.execute("UPDATE wf_workspaces SET resources = ? WHERE id = ?", (_j(resources), wid))

    # ------------------------------------------------------------------ missions

    def add_mission(self, **f: Any) -> dict:
        mid = f.pop("id", None) or new_id("msn")
        now = _now()
        self._insert("at_missions", {"id": mid, "created_at": now, "updated_at": now, **f})
        return self.mission(mid)

    def mission(self, mid: str) -> dict | None:
        return self._get("at_missions", "id", mid)

    def update_mission(self, mid: str, **f: Any) -> dict | None:
        f["updated_at"] = _now()
        self._update("at_missions", "id", mid, f)
        return self.mission(mid)

    def missions(self, *, workspace_id: str | None = None, status: list[str] | None = None, agent_id: str | None = None,
                 responsibility_of: str | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        if workspace_id:
            where.append("workspace_id = ?")
            args.append(workspace_id)
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            args += status
        if agent_id:
            where.append("(lead_agent_id = ? OR agent_ids LIKE ?)")
            args += [agent_id, f'%"{agent_id}"%']
        if responsibility_of:
            where.append("responsibility_of = ?")
            args.append(responsibility_of)
        return self._list("at_missions", where, args, "created_at DESC", limit)

    def add_mission_cost(self, mid: str, cost: str) -> None:
        from decimal import Decimal

        with self._lock:
            r = self._db.execute("SELECT cost_usd FROM at_missions WHERE id = ?", (mid,)).fetchone()
            if r:
                self._db.execute("UPDATE at_missions SET cost_usd = ? WHERE id = ?", (str(Decimal(r["cost_usd"]) + Decimal(cost)), mid))

    # ------------------------------------------------------------------ tasks

    def add_task(self, **f: Any) -> dict:
        tid = f.pop("id", None) or new_id("job")
        now = _now()
        row = {"id": tid, "created_at": now, "updated_at": now, **f}
        row["root_id"] = row.get("root_id") or tid
        self._insert("at_tasks", row)
        return self.task(tid)

    def task(self, tid: str) -> dict | None:
        return self._get("at_tasks", "id", tid)

    def update_task(self, tid: str, **f: Any) -> None:
        f["updated_at"] = _now()
        self._update("at_tasks", "id", tid, f)

    def transition(self, tid: str, allowed_from: Iterable[str], to: str, **f: Any) -> bool:
        """Compare-and-set on status: two workers can never both claim, finish or fail the same task."""
        allowed = tuple(allowed_from)
        f["status"], f["updated_at"] = to, _now()
        if to == C.T_RUNNING:
            f.setdefault("started_at", f["updated_at"])
        if to in C.TASK_FINAL:
            f.setdefault("finished_at", f["updated_at"])
            f.setdefault("lease_owner", None)
            f.setdefault("lease_until", None)
        return self._update("at_tasks", "id", tid, f, f"status IN ({','.join('?' * len(allowed))})", allowed) == 1

    def tasks(self, *, workspace_id: str | None = None, mission_id: str | None = None, agent_id: str | None = None, status: list[str] | None = None,
              kind: str | None = None, root_id: str | None = None, parent_id: str | None = None, q: str | None = None, from_agent_id: str | None = None,
              since: datetime | None = None, limit: int = 100, offset: int = 0, order: str = "created_at DESC") -> list[dict]:
        where, args = self._task_where(workspace_id, mission_id, agent_id, status, kind, root_id, parent_id, q, from_agent_id, since)
        return self._list("at_tasks", where, args, order, limit, offset)

    def count_tasks(self, **kw: Any) -> int:
        where, args = self._task_where(kw.get("workspace_id"), kw.get("mission_id"), kw.get("agent_id"), kw.get("status"), kw.get("kind"),
                                       kw.get("root_id"), kw.get("parent_id"), kw.get("q"), kw.get("from_agent_id"), kw.get("since"))
        return self._count("at_tasks", where, args)

    @staticmethod
    def _task_where(workspace_id, mission_id, agent_id, status, kind, root_id, parent_id, q, from_agent_id, since) -> tuple[list[str], list]:
        where, args = [], []
        for col, v in (("workspace_id", workspace_id), ("mission_id", mission_id), ("to_agent_id", agent_id), ("kind", kind),
                       ("root_id", root_id), ("parent_id", parent_id), ("from_agent_id", from_agent_id)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            args += list(status)
        if q:
            where.append("(id LIKE ? OR objective LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if since:
            where.append("created_at >= ?")
            args.append(_iso(since))
        return where, args

    def open_task_by_dedupe(self, key: str) -> dict | None:
        r = self._db.execute(f"SELECT * FROM at_tasks WHERE dedupe_key = ? AND status IN ({','.join('?' * len(C.TASK_OPEN))}) ORDER BY created_at LIMIT 1",
                             (key, *C.TASK_OPEN)).fetchone()
        return self._row("at_tasks", r)

    def next_queued(self, limit: int = 50) -> list[dict]:
        return self._list("at_tasks", ["status = ?", "(not_before IS NULL OR not_before <= ?)"], [C.T_QUEUED, _now()], "priority, created_at", limit)

    def claim(self, tid: str, worker: str, lease_s: int) -> bool:
        until = _iso(now_dt() + timedelta(seconds=lease_s))
        n = self._db.execute("UPDATE at_tasks SET status = ?, lease_owner = ?, lease_until = ?, attempts = attempts + 1,"
                             " started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ? AND status = ?",
                             (C.T_RUNNING, worker, until, _now(), _now(), tid, C.T_QUEUED)).rowcount
        return n == 1

    def renew_leases(self, worker: str, task_ids: list[str], lease_s: int) -> int:
        if not task_ids:
            return 0
        until = _iso(now_dt() + timedelta(seconds=lease_s))
        return self._db.execute(f"UPDATE at_tasks SET lease_until = ? WHERE lease_owner = ? AND status = ? AND id IN ({','.join('?' * len(task_ids))})",
                                (until, worker, C.T_RUNNING, *task_ids)).rowcount

    def expired_leases(self) -> list[dict]:
        return self._list("at_tasks", ["status = ?", "lease_until < ?"], [C.T_RUNNING, _now()], "lease_until", 200)

    def running_count(self, agent_id: str) -> int:
        return self._count("at_tasks", ["to_agent_id = ?", "status = ?"], [agent_id, C.T_RUNNING])

    def overdue_tasks(self) -> list[dict]:
        st = (C.T_QUEUED, C.T_RUNNING, C.T_WAITING, C.T_UNASSIGNED)
        return self._list("at_tasks", [f"status IN ({','.join('?' * len(st))})", "deadline_at IS NOT NULL", "deadline_at < ?"],
                          [*st, _now()], "deadline_at", 200)

    def bump_task_cost(self, tid: str, cost: str, tokens: int) -> None:
        from decimal import Decimal

        with self._lock:
            r = self._db.execute("SELECT cost_usd FROM at_tasks WHERE id = ?", (tid,)).fetchone()
            if r:
                self._db.execute("UPDATE at_tasks SET cost_usd = ?, tokens = tokens + ?, updated_at = ? WHERE id = ?",
                                 (str(Decimal(r["cost_usd"]) + Decimal(cost)), tokens, _now(), tid))

    def fix_count(self, fingerprint: str, since: datetime) -> int:
        return self._count("at_tasks", ["kind = 'fix'", "fingerprint = ?", "created_at >= ?"], [fingerprint, _iso(since)])

    def recent_delegations(self, agent_id: str, since: datetime) -> int:
        return self._count("at_tasks", ["from_agent_id = ?", "created_at >= ?"], [agent_id, _iso(since)])

    def task_status_counts(self, **kw: Any) -> dict[str, int]:
        where, args = self._task_where(kw.get("workspace_id"), kw.get("mission_id"), kw.get("agent_id"), None, kw.get("kind"),
                                       kw.get("root_id"), None, kw.get("q"), None, kw.get("since"))
        w = (" WHERE " + " AND ".join(where)) if where else ""
        return {r["status"]: r["n"] for r in self._db.execute(f"SELECT status, COUNT(*) AS n FROM at_tasks{w} GROUP BY status", tuple(args)).fetchall()}

    # ------------------------------------------------------------------ runs and steps

    def add_run(self, **f: Any) -> dict:
        rid = f.pop("id", None) or new_id("run")
        self._insert("at_runs", {"id": rid, "started_at": _now(), "status": C.R_RUNNING, **f})
        return self.run(rid)

    def run(self, rid: str) -> dict | None:
        return self._get("at_runs", "id", rid)

    def update_run(self, rid: str, **f: Any) -> None:
        self._update("at_runs", "id", rid, f)

    def runs(self, *, task_id: str | None = None, agent_id: str | None = None, status: list[str] | None = None, limit: int = 50) -> list[dict]:
        where, args = [], []
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        if agent_id:
            where.append("agent_id = ?")
            args.append(agent_id)
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            args += status
        return self._list("at_runs", where, args, "started_at DESC", limit)

    def last_run(self, agent_id: str) -> dict | None:
        return self._row("at_runs", self._db.execute("SELECT * FROM at_runs WHERE agent_id = ? AND status != ? ORDER BY started_at DESC LIMIT 1",
                                                     (agent_id, C.R_RUNNING)).fetchone())

    def add_step(self, run_id: str, kind: str, name: str = "", *, ok: bool = True, secs: float | None = None, input: Any = None,
                 output: Any = None, tokens_in: int = 0, tokens_out: int = 0, cost_usd: str = "0") -> int:
        cur = self._db.execute("INSERT INTO at_run_steps (run_id, at, kind, name, ok, secs, input, output, tokens_in, tokens_out, cost_usd)"
                               " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (run_id, _now(), kind, name, int(ok), secs, _clip_json(input), _clip_json(output), tokens_in, tokens_out, cost_usd))
        return int(cur.lastrowid)

    def steps(self, run_id: str, limit: int = 500) -> list[dict]:
        rows = self._db.execute("SELECT * FROM at_run_steps WHERE run_id = ? ORDER BY id LIMIT ?", (run_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["ok"] = bool(d["ok"])
            d["input"], d["output"] = _load(d["input"], d["input"]), _load(d["output"], d["output"])
            out.append(d)
        return out

    def model_calls(self, since: datetime | None = None, limit: int = 200) -> list[dict]:
        where = "WHERE s.kind = 'model'" + (" AND s.at >= ?" if since else "")
        rows = self._db.execute(f"SELECT s.*, r.agent_id, r.task_id, r.provider, r.model FROM at_run_steps s JOIN at_runs r ON r.id = s.run_id {where}"
                                " ORDER BY s.id DESC LIMIT ?", ((_iso(since),) if since else ()) + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def usage_totals(self, since: datetime) -> dict:
        r = self._db.execute("SELECT COUNT(*) AS runs, SUM(model_calls) AS model_calls, SUM(tool_calls) AS tool_calls, SUM(tokens_in) AS tin,"
                             " SUM(tokens_out) AS tout, SUM(CAST(cost_usd AS REAL)) AS cost, SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed"
                             " FROM at_runs WHERE started_at >= ?", (_iso(since),)).fetchone()
        lat = self._db.execute("SELECT AVG(secs) AS avg, MAX(secs) AS max, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures, COUNT(*) AS n"
                               " FROM at_run_steps WHERE kind = 'model' AND at >= ?", (_iso(since),)).fetchone()
        return {"runs": r["runs"] or 0, "model_calls": r["model_calls"] or 0, "tool_calls": r["tool_calls"] or 0, "tokens_in": r["tin"] or 0,
                "tokens_out": r["tout"] or 0, "cost_usd": round(r["cost"] or 0.0, 6), "failed_runs": r["failed"] or 0,
                "model_latency_avg_s": round(lat["avg"], 3) if lat["avg"] is not None else None,
                "model_latency_max_s": round(lat["max"], 3) if lat["max"] is not None else None, "model_failures": lat["failures"] or 0}

    def cost_by(self, col: str, since: datetime) -> list[dict]:
        assert col in ("agent_id", "mission_id", "workspace_id")
        rows = self._db.execute(f"SELECT {col} AS k, COUNT(*) AS runs, SUM(CAST(cost_usd AS REAL)) AS cost, SUM(tokens_in + tokens_out) AS tokens"
                                f" FROM at_runs WHERE started_at >= ? GROUP BY {col} ORDER BY cost DESC", (_iso(since),)).fetchall()
        return [{"id": r["k"], "runs": r["runs"], "cost_usd": round(r["cost"] or 0.0, 6), "tokens": r["tokens"] or 0} for r in rows]

    # ------------------------------------------------------------------ events

    def add_event(self, **f: Any) -> tuple[dict, bool]:
        """Insert an event. A second event with the same dedupe_key is not inserted: the first one is returned and its
        occurrence count goes up. Returns (event, created)."""
        eid = f.pop("id", None) or new_id("evt")
        now = _now()
        row = {"id": eid, "received_at": now, "updated_at": now, "occurred_at": f.pop("occurred_at", None) or now, **f}
        try:
            self._insert("at_events", row)
        except sqlite3.IntegrityError:
            key = f.get("dedupe_key")
            self._db.execute("UPDATE at_events SET occurrences = occurrences + 1, updated_at = ? WHERE dedupe_key = ?", (now, key))
            return self._get("at_events", "dedupe_key", key), False
        return self.event(eid), True

    def event(self, eid: str) -> dict | None:
        return self._get("at_events", "id", eid)

    def event_by_key(self, key: str) -> dict | None:
        return self._get("at_events", "dedupe_key", key)

    def update_event(self, eid: str, **f: Any) -> None:
        f["updated_at"] = _now()
        self._update("at_events", "id", eid, f)

    def set_event_status(self, eid: str, allowed: tuple[str, ...], to: str, **f: Any) -> bool:
        f["processing_status"], f["updated_at"] = to, _now()
        return self._update("at_events", "id", eid, f, f"processing_status IN ({','.join('?' * len(allowed))})", allowed) == 1

    def due_events(self, limit: int = 50) -> list[dict]:
        return self._list("at_events", ["processing_status IN (?, ?)", "(next_attempt_at IS NULL OR next_attempt_at <= ?)"],
                          [C.E_RECEIVED, C.E_FAILED, _now()], "received_at", limit)

    def events(self, *, workspace_id: str | None = None, source: str | None = None, type_: str | None = None, severity: list[str] | None = None,
               watcher_id: str | None = None, status: str | None = None, fingerprint: str | None = None, since: datetime | None = None,
               limit: int = 100) -> list[dict]:
        where, args = [], []
        for col, v in (("workspace_id", workspace_id), ("source", source), ("type", type_), ("watcher_id", watcher_id),
                       ("processing_status", status), ("fingerprint", fingerprint)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        if severity:
            where.append(f"severity IN ({','.join('?' * len(severity))})")
            args += severity
        if since:
            where.append("received_at >= ?")
            args.append(_iso(since))
        return self._list("at_events", where, args, "received_at DESC", limit)

    # ------------------------------------------------------------------ watchers

    def add_watcher(self, **f: Any) -> dict:
        wid = f.pop("id", None) or new_id("wch")
        now = _now()
        self._insert("at_watchers", {"id": wid, "created_at": now, "updated_at": now, **f})
        return self.watcher(wid)

    def watcher(self, wid: str) -> dict | None:
        return self._get("at_watchers", "id", wid)

    def update_watcher(self, wid: str, **f: Any) -> dict | None:
        f["updated_at"] = _now()
        self._update("at_watchers", "id", wid, f)
        return self.watcher(wid)

    def watchers(self, *, workspace_id: str | None = None, kind: str | None = None, status: str | None = None) -> list[dict]:
        where, args = [], []
        for col, v in (("workspace_id", workspace_id), ("kind", kind), ("status", status)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        return self._list("at_watchers", where, args, "created_at", 500)

    def delete_watcher(self, wid: str) -> bool:
        return self._db.execute("DELETE FROM at_watchers WHERE id = ?", (wid,)).rowcount == 1

    # ------------------------------------------------------------------ activity

    def add_activity(self, **f: Any) -> int:
        row = {"at": _now(), **f}
        if "needs_human" in row:
            row["needs_human"] = int(bool(row["needs_human"]))
        for k in ("title", "reason", "result"):
            if k in row and row[k] is not None:
                row[k] = str(row[k])[:2000]
        self._insert("at_activity", row)
        return int(self._db.execute("SELECT last_insert_rowid() AS i").fetchone()["i"])

    def activity(self, *, after: int = 0, before: int | None = None, agent_id: str | None = None, workspace_id: str | None = None,
                 mission_id: str | None = None, task_id: str | None = None, root_id: str | None = None, severity: list[str] | None = None,
                 types: list[str] | None = None, needs_human: bool | None = None, since: datetime | None = None, until: datetime | None = None,
                 limit: int = 200, newest_first: bool = True) -> list[dict]:
        where, args = ["id > ?"], [after]
        if before:
            where.append("id < ?")
            args.append(before)
        if agent_id:
            where.append("(agent_id = ? OR target_agent_id = ?)")
            args += [agent_id, agent_id]
        for col, v in (("workspace_id", workspace_id), ("mission_id", mission_id), ("task_id", task_id)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        if root_id:
            where.append("task_id IN (SELECT id FROM at_tasks WHERE root_id = ?)")
            args.append(root_id)
        if severity:
            where.append(f"severity IN ({','.join('?' * len(severity))})")
            args += severity
        if types:
            clauses = []
            for t in types:
                if t.endswith(".*"):
                    clauses.append("type LIKE ?")
                    args.append(t[:-1] + "%")
                else:
                    clauses.append("type = ?")
                    args.append(t)
            where.append("(" + " OR ".join(clauses) + ")")
        if needs_human is not None:
            where.append("needs_human = ?")
            args.append(int(needs_human))
        if since:
            where.append("at >= ?")
            args.append(_iso(since))
        if until:
            where.append("at < ?")
            args.append(_iso(until))
        rows = self._list("at_activity", where, args, "id DESC" if newest_first else "id", limit)
        for r in rows:
            r["needs_human"] = bool(r["needs_human"])
        return rows

    def last_activity_id(self) -> int:
        r = self._db.execute("SELECT MAX(id) AS m FROM at_activity").fetchone()
        return int(r["m"] or 0)

    def last_wake(self, agent_id: str) -> str | None:
        r = self._db.execute("SELECT at FROM at_activity WHERE agent_id = ? AND type = 'agent.awakened' ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
        return r["at"] if r else None

    def last_activity_at(self, agent_id: str) -> str | None:
        r = self._db.execute("SELECT at FROM at_activity WHERE agent_id = ? ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
        return r["at"] if r else None

    # ------------------------------------------------------------------ notifications

    def add_notification(self, **f: Any) -> int | None:
        try:
            self._insert("at_notifications", {"at": _now(), **f})
        except sqlite3.IntegrityError:
            return None
        return int(self._db.execute("SELECT last_insert_rowid() AS i").fetchone()["i"])

    def notifications(self, unread_only: bool = False, limit: int = 100) -> list[dict]:
        return self._list("at_notifications", ["read_at IS NULL"] if unread_only else [], [], "id DESC", limit)

    def unread_count(self) -> int:
        return self._count("at_notifications", ["read_at IS NULL"], [])

    def mark_read(self, ids: list[int] | None = None) -> int:
        if ids is None:
            return self._db.execute("UPDATE at_notifications SET read_at = ? WHERE read_at IS NULL", (_now(),)).rowcount
        if not ids:
            return 0
        return self._db.execute(f"UPDATE at_notifications SET read_at = ? WHERE read_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                                (_now(), *ids)).rowcount

    # ------------------------------------------------------------------ approvals

    def add_approval(self, **f: Any) -> dict:
        self._insert("at_approvals", {"at": _now(), **f})
        aid = int(self._db.execute("SELECT last_insert_rowid() AS i").fetchone()["i"])
        return self.approval(aid)

    def approval(self, aid: int) -> dict | None:
        return self._get("at_approvals", "id", aid)

    def approvals(self, status: str | None = None, task_id: str | None = None, agent_id: str | None = None, limit: int = 100) -> list[dict]:
        where, args = [], []
        for col, v in (("status", status), ("task_id", task_id), ("agent_id", agent_id)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        return self._list("at_approvals", where, args, "id DESC", limit)

    def decide_approval(self, aid: int, status: str, by: str, note: str = "") -> bool:
        return self._update("at_approvals", "id", aid, {"status": status, "decided_by": by, "decided_at": _now(), "note": note[:500] or None},
                            "status = 'pending'") == 1

    # ------------------------------------------------------------------ artifacts

    def add_artifact(self, **f: Any) -> dict:
        aid = f.pop("id", None) or new_id("art")
        now = _now()
        self._insert("at_artifacts", {"id": aid, "created_at": now, "updated_at": now, **f})
        return self.artifact(aid)

    def artifact(self, aid: str) -> dict | None:
        return self._get("at_artifacts", "id", aid)

    def update_artifact(self, aid: str, **f: Any) -> None:
        f["updated_at"] = _now()
        self._update("at_artifacts", "id", aid, f)

    def artifacts(self, *, task_id: str | None = None, root_id: str | None = None, workspace_id: str | None = None,
                  fingerprint: str | None = None, since: datetime | None = None, limit: int = 50) -> list[dict]:
        where, args = [], []
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        if root_id:
            where.append("task_id IN (SELECT id FROM at_tasks WHERE root_id = ?)")
            args.append(root_id)
        if workspace_id:
            where.append("workspace_id = ?")
            args.append(workspace_id)
        if fingerprint:
            where.append("task_id IN (SELECT id FROM at_tasks WHERE fingerprint = ?)")
            args.append(fingerprint)
        if since:
            where.append("created_at >= ?")
            args.append(_iso(since))
        return self._list("at_artifacts", where, args, "created_at DESC", limit)

    # ------------------------------------------------------------------ workers

    def heartbeat(self, wid: str, **f: Any) -> None:
        now = _now()
        if self._update("at_workers", "id", wid, {"heartbeat_at": now, "status": "running", **f}) == 0:
            self._insert("at_workers", {"id": wid, "started_at": now, "heartbeat_at": now, "status": "running", **f})

    def stop_worker(self, wid: str) -> None:
        self._update("at_workers", "id", wid, {"status": "stopped", "running": [], "heartbeat_at": _now()})

    def workers(self, limit: int = 20) -> list[dict]:
        return self._list("at_workers", [], [], "heartbeat_at DESC", limit)

    # ------------------------------------------------------------------ token flows (observed on chain since the watcher started)

    def add_flow(self, watcher_id: str, frm: str, to: str, value_raw: str, block: int, tx: str) -> None:
        with self._lock:
            r = self._db.execute("SELECT total_raw FROM at_token_flows WHERE watcher_id = ? AND from_addr = ? AND to_addr = ?", (watcher_id, frm, to)).fetchone()
            if r is None:
                self._db.execute("INSERT INTO at_token_flows (watcher_id, from_addr, to_addr, transfers, total_raw, first_block, last_block, last_tx)"
                                 " VALUES (?,?,?,?,?,?,?,?)", (watcher_id, frm, to, 1, str(int(value_raw)), block, block, tx))
            else:
                self._db.execute("UPDATE at_token_flows SET transfers = transfers + 1, total_raw = ?, last_block = ?, last_tx = ?"
                                 " WHERE watcher_id = ? AND from_addr = ? AND to_addr = ?",
                                 (str(int(r["total_raw"]) + int(value_raw)), block, tx, watcher_id, frm, to))

    def flows(self, watcher_ids: list[str], address: str | None = None, limit: int = 100) -> list[dict]:
        if not watcher_ids:
            return []
        where = [f"watcher_id IN ({','.join('?' * len(watcher_ids))})"]
        args: list[Any] = list(watcher_ids)
        if address:
            where.append("(from_addr = ? OR to_addr = ?)")
            args += [address.lower(), address.lower()]
        rows = self._db.execute(f"SELECT * FROM at_token_flows WHERE {' AND '.join(where)} ORDER BY transfers DESC, last_block DESC LIMIT ?",
                                (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_holder(self, watcher_id: str, address: str, balance_raw: str | None, block: int) -> dict | None:
        """Record a holder's balance read at `block`; returns the previous row (None for an address not seen before)."""
        with self._lock:
            prev = self._db.execute("SELECT * FROM at_token_holders WHERE watcher_id = ? AND address = ?", (watcher_id, address)).fetchone()
            if prev is None:
                self._db.execute("INSERT INTO at_token_holders (watcher_id, address, balance_raw, first_block, last_block) VALUES (?,?,?,?,?)",
                                 (watcher_id, address, balance_raw, block, block))
            else:
                self._db.execute("UPDATE at_token_holders SET balance_raw = ?, last_block = ? WHERE watcher_id = ? AND address = ?",
                                 (balance_raw, block, watcher_id, address))
        return dict(prev) if prev else None

    def holders(self, watcher_id: str, limit: int = 100) -> list[dict]:
        rows = self._db.execute("SELECT * FROM at_token_holders WHERE watcher_id = ? AND balance_raw IS NOT NULL AND balance_raw != '0'"
                                " ORDER BY LENGTH(balance_raw) DESC, balance_raw DESC LIMIT ?", (watcher_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def holder_count(self, watcher_id: str) -> int:
        return self._count("at_token_holders", ["watcher_id = ?", "balance_raw IS NOT NULL", "balance_raw != '0'"], [watcher_id])


def _clip_json(v: Any, n: int = 20000) -> str | None:
    if v is None:
        return None
    s = v if isinstance(v, str) else _j(v)
    return s if len(s) <= n else s[:n] + f"…(truncated, {len(s)} chars)"
