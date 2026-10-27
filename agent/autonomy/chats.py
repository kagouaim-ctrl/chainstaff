"""Chats: the conversation a person has with KAGOU or with one agent, on top of the task queue.

A chat stores only what the person typed and which task each message became. Everything the console shows as the
answer (who took it, what is running, the result, the approval it waits on) is read from the task and its
hand-off tree, so a chat can never say more than the task queue knows.

    person types ──▶ chat turn ──▶ task (to the chosen agent, or matched by responsibility and permissions)
                                    └▶ worker runs it ─▶ result / sub-tasks / approval ─▶ shown in the chat

A follow-up in the same chat carries the earlier turns (what was asked, who answered, the result summary) as the
task's evidence, so the agent sees the conversation without a separate memory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent.store import _now
from agent.workforce.db import new_id

from . import constants as C
from .wakeup import TaskError

if TYPE_CHECKING:  # pragma: no cover
    from .service import Autonomy

SCHEMA = """
CREATE TABLE IF NOT EXISTS at_chats (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    agent_id     TEXT,                               -- set when the chat is with one agent; NULL = KAGOU picks per message
    workspace_id TEXT,
    created_by   TEXT NOT NULL,
    archived     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS at_chats_updated ON at_chats (archived, updated_at);
CREATE TABLE IF NOT EXISTS at_chat_turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL,
    at           TEXT NOT NULL,
    text         TEXT NOT NULL,
    agent_id     TEXT,                               -- the agent the person picked for this message, if any
    kind         TEXT NOT NULL DEFAULT 'general',
    task_id      TEXT,                               -- NULL when the task could not be created (error says why)
    routing      TEXT,                               -- JSON: how the agent was chosen
    error        TEXT
);
CREATE INDEX IF NOT EXISTS at_chat_turns_chat ON at_chat_turns (chat_id, id);
"""

MAX_TEXT = 4000
HISTORY = 6  # earlier turns passed to the agent as evidence


class ChatError(ValueError):
    pass


class Chats:
    def __init__(self, at: "Autonomy") -> None:
        self.at = at
        self.db = at.db
        self._db = at.db._db
        with at.db._lock:
            self._db.executescript(SCHEMA)

    # ------------------------------------------------------------------ reads

    def list(self, *, agent_id: str | None = None, limit: int = 100) -> list[dict]:
        where, args = ["archived = 0"], []
        if agent_id:
            where.append("agent_id = ?")
            args.append(agent_id)
        rows = self._db.execute(f"SELECT * FROM at_chats WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            c = dict(r)
            last = self._db.execute("SELECT task_id, error FROM at_chat_turns WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (c["id"],)).fetchone()
            n = self._db.execute("SELECT COUNT(*) AS n FROM at_chat_turns WHERE chat_id = ?", (c["id"],)).fetchone()["n"]
            t = self.db.task(last["task_id"]) if last and last["task_id"] else None
            out.append({**self._public(c), "turns": n, "last_status": t["status"] if t else ("error" if last and last["error"] else None)})
        return out

    def get(self, cid: str) -> dict:
        c = self._chat(cid)
        turns = [dict(r) for r in self._db.execute("SELECT * FROM at_chat_turns WHERE chat_id = ? ORDER BY id", (cid,)).fetchall()]
        names: dict[str, str] = {}
        out = []
        for t in turns:
            out.append(self._turn(t, names))
        if c.get("agent_id"):
            names.setdefault(c["agent_id"], self.at.name(c["agent_id"]))
        return {**self._public(c), "messages": out, "names": names}

    def _turn(self, t: dict, names: dict[str, str]) -> dict:
        task = self.db.task(t["task_id"]) if t["task_id"] else None
        tree, approvals, steps = [], [], []
        if task:
            tree = [x for x in self.db.tasks(root_id=task["root_id"], limit=60, order="created_at") if x["id"] != task["id"]]
            for x in [task, *tree]:
                approvals += self.db.approvals(task_id=x["id"])
                for a in (x.get("to_agent_id"), x.get("from_agent_id")):
                    if a and a not in names:
                        names[a] = self.at.name(a)
            if task["status"] in C.TASK_OPEN:
                # The latest few steps of the newest run, so "working" says on what.
                runs = self.db.runs(task_id=task["id"], limit=1)
                if runs:
                    steps = [{"kind": s["kind"], "name": s["name"], "ok": s["ok"], "at": s["at"]} for s in self.db.steps(runs[0]["id"])[-4:]]
        for a in approvals:
            a["agent_name"] = names.get(a.get("agent_id") or "") or self.at.name(a.get("agent_id"))
        compact = lambda x: {k: x[k] for k in ("id", "kind", "objective", "status", "from_agent_id", "to_agent_id", "depth", "created_at", "finished_at", "error")} | {
            "summary": (x.get("result") or {}).get("summary")}  # noqa: E731
        return {"id": t["id"], "at": t["at"], "text": t["text"], "agent_id": t["agent_id"], "kind": t["kind"], "error": t["error"],
                "routing": json.loads(t["routing"]) if t["routing"] else None, "task": task, "tree": [compact(x) for x in tree],
                "approvals": approvals, "steps": steps}

    # ------------------------------------------------------------------ writes

    def create(self, body: dict, by: str) -> dict:
        agent_id = str(body.get("agent_id") or "") or None
        if agent_id:
            self.at.agent_or_raise(agent_id)
        text = _text(body.get("text"), required=False)
        title = str(body.get("title") or "").strip()[:120] or (text[:60] if text else "") or "New chat"
        cid = new_id("cht")
        now = _now()
        self._db.execute("INSERT INTO at_chats (id, title, agent_id, workspace_id, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (cid, title, agent_id, str(body.get("workspace_id") or "") or None, by, now, now))
        if text:
            self.send(cid, {**body, "text": text}, by)
        return self.get(cid)

    def send(self, cid: str, body: dict, by: str) -> dict:
        c = self._chat(cid)
        text = _text(body.get("text"))
        kind = str(body.get("kind") or "general")
        if kind not in C.TASK_KINDS:
            raise ChatError(f"任务类型只能是 {', '.join(C.TASK_KINDS)}")
        agent_id = str(body.get("agent_id") or "") or c.get("agent_id")
        evidence = {"source": "chat", "chat_id": cid, "conversation": self._history(cid)}
        task, routing, error = None, None, None
        try:
            if agent_id:
                a = self.at.agent_or_raise(agent_id)
                ws = str(body.get("workspace_id") or "") or (c["workspace_id"] if c["workspace_id"] in (a.get("workspace_ids") or []) else None)
                task = self.at.give_task(agent_id, {"objective": text, "kind": kind, "workspace_id": ws, "evidence": evidence}, by)
                routing = {"auto": False, "agent_id": agent_id}
            else:
                ws = str(body.get("workspace_id") or "") or c.get("workspace_id") or self._default_workspace()
                if not ws:
                    raise ChatError("还没有工作区：先在 Workspace 里建一个，KAGOU 才知道把任务交给哪些 Agent")
                task, info = self.at.wakeup.create_task(workspace_id=ws, objective=text, kind=kind, evidence=evidence, created_by=by,
                                                        reason="聊天里提出，KAGOU 按职责和权限选择 Agent")
                task = {**task, "deduplicated": info["deduplicated"]}
                ranked = sorted(info["considered"], key=lambda x: (not x["eligible"], -x["relevance"], x["load"]))
                routing = {"auto": True, "agent_id": task.get("to_agent_id"), "considered": ranked[:6]}
        except (TaskError, ValueError) as e:  # AutonomyError and ChatError are ValueErrors; a blocked hand-off is a TaskError
            error = str(e)
        now = _now()
        self._db.execute("INSERT INTO at_chat_turns (chat_id, at, text, agent_id, kind, task_id, routing, error) VALUES (?,?,?,?,?,?,?,?)",
                         (cid, now, text, str(body.get("agent_id") or "") or None, kind, task["id"] if task else None,
                          json.dumps(routing, ensure_ascii=False) if routing else None, error))
        ws_used = (task or {}).get("workspace_id") or c.get("workspace_id")
        self._db.execute("UPDATE at_chats SET updated_at = ?, workspace_id = ? WHERE id = ?", (now, ws_used, cid))
        if task:
            self.at.activity.audit(f"聊天 {cid} 发起任务 {task['id']}", by, {"chat": cid, "task": task["id"]})
        return self.get(cid)

    def update(self, cid: str, body: dict, by: str) -> dict:
        self._chat(cid)
        if "title" in body:
            title = str(body.get("title") or "").strip()[:120]
            if not title:
                raise ChatError("标题不能为空")
            self._db.execute("UPDATE at_chats SET title = ? WHERE id = ?", (title, cid))
        if "archived" in body:
            self._db.execute("UPDATE at_chats SET archived = ? WHERE id = ?", (1 if body.get("archived") else 0, cid))
        return self._public(self._chat(cid))

    # ------------------------------------------------------------------ helpers

    def _chat(self, cid: str) -> dict:
        r = self._db.execute("SELECT * FROM at_chats WHERE id = ?", (cid,)).fetchone()
        if r is None:
            raise ChatError(f"没有这个会话 {cid}")
        return dict(r)

    def _public(self, c: dict) -> dict:
        return {**c, "archived": bool(c["archived"]), "agent_name": self.at.name(c["agent_id"]) if c.get("agent_id") else None}

    def _history(self, cid: str) -> list[dict]:
        rows = self._db.execute("SELECT text, task_id, error FROM at_chat_turns WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (cid, HISTORY)).fetchall()
        out = []
        for r in reversed(rows):
            t = self.db.task(r["task_id"]) if r["task_id"] else None
            item: dict[str, Any] = {"user": r["text"][:1500]}
            if t:
                item.update({"agent": self.at.name(t.get("to_agent_id")) if t.get("to_agent_id") else None, "status": t["status"],
                             "result": ((t.get("result") or {}).get("summary") or "")[:1500] or None})
            elif r["error"]:
                item["error"] = r["error"][:300]
            out.append(item)
        return out

    def _default_workspace(self) -> str | None:
        """The workspace with the most active agents that can take tasks: where KAGOU can actually find someone."""
        ws = [w for w in self.db.workspaces() if not w.get("archived")]
        if not ws:
            return None
        ready = [a for a in self.db.agents() if a.get("autonomous") and a["status"] == "active"]
        return max(ws, key=lambda w: sum(1 for a in ready if w["id"] in (a.get("workspace_ids") or [])))["id"]


def _text(v: Any, required: bool = True) -> str:
    s = str(v or "").strip()
    if required and not s:
        raise ChatError("消息不能为空")
    if len(s) > MAX_TEXT:
        raise ChatError(f"消息太长（最多 {MAX_TEXT} 字）")
    return s
