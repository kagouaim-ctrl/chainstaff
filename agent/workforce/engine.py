"""The workforce engine: agents, the message bus, the AI manager and the task executor.

How a goal runs (every arrow is a persisted row, so a restart resumes instead of starting over):

    user goal ──▶ goal task (pending) ──bus──▶ manager inbox
    manager: plan ──▶ subtasks (pending, with dependencies) ──▶ goal waiting
    dispatch: each ready subtask ──bus "task.assign"──▶ worker inbox        (parallel, thread pool)
    worker: permission check ▶ run skill ▶ result
            ──bus "task.result"──▶ manager      ──bus "handoff"──▶ agents of dependent subtasks
    manager: collect ▶ maybe add steps ▶ dispatch the next ready ones ▶ when all settled: goal review + approval
    human: approve (optionally anchor the result hash on BSC testnet) / reject

Limits on every goal: max steps, a deadline and a cost budget. Going over the budget pauses the
goal behind an approval instead of continuing.

The node functions (_plan, _dispatch, _execute, _collect, _finalize) map one-to-one onto a
LangGraph StateGraph if the orchestration moves there later.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from agent.store import Store, _iso

from . import db as D
from .cron import Cron
from .db import WorkforceDB
from .gateway import Gateway
from .models import DEFAULT_MODEL
from . import chaintools  # noqa: F401 - registers the extra read-only chain tools
from .chain import Cancelled
from .planner import Planner, PlanCancelled, PlanError
from .runtime import AgentRuntime, AnthropicProvider, Limits
from .skills import ADDRESS_RE, AGENT_TASK, PERMISSIONS, REGISTRY, SKILL_BY_ID, SkillContext, SkillError, validate

EXTRA_PERMISSIONS = {"message:peers": "可以直接给同工作区的其他员工发消息"}
ALL_PERMISSIONS = {**PERMISSIONS, **EXTRA_PERMISSIONS}
PROVIDERS = ("none", "anthropic", "openai_compat")
AUDIT = "workforce"

# Squad policy. Each one changes what the engine does for the squad's missions.
SQUAD_PERMISSIONS = {
    "manager_executes": "主管有对应技能时也可以亲自执行任务",
    "peer_messages": "成员之间可以直接发消息，不经过主管",
    "anchor_results": "审批通过后可以把结果哈希写到 BSC 测试网",
}
DEFAULT_SQUAD_PERMISSIONS = ["manager_executes", "anchor_results"]
# Skills the rule planner can plan with (it only understands addresses in the goal).
RULE_SKILLS = ("wallet_analysis", "contract_analysis", "token_info", "contract_events", "tx_history")
MAX_ATTACHMENT_CHARS = 12000
# A goal whose planning was interrupted this many times (restarts mid-plan) fails instead of planning again.
MAX_PLAN_ATTEMPTS = 3


class WorkforceError(ValueError):
    """A request the workforce refuses; the message is shown to the user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(v: Any, what: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise WorkforceError(f"{what} 格式不对：{v}") from None
    if not d.is_finite() or d < 0:
        raise WorkforceError(f"{what} 不能是负数")
    return d


def result_hash(result: Any) -> str:
    canon = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "0x" + hashlib.sha256(canon.encode()).hexdigest()


class Workforce:
    def __init__(self, store: Store, *, reader=None, etherscan: Callable[[dict], Any] | None = None,
                 tokens: dict[str, tuple[str, int]] | None = None, llm: Callable[[], Any] | None = None,
                 anchor=None, max_workers: int = 4, msg_timeout_s: int = 300,
                 price_per_mtok: tuple[Decimal, Decimal] | None = None, plan_timeout_s: float = 60.0, plan_retries: int = 2,
                 plan_workers: int = 2, compat=None, ai_env: dict | None = None, default_model: str | None = None) -> None:
        self.store = store
        self.db = WorkforceDB(store)
        self.reader = reader
        self.etherscan = etherscan
        self.tokens = tokens or {}
        self.planner = Planner(llm, price_per_mtok, model=default_model or DEFAULT_MODEL, timeout_s=plan_timeout_s, retries=plan_retries, compat=compat)
        self.llm = llm
        self.compat = compat
        # Agents that reason with a model run here; one provider per configured model API.
        providers: dict = {"anthropic": AnthropicProvider(llm)} if llm else {}
        if compat:
            providers["openai_compat"] = compat
        self.runtime = AgentRuntime(self, providers, price_per_mtok)
        # Every model call (agent runs and planning) goes through the gateway: budgets, concurrency, circuit breaker.
        self.gateway = Gateway.from_env(self.db, ai_env or {})
        self.planner.gateway = self.gateway
        self.anchor = anchor
        self.events = None  # set by the BSC listener (agent.bsc) when it runs; chain tools query it
        self.msg_timeout = timedelta(seconds=msg_timeout_s)
        self.instance = D.new_id("eng")  # names this process in planning claims
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wf-agent")
        # Planning calls a model and can take a while, so it never runs on the scheduler thread.
        self.plan_pool = ThreadPoolExecutor(max_workers=plan_workers, thread_name_prefix="wf-plan")
        self._inflight: dict[int, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}  # running subtask id -> set when it is cancelled
        self._wake = threading.Event()  # set when there is new work, so the loop does not wait out its interval
        self._tick_lock = threading.Lock()
        self._goal_locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seed_skills()
        self._migrate_squads()

    # =====================================================================
    # agents
    # =====================================================================

    def _clean_agent(self, body: dict, partial: bool = False) -> dict:
        f: dict[str, Any] = {}
        if "name" in body or not partial:
            name = str(body.get("name", "")).strip()
            if not name:
                raise WorkforceError("员工名称不能为空")
            f["name"] = name[:40]
        for k, n in (("role", 60), ("description", 1000), ("system_prompt", 8000), ("model", 80)):
            if k in body:
                f[k] = str(body[k] or "").strip()[:n]
        if "provider" in body:
            if body["provider"] not in PROVIDERS:
                raise WorkforceError(f"模型提供方只能是 {', '.join(PROVIDERS)}")
            f["provider"] = body["provider"]
        if "model_config" in body:
            mc = body["model_config"] or {}
            if not isinstance(mc, dict):
                raise WorkforceError("模型配置必须是对象")
            f["model_config"] = {k: mc[k] for k in ("effort", *(b[0] for b in Limits.BOUNDS)) if k in mc}
        if "skills" in body:
            skills = list(dict.fromkeys(body["skills"] or []))
            bad = [s for s in skills if s not in SKILL_BY_ID]
            if bad:
                raise WorkforceError(f"没有这些技能：{', '.join(bad)}")
            f["skills"] = skills
        if "permissions" in body:
            perms = list(dict.fromkeys(body["permissions"] or []))
            bad = [p for p in perms if p not in ALL_PERMISSIONS]
            if bad:
                raise WorkforceError(f"没有这些权限：{', '.join(bad)}")
            f["permissions"] = perms
        if "is_manager" in body:
            f["is_manager"] = int(bool(body["is_manager"]))
        if "budget_usd" in body:
            f["budget_usd"] = str(_dec(body["budget_usd"] or 0, "预算"))
        if "max_concurrency" in body:
            try:
                n = int(body["max_concurrency"])
            except (TypeError, ValueError):
                raise WorkforceError("并发上限必须是整数") from None
            if not 1 <= n <= 20:
                raise WorkforceError("并发上限只能是 1–20")
            f["max_concurrency"] = n
        if "onchain" in body:
            oc = body["onchain"] or {}
            f["onchain"] = {k: str(oc[k])[:80] for k in ("agent_id", "address", "network", "tx") if oc.get(k)}
        return f

    def create_agent(self, body: dict, by: str) -> dict:
        f = self._clean_agent(body)
        if f.get("provider") == "anthropic" and not f.get("model"):
            f["model"] = self.planner.model
        if f.get("provider") == "openai_compat" and not f.get("model") and self.compat:
            f["model"] = self.compat.model
        a = self.db.add_agent(**f)
        self._audit(f"创建员工 {a['name']}（{a['id']}）", by, {"agent": a["id"]})
        return a

    def update_agent(self, aid: str, body: dict, by: str) -> dict:
        self._agent_or_404(aid)
        f = self._clean_agent(body, partial=True)
        a = self.db.update_agent(aid, **f)
        self._audit(f"修改员工 {a['name']}：{', '.join(f) or '无变化'}", by, {"agent": aid})
        return a

    def duplicate_agent(self, aid: str, by: str) -> dict:
        src = self._agent_or_404(aid)
        keep = ("role", "description", "system_prompt", "provider", "model", "model_config", "skills", "permissions", "is_manager", "budget_usd",
                "max_concurrency")
        a = self.db.add_agent(name=(src["name"] + " 副本")[:40], **{k: src[k] for k in keep})
        self._audit(f"复制员工 {src['name']} → {a['id']}", by, {"agent": a["id"], "from": aid})
        return a

    def set_agent_status(self, aid: str, status: str, by: str) -> dict:
        if status not in (D.AGENT_ACTIVE, D.AGENT_PAUSED, D.AGENT_ARCHIVED):
            raise WorkforceError("状态只能是 active / paused / archived")
        a = self._agent_or_404(aid)
        if status == D.AGENT_ARCHIVED and self._open_tasks_of(aid):
            raise WorkforceError("这个员工还有没结束的任务，先取消或等任务结束再归档")
        a = self.db.update_agent(aid, status=status)
        self._audit(f"员工 {a['name']} → {status}", by, {"agent": aid})
        self.db.post(workspace_id="", kind="system", event=f"agent.{status}", text=f"{a['name']} {dict(active='启用', paused='暂停', archived='归档')[status]}", sender="system", data={"agent": aid})
        return a

    def delete_agent(self, aid: str, by: str) -> None:
        a = self._agent_or_404(aid)
        if self._open_tasks_of(aid):
            raise WorkforceError("这个员工还有没结束的任务，不能删除")
        if self.db.tasks(agent=aid, limit=1)[1]:
            raise WorkforceError("这个员工有历史任务，删除会丢失执行记录；请改为归档")
        self.db.delete_agent(aid)
        for sq in self.db.squads(include_deleted=True):
            if aid in sq["members"] or sq["manager_id"] == aid:
                self.db.update_squad(sq["id"], members=[m for m in sq["members"] if m != aid],
                                     manager_id=None if sq["manager_id"] == aid else sq["manager_id"])
        for ws in self.db.workspaces():
            if aid in ws["members"] or ws["manager_id"] == aid:
                self.db.update_workspace(ws["id"], members=[m for m in ws["members"] if m != aid],
                                         manager_id=None if ws["manager_id"] == aid else ws["manager_id"])
            self._sync_workspace(ws["id"])
        self._audit(f"删除员工 {a['name']}（{aid}）", by, {"agent": aid})

    def _agent_or_404(self, aid: str) -> dict:
        a = self.db.agent(aid)
        if a is None:
            raise WorkforceError(f"没有员工 {aid}")
        return a

    def _open_tasks_of(self, aid: str) -> int:
        return self.db.tasks(agent=aid, status=[D.PENDING, D.ASSIGNED, D.RUNNING, D.WAITING], limit=1)[1]

    @staticmethod
    def over_budget(agent: dict) -> str | None:
        """The agent's own spending cap (budget_usd, 0 = none): None while it has room, else why it is out."""
        try:
            cap, spent = Decimal(str(agent.get("budget_usd") or "0")), Decimal(str(agent.get("cost_usd") or "0"))
        except InvalidOperation:
            return None
        if cap > 0 and spent >= cap:
            return f"预算已用完（${spent} / ${cap}）"
        return None

    def can_run(self, agent: dict, skill_id: str) -> str | None:
        """None if the agent may run the skill, else the reason it may not."""
        skill = SKILL_BY_ID.get(skill_id)
        if skill is None:
            return f"没有技能 {skill_id}"
        st = self.skill_state(skill_id)
        if not st["installed"]:
            return f"技能 {skill.name} 没有安装"
        if not st["enabled"]:
            return f"技能 {skill.name} 已停用"
        if skill_id not in agent["skills"]:
            return f"{agent['name']} 没有 {skill.name} 技能"
        missing = [p for p in skill.permissions if p not in agent["permissions"]]
        if missing:
            return f"{agent['name']} 缺少权限：{', '.join(ALL_PERMISSIONS[p] for p in missing)}"
        if skill_id == AGENT_TASK:
            return self.runtime.available(agent)
        return None

    # =====================================================================
    # workspaces
    # =====================================================================

    def create_workspace(self, body: dict, by: str) -> dict:
        name = str(body.get("name", "")).strip()[:60]
        if not name:
            raise WorkforceError("工作区名称不能为空")
        manager, members = self._check_members(body.get("manager_id"), body.get("members") or [])
        ws = self.db.add_workspace(name, str(body.get("description", ""))[:500], manager, members)
        if manager or members:
            # A roster given with the workspace becomes its first squad, so missions have a team.
            self.db.add_squad(workspace_id=ws["id"], name="Core squad", manager_id=manager, members=members,
                              permissions=list(DEFAULT_SQUAD_PERMISSIONS))
        self.db.post(workspace_id=ws["id"], kind="system", event="workspace.created", text=f"工作区 {name} 已建立", data={"members": members, "manager": manager})
        self._audit(f"建立工作区 {name}", by, {"workspace": ws["id"]})
        return self._sync_workspace(ws["id"])

    def update_workspace(self, wid: str, body: dict, by: str) -> dict:
        ws = self.workspace_or_404(wid)
        f: dict[str, Any] = {}
        if "name" in body:
            f["name"] = str(body["name"]).strip()[:60] or ws["name"]
        if "description" in body:
            f["description"] = str(body["description"])[:500]
        if "archived" in body:
            f["archived"] = int(bool(body["archived"]))
        ws = self.db.update_workspace(wid, **f)
        if "manager_id" in body or "members" in body:
            # The old roster API edits the workspace's default squad.
            manager, members = self._check_members(body.get("manager_id", ws["manager_id"]), body.get("members", ws["members"]))
            sq = self.default_squad(wid)
            if sq:
                self.db.update_squad(sq["id"], manager_id=manager, members=members)
            else:
                self.db.add_squad(workspace_id=wid, name="Core squad", manager_id=manager, members=members, permissions=list(DEFAULT_SQUAD_PERMISSIONS))
            ws = self._sync_workspace(wid)
        self._audit(f"修改工作区 {ws['name']}", by, {"workspace": wid})
        return ws

    def _check_members(self, manager_id: str | None, members: list[str]) -> tuple[str | None, list[str]]:
        members = list(dict.fromkeys(str(m) for m in members))
        for m in members + ([manager_id] if manager_id else []):
            if self.db.agent(m) is None:
                raise WorkforceError(f"没有员工 {m}")
        if manager_id and not self.db.agent(manager_id)["is_manager"]:
            raise WorkforceError("主管必须是标记为 AI Manager 的员工")
        return manager_id or None, [m for m in members if m != manager_id]

    def workspace_or_404(self, wid: str) -> dict:
        ws = self.db.workspace(wid)
        if ws is None:
            raise WorkforceError(f"没有工作区 {wid}")
        return ws

    def _sync_workspace(self, wid: str) -> dict:
        """The workspace roster is the union of its squads; older code paths read it from the workspace row."""
        squads = self.db.squads(wid)
        if squads:
            main = self.default_squad(wid)
            manager = main["manager_id"] if main else None
            ids = [x for sq in squads for x in [sq["manager_id"], *sq["members"]] if x]
            self.db.update_workspace(wid, manager_id=manager, members=[x for x in dict.fromkeys(ids) if x != manager])
        return self.db.workspace(wid)

    def _migrate_squads(self) -> None:
        """Workspaces from before squads existed: their roster becomes a squad, their missions belong to it."""
        rows = self.db._db.execute("SELECT id FROM wf_workspaces").fetchall()
        for r in rows:
            ws = self.db.workspace(r["id"])
            if self.db.squads(ws["id"], include_deleted=True) or not (ws["manager_id"] or ws["members"]):
                continue
            sq = self.db.add_squad(workspace_id=ws["id"], name="Core squad", manager_id=ws["manager_id"],
                                   members=[m for m in ws["members"] if m != ws["manager_id"]], permissions=list(DEFAULT_SQUAD_PERMISSIONS))
            self.db._db.execute("UPDATE wf_tasks SET squad_id = ? WHERE workspace_id = ? AND kind = 'goal' AND squad_id IS NULL", (sq["id"], ws["id"]))

    # =====================================================================
    # squads: a manager and members that take missions together
    # =====================================================================

    def squad_or_404(self, sid: str) -> dict:
        sq = self.db.squad(sid)
        if sq is None or sq["status"] == D.SQUAD_DELETED:
            raise WorkforceError(f"没有 Squad {sid}")
        return sq

    def default_squad(self, wid: str) -> dict | None:
        squads = self.db.squads(wid)
        return next((s for s in squads if s["status"] == D.SQUAD_ACTIVE and s["manager_id"]), None) or (squads[0] if squads else None)

    def _squad_roster(self, manager_id: Any, members: Any) -> tuple[str | None, list[str]]:
        if not isinstance(members, list):
            raise WorkforceError("成员必须是员工编号列表")
        members = list(dict.fromkeys(str(m) for m in members if m))
        manager_id = str(manager_id) if manager_id else None
        for m in members + ([manager_id] if manager_id else []):
            a = self.db.agent(m)
            if a is None:
                raise WorkforceError(f"没有员工 {m}")
            if a["status"] == D.AGENT_ARCHIVED:
                raise WorkforceError(f"{a['name']} 已归档，不能加入 Squad")
        return manager_id, [m for m in members if m != manager_id]

    @staticmethod
    def _squad_policy(perms: Any) -> list[str]:
        if not isinstance(perms, list):
            raise WorkforceError("Squad 权限必须是列表")
        bad = [p for p in perms if p not in SQUAD_PERMISSIONS]
        if bad:
            raise WorkforceError(f"没有这些 Squad 权限：{', '.join(map(str, bad))}")
        return list(dict.fromkeys(perms))

    @staticmethod
    def _parallel(v: Any) -> int:
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            raise WorkforceError("并行上限必须是整数") from None
        if not 0 <= n <= 50:
            raise WorkforceError("并行上限只能是 0–50（0 表示不限）")
        return n

    def create_squad(self, body: dict, by: str) -> dict:
        ws = self.workspace_or_404(str(body.get("workspace_id") or ""))
        name = str(body.get("name", "")).strip()[:60]
        if not name:
            raise WorkforceError("Squad 名称不能为空")
        manager, members = self._squad_roster(body.get("manager_id"), body.get("members") or [])
        sq = self.db.add_squad(workspace_id=ws["id"], name=name, description=str(body.get("description", ""))[:500], manager_id=manager,
                               members=members, permissions=self._squad_policy(body.get("permissions", DEFAULT_SQUAD_PERMISSIONS)),
                               max_parallel=self._parallel(body.get("max_parallel")))
        self._sync_workspace(ws["id"])
        self.db.post(workspace_id=ws["id"], kind="system", event="squad.created", sender="user",
                     text=f"Squad {name} 已建立：主管 {self._name(manager)}，{len(members)} 名成员", data={"squad": sq["id"]})
        self._audit(f"建立 Squad {name}（{sq['id']}）", by, {"squad": sq["id"], "workspace": ws["id"]})
        return sq

    def update_squad(self, sid: str, body: dict, by: str) -> dict:
        sq = self.squad_or_404(sid)
        f: dict[str, Any] = {}
        if "name" in body:
            f["name"] = str(body["name"]).strip()[:60] or sq["name"]
        if "description" in body:
            f["description"] = str(body["description"])[:500]
        if "permissions" in body:
            f["permissions"] = self._squad_policy(body["permissions"])
        if "max_parallel" in body:
            f["max_parallel"] = self._parallel(body["max_parallel"])
        if "manager_id" in body or "members" in body:
            manager, members = self._squad_roster(body.get("manager_id", sq["manager_id"]), body.get("members", sq["members"]))
            for gone in set([sq["manager_id"], *sq["members"]]) - set([manager, *members]) - {None}:
                self._check_can_leave(sq, gone)
            f["manager_id"], f["members"] = manager, members
        sq = self.db.update_squad(sid, **f)
        self._sync_workspace(sq["workspace_id"])
        self._audit(f"修改 Squad {sq['name']}：{', '.join(f) or '无变化'}", by, {"squad": sid})
        return sq

    def _check_can_leave(self, sq: dict, aid: str) -> None:
        busy = self.db._db.execute(
            "SELECT COUNT(*) AS n FROM wf_tasks s JOIN wf_tasks g ON g.id = s.root_id WHERE s.kind = 'subtask' AND s.assigned_agent = ?"
            " AND s.status IN (?, ?) AND g.squad_id = ?", (aid, D.ASSIGNED, D.RUNNING, sq["id"])).fetchone()["n"]
        if busy:
            raise WorkforceError(f"{self._name(aid)} 正在执行这个 Squad 的任务，等任务结束再移出")

    def squad_add_member(self, sid: str, aid: str, by: str) -> dict:
        sq = self.squad_or_404(sid)
        if aid == sq["manager_id"] or aid in sq["members"]:
            raise WorkforceError(f"{self._name(aid)} 已经在 Squad {sq['name']} 里")
        _, members = self._squad_roster(sq["manager_id"], sq["members"] + [aid])
        sq = self.db.update_squad(sid, members=members)
        self._sync_workspace(sq["workspace_id"])
        self.db.post(workspace_id=sq["workspace_id"], kind="system", event="squad.member_added", sender="user",
                     text=f"{self._name(aid)} 加入 Squad {sq['name']}", data={"squad": sid, "agent": aid})
        self._audit(f"Squad {sq['name']} 加入 {self._name(aid)}", by, {"squad": sid, "agent": aid})
        return sq

    def squad_remove_member(self, sid: str, aid: str, by: str) -> dict:
        sq = self.squad_or_404(sid)
        if aid == sq["manager_id"]:
            raise WorkforceError(f"{self._name(aid)} 是这个 Squad 的主管，先指定新主管再移出")
        if aid not in sq["members"]:
            raise WorkforceError(f"{self._name(aid)} 不在 Squad {sq['name']} 里")
        self._check_can_leave(sq, aid)
        sq = self.db.update_squad(sid, members=[m for m in sq["members"] if m != aid])
        self._sync_workspace(sq["workspace_id"])
        self.db.post(workspace_id=sq["workspace_id"], kind="system", event="squad.member_removed", sender="user",
                     text=f"{self._name(aid)} 离开 Squad {sq['name']}", data={"squad": sid, "agent": aid})
        self._audit(f"Squad {sq['name']} 移出 {self._name(aid)}", by, {"squad": sid, "agent": aid})
        return sq

    def set_squad_manager(self, sid: str, aid: str, by: str) -> dict:
        """The new manager leaves the member list; the old one stays on as a member."""
        sq = self.squad_or_404(sid)
        a = self._agent_or_404(aid)
        if a["status"] == D.AGENT_ARCHIVED:
            raise WorkforceError(f"{a['name']} 已归档，不能当主管")
        old = sq["manager_id"]
        members = [m for m in sq["members"] if m != aid] + ([old] if old and old != aid else [])
        sq = self.db.update_squad(sid, manager_id=aid, members=members)
        self._sync_workspace(sq["workspace_id"])
        self.db.post(workspace_id=sq["workspace_id"], kind="system", event="squad.manager", sender="user",
                     text=f"{a['name']} 成为 Squad {sq['name']} 的主管", data={"squad": sid, "agent": aid, "previous": old})
        self._audit(f"Squad {sq['name']} 主管 → {a['name']}", by, {"squad": sid, "agent": aid})
        return sq

    def set_squad_status(self, sid: str, status: str, by: str) -> dict:
        if status not in (D.SQUAD_ACTIVE, D.SQUAD_PAUSED):
            raise WorkforceError("状态只能是 active / paused")
        sq = self.squad_or_404(sid)
        sq = self.db.update_squad(sid, status=status)
        verb = "恢复" if status == D.SQUAD_ACTIVE else "暂停"
        self.db.post(workspace_id=sq["workspace_id"], kind="system", event=f"squad.{status}", sender="user",
                     text=f"Squad {sq['name']} 已{verb}" + ("：进行中的 Mission 不再派新任务，正在执行的会做完" if status == D.SQUAD_PAUSED else ""),
                     data={"squad": sid})
        self._audit(f"{verb} Squad {sq['name']}", by, {"squad": sid})
        if status == D.SQUAD_ACTIVE:
            for g in self.db.tasks(squad_id=sid, kind="goal", status=[D.WAITING], limit=200)[0]:
                self._dispatch(g["id"])
        return sq

    def delete_squad(self, sid: str, by: str) -> None:
        sq = self.squad_or_404(sid)
        if self.db.tasks(squad_id=sid, kind="goal", status=[D.PENDING, D.RUNNING, D.WAITING, D.REVIEW], limit=1)[1]:
            raise WorkforceError(f"Squad {sq['name']} 还有没结束的 Mission，先取消或等它们结束")
        if self.db.tasks(squad_id=sid, kind="goal", limit=1)[1]:
            self.db.update_squad(sid, status=D.SQUAD_DELETED)  # keeps the name on its finished missions
        else:
            self.db.delete_squad(sid)
        self._sync_workspace(sq["workspace_id"])
        self.db.post(workspace_id=sq["workspace_id"], kind="system", event="squad.deleted", sender="user", text=f"Squad {sq['name']} 已删除", data={"squad": sid})
        self._audit(f"删除 Squad {sq['name']}", by, {"squad": sid})

    def _executors(self, squad: dict | None, manager_id: str | None = None, ws: dict | None = None) -> list[str]:
        """Who may be given a task: the members, and the manager too when the squad allows it."""
        if squad:
            mgr = manager_id or squad["manager_id"]
            ids = list(squad["members"]) + ([squad["manager_id"]] if squad["manager_id"] else [])
            if "manager_executes" not in squad["permissions"]:
                ids = [x for x in ids if x != mgr]
            return list(dict.fromkeys(ids))
        return list(ws["members"]) if ws else []

    def _roster(self, goal: dict) -> list[dict]:
        sq = self.db.squad(goal["squad_id"]) if goal.get("squad_id") else None
        ids = self._executors(sq, goal["assigned_agent"], self.db.workspace(goal["workspace_id"]))
        return [a for a in (self.db.agent(x) for x in ids) if a and a["status"] == D.AGENT_ACTIVE]

    def squad_views(self, workspace_id: str | None = None) -> list[dict]:
        stats = self.db.squad_mission_stats()
        names = {w["id"]: w["name"] for w in self.db.workspaces()}
        out = []
        for sq in self.db.squads(workspace_id):
            st = stats.get(sq["id"], {"by_status": {}, "cost": 0.0})
            by = st["by_status"]
            done, failed = by.get(D.COMPLETED, 0), by.get(D.FAILED, 0)
            check = self.preflight(sq["workspace_id"], squad_id=sq["id"])
            able = [self.db.agent(x) for x in check["executors"]]
            skills = sorted({s for a in able if a for s in a["skills"] if self.can_run(a, s) is None})
            out.append({**sq, "workspace_name": names.get(sq["workspace_id"], sq["workspace_id"]),
                        "missions": {"active": sum(by.get(s, 0) for s in (D.PENDING, D.RUNNING, D.WAITING)), "review": by.get(D.REVIEW, 0),
                                     "completed": done, "failed": failed, "cancelled": by.get(D.CANCELLED, 0)},
                        "success_rate": round(done / (done + failed), 4) if done + failed else None, "cost_usd": round(st["cost"], 6),
                        "skills": skills, "executors": check["executors"], "running": self.db.running_subtasks(squad_id=sq["id"]),
                        "ready": check["ready"], "issues": [c for c in check["checks"] if c["level"] == "error"]})
        return out

    # =====================================================================
    # missions: check before creating, then submit
    # =====================================================================

    def preflight(self, wid: str, *, squad_id: str | None = None, manager_id: str | None = None, text: str = "",
                  budget_usd: Any = "1") -> dict:
        """Everything a mission needs, checked before it exists, so a mission is never created to fail.

        Each check says what is wrong and which fix the terminal should offer:
        create_squad, configure_squad, resume_squad, assign_manager, add_agent, create_agent, configure_manager.
        `ready` is the squad's state; `ok` also covers the goal text and the budget.
        """
        checks: list[dict] = []

        def add(key: str, level: str, msg: str, fix: str | None = None) -> None:
            checks.append({"key": key, "level": level, "text": msg, "fix": fix})

        out = {"ok": False, "ready": False, "workspace_id": wid, "squad_id": None, "manager_id": None, "planner": None,
               "executors": [], "checks": checks}
        ws = self.db.workspace(wid) if wid else None
        if ws is None or ws["archived"]:
            add("workspace", "error", f"没有工作区 {wid}" if wid else "先选一个工作区", "create_squad")
            return out
        sq = self.db.squad(squad_id) if squad_id else self.default_squad(wid)
        if squad_id and (sq is None or sq["status"] == D.SQUAD_DELETED or sq["workspace_id"] != wid):
            add("squad", "error", f"工作区 {ws['name']} 里没有 Squad {squad_id}", "configure_squad")
            return out
        if sq is None:
            add("squad", "error", f"工作区 {ws['name']} 还没有 Squad：先建一个 Squad，指定主管并加入员工", "create_squad")
            return out
        out["squad_id"] = sq["id"]
        if sq["status"] == D.SQUAD_PAUSED:
            add("squad", "error", f"Squad {sq['name']} 已暂停，恢复后才能接 Mission", "resume_squad")
        else:
            add("squad", "ok", f"Squad {sq['name']}")
        mid = manager_id or sq["manager_id"]
        manager = self.db.agent(mid) if mid else None
        if not mid:
            add("manager", "error", f"Squad {sq['name']} 还没有 AI 主管，先指定一个主管", "assign_manager")
        elif manager is None:
            add("manager", "error", f"没有员工 {mid}", "assign_manager")
        elif mid != sq["manager_id"] and mid not in sq["members"]:
            add("manager", "error", f"{manager['name']} 不在 Squad {sq['name']} 里，不能当这个 Mission 的主管", "assign_manager")
            manager = None
        elif manager["status"] != D.AGENT_ACTIVE:
            add("manager", "error", f"主管 {manager['name']} 不在岗（{manager['status']}）", "assign_manager")
            manager = None
        else:
            add("manager", "ok", f"主管 {manager['name']}")
        out["manager_id"] = manager["id"] if manager else None

        # Who can actually take a task, and if nobody, exactly why not.
        ids = self._executors(sq, mid)
        able, paused, unable = [], [], []
        for x in ids:
            a = self.db.agent(x)
            if a is None:
                continue
            runnable = [s for s in a["skills"] if self.can_run(a, s) is None]
            if a["status"] != D.AGENT_ACTIVE:
                paused.append(f"{a['name']}（{a['status']}）")
            elif not runnable:
                why = next((self.can_run(a, s) for s in a["skills"]), None) or f"{a['name']} 没有任何技能"
                unable.append(why)
            else:
                able.append(a)
        out["executors"] = [a["id"] for a in able]
        if not able:
            if not sq["members"]:
                why = f"Squad {sq['name']} 里除了主管还没有成员" + ("，主管自己也没有可用的技能" if ids else "") + "：先把有技能的员工加进来"
            else:
                why = f"Squad {sq['name']} 里没有能执行任务的员工：" + "；".join(([f"{'、'.join(paused)} 不在岗"] if paused else []) + unable)
            add("executors", "error", why, "add_agent")
        else:
            add("executors", "ok", f"{len(able)} 名员工可以执行：" + "、".join(a["name"] for a in able))
            if paused:
                add("executors_paused", "warn", f"{'、'.join(paused)} 不在岗，不会被分配任务", "configure_squad")
            busy = [a for a in able if self.db.running_subtasks(agent=a["id"]) >= a.get("max_concurrency", 2)]
            if len(busy) == len(able):
                add("capacity", "warn", "所有能执行的员工都在满负荷，新任务会排队等他们空出来")

        # How the manager will plan, and whether that plan can work for this goal.
        uses_model = self.planner.ready(manager)
        out["planner"] = "model" if uses_model else "rules"
        if manager and manager["provider"] == "anthropic" and not self.llm:
            add("planner_model", "warn", "主管配置了 Anthropic，但服务器没有 ANTHROPIC_API_KEY，会改用规则规划")
        if manager and manager["provider"] == "openai_compat" and not self.compat:
            add("planner_model", "warn", "主管配置了 OpenAI 兼容模型，但服务器没有设置 KAGOU_OPENAI_COMPAT_*，会改用规则规划")
        rule_able = [a for a in able if any(self.can_run(a, s) is None for s in RULE_SKILLS)]
        if uses_model:
            add("planner", "ok", f"主管用模型（{manager['model'] or (self.compat.model if manager['provider'] == 'openai_compat' else self.planner.model)}）拆分任务")
        elif text.strip() and not ADDRESS_RE.search(text):
            add("planner", "error", "没有模型时，主管只能拆分带 0x 地址的链上分析任务；在目标里写上地址，"
                                    "或给主管配置 Anthropic 模型（并在服务器设置 ANTHROPIC_API_KEY）", "configure_manager")
        elif able and not rule_able:
            add("planner", "error", "团队里没有能做链上分析的员工（需要钱包分析、合约分析、代币信息、合约事件或交易历史技能）", "add_agent")
        elif able:
            add("planner", "ok", "规则规划：按目标里的地址分派链上分析" + ("" if any(self.can_run(a, "data_report") is None for a in able) else "；团队里没有会数据报告的员工，结果不会汇总成报告"))
        if able and not uses_model and self.reader is None:
            add("chain", "error", "服务器没有配置链（CHAINSTAFF_CHAIN），链上分析技能都会失败", None)

        try:
            b = _dec(budget_usd, "预算")
            add("budget", "warn" if b == 0 else "ok", "预算 0 表示不设上限" if b == 0 else f"预算 ${b}")
        except WorkforceError as e:
            add("budget", "error", str(e))
        if self.anchor is None:
            add("anchor", "info", "没有配置测试网钱包：审批通过后是链下完成，不能上链")
        elif "anchor_results" not in sq["permissions"]:
            add("anchor", "info", f"Squad {sq['name']} 没有上链权限：审批通过后只在链下完成")

        squad_errors = [c for c in checks if c["level"] == "error" and c["key"] not in ("planner", "budget", "chain")]
        out["ready"] = not squad_errors
        out["ok"] = bool(text.strip()) and not any(c["level"] == "error" for c in checks)
        return out

    def submit_goal(self, wid: str, text: str, by: str, *, priority: int = 2, max_steps: int = 20,
                    budget_usd: Any = "1", timeout_s: int = 600, squad_id: str | None = None, manager_id: str | None = None,
                    attachments: list[dict] | None = None, idempotency_key: str | None = None) -> dict:
        self.workspace_or_404(wid)
        text = text.strip()
        key = (idempotency_key or "").strip()[:128] or None
        if key:
            prior = self.db.task_by_idempotency_key(key)
            if prior:
                return self._replayed(prior, wid, text, by)
        if not text:
            raise WorkforceError("任务内容不能为空")
        if not (0 <= int(priority) <= 3):
            raise WorkforceError("优先级只能是 0–3")
        if not (1 <= int(max_steps) <= 100):
            raise WorkforceError("最大步数只能是 1–100")
        if not (10 <= int(timeout_s) <= 86400):
            raise WorkforceError("超时只能是 10 秒到 24 小时")
        budget = _dec(budget_usd, "预算")
        files = self._attachments(attachments)
        full = text + "".join(f"\n\n--- 附件 {f['name']} ---\n{f['text']}" for f in files)
        check = self.preflight(wid, squad_id=squad_id, manager_id=manager_id, text=full, budget_usd=budget)
        errors = [c["text"] for c in check["checks"] if c["level"] == "error"]
        if errors:
            raise WorkforceError(errors[0] if len(errors) == 1 else "；".join(errors[:3]))
        manager = self.db.agent(check["manager_id"])
        squad = self.db.squad(check["squad_id"])
        deadline = _now() + timedelta(seconds=int(timeout_s))
        try:
            goal = self.db.add_task(workspace_id=wid, kind="goal", title=text.splitlines()[0][:120], description=full[:4000 + MAX_ATTACHMENT_CHARS],
                                    assigned_agent=manager["id"], priority=int(priority), status=D.PENDING, max_steps=int(max_steps),
                                    budget_usd=str(budget), timeout_s=int(timeout_s), deadline_at=_iso(deadline), created_by=by, squad_id=squad["id"],
                                    idempotency_key=key)
        except sqlite3.IntegrityError:
            prior = self.db.task_by_idempotency_key(key) if key else None
            if prior is None:
                raise
            return self._replayed(prior, wid, text, by)  # the same request won a race a moment ago
        self.db.post(workspace_id=wid, kind="user", event="user.instruction", sender="user", text=text, task_id=goal["id"],
                     data={"by": by, "squad": squad["id"], "attachments": [{"name": f["name"], "chars": len(f["text"])} for f in files]})
        self.db.post(workspace_id=wid, kind="task", event="task.created", task_id=goal["id"],
                     text=f"任务 {goal['id']} 已创建，交给 Squad {squad['name']} 的主管 {manager['name']}",
                     data={"priority": goal["priority"], "max_steps": goal["max_steps"], "budget_usd": goal["budget_usd"], "deadline": goal["deadline_at"],
                           "squad": squad["id"], "executors": check["executors"]})
        self.db.post(workspace_id=wid, kind="agent", event="goal.submitted", sender="user", recipient=manager["id"], task_id=goal["id"],
                     text=text, dedupe_key=f"goal:{goal['id']}")
        self._audit(f"提交任务 {goal['id']}：{goal['title']}（Squad {squad['name']}）", by, {"task": goal["id"], "squad": squad["id"]})
        self._wake.set()
        return goal

    def _replayed(self, prior: dict, wid: str, text: str, by: str) -> dict:
        """A retried submit: hand back the mission the first attempt created instead of creating another."""
        if prior["kind"] != "goal" or prior["workspace_id"] != wid or prior["created_by"] != by or not prior["description"].startswith(text):
            raise WorkforceError("这个请求编号（Idempotency-Key）已经用于另一个任务，请换一个新的编号")
        return {**prior, "replayed": True}

    @staticmethod
    def _attachments(files: Any) -> list[dict]:
        """Text attachments travel with the goal, so the manager and the planner read them."""
        if not files:
            return []
        if not isinstance(files, list) or len(files) > 5:
            raise WorkforceError("附件最多 5 个")
        out, total = [], 0
        for f in files:
            if not isinstance(f, dict) or not str(f.get("name", "")).strip():
                raise WorkforceError("附件要有文件名和文本内容")
            body = str(f.get("text", ""))
            total += len(body)
            if total > MAX_ATTACHMENT_CHARS:
                raise WorkforceError(f"附件文本太长（合计上限 {MAX_ATTACHMENT_CHARS} 字符）")
            out.append({"name": str(f["name"]).strip()[:80], "text": body})
        return out

    def assign_direct(self, aid: str, skill_id: str, inp: dict, by: str, *, workspace_id: str | None = None, title: str = "",
                      priority: int = 2, budget_usd: Any = "1", timeout_s: int = 600) -> dict:
        """Give one agent one skill run, without the manager's planning step.

        It is still a goal with one subtask, so it goes through the same bus, executor, budget,
        deadline, review approval and anchoring as a planned goal; the agent itself owns the goal.
        """
        agent = self._agent_or_404(aid)
        if agent["status"] != D.AGENT_ACTIVE:
            raise WorkforceError(f"{agent['name']} 不在岗（{agent['status']}）")
        skill = SKILL_BY_ID.get(skill_id)
        if skill is None:
            raise WorkforceError(f"没有技能 {skill_id}")
        if skill_id == "data_report":
            raise WorkforceError("数据报告只汇总同一任务里上游员工的结果，不能单独派给员工")
        reason = self.can_run(agent, skill_id)
        if reason:
            raise WorkforceError(reason)
        try:
            validate(skill.input_schema, inp)
        except SkillError as e:
            raise WorkforceError(f"输入不对：{e}") from e
        spaces = [w for w in self.db.workspaces() if not w["archived"] and (aid in w["members"] or w["manager_id"] == aid)]
        if workspace_id:
            spaces = [w for w in spaces if w["id"] == workspace_id]
        if not spaces:
            raise WorkforceError(f"{agent['name']} 不在{'这个' if workspace_id else '任何'}工作区里，先把它加进工作区")
        wid = spaces[0]["id"]
        squads = [q for w in spaces for q in self.db.squads(w["id"]) if aid == q["manager_id"] or aid in q["members"]]
        live = [q for q in squads if q["status"] == D.SQUAD_ACTIVE]
        if squads and not live:
            raise WorkforceError(f"{agent['name']} 所在的 Squad 已暂停，恢复后才能派活")
        squad = live[0] if live else None
        if squad:
            wid = squad["workspace_id"]
        if not (0 <= int(priority) <= 3):
            raise WorkforceError("优先级只能是 0–3")
        if not (10 <= int(timeout_s) <= 86400):
            raise WorkforceError("超时只能是 10 秒到 24 小时")
        budget = _dec(budget_usd, "预算")
        target = inp.get("address")
        title = (title.strip() or f"{skill.name}{' ' + target if target else ''}")[:120]
        goal = self.db.add_task(workspace_id=wid, kind="goal", title=title, description=f"{title}\n\n直接派给 {agent['name']}：{skill.name}",
                                assigned_agent=aid, priority=int(priority), status=D.WAITING, max_steps=2, budget_usd=str(budget),
                                timeout_s=int(timeout_s), deadline_at=_iso(_now() + timedelta(seconds=int(timeout_s))),
                                planner="direct", started_at=_iso(_now()), created_by=by, squad_id=squad["id"] if squad else None)
        sub = self.db.add_task(workspace_id=wid, kind="subtask", parent_id=goal["id"], root_id=goal["id"], title=title, skill=skill_id,
                               input=dict(inp), assigned_agent=aid, priority=int(priority), status=D.PENDING, created_by=by,
                               max_steps=1, budget_usd="0", timeout_s=int(timeout_s))
        self.db.post(workspace_id=wid, kind="user", event="user.instruction", sender="user", task_id=goal["id"],
                     text=f"{title}（直接派给 {agent['name']}）", data={"by": by, "direct": aid})
        self.db.post(workspace_id=wid, kind="task", event="task.created", task_id=goal["id"],
                     text=f"任务 {goal['id']} 已创建，直接派给 {agent['name']}",
                     data={"priority": goal["priority"], "max_steps": goal["max_steps"], "budget_usd": goal["budget_usd"], "deadline": goal["deadline_at"]})
        self.db.post(workspace_id=wid, kind="agent", event="plan.created", sender=aid, task_id=goal["id"],
                     text=f"直接派活，不经过主管拆分：\n1. {title} → {agent['name']}",
                     data={"planner": "direct", "steps": [{"key": "s1", "task": sub["id"], "title": title, "skill": skill_id,
                                                           "agent": aid, "depends_on": []}]})
        self._audit(f"直接派活 {goal['id']} 给 {agent['name']}：{skill.name}", by, {"task": goal["id"], "agent": aid})
        self._dispatch(goal["id"])
        return self.db.task(goal["id"])

    def cancel(self, tid: str, by: str, reason: str = "") -> dict:
        t = self.task_or_404(tid)
        root = self.db.task(t["root_id"])
        if root["status"] in D.FINAL:
            raise WorkforceError(f"任务已经{root['status']}，不能取消")
        self._end_goal(root, D.CANCELLED, reason or f"由 {by} 取消")
        self._audit(f"取消任务 {root['id']}", by, {"task": root["id"]})
        self._wake.set()
        return self.db.task(root["id"])

    def retry(self, tid: str, by: str) -> dict:
        """Recovery: put a failed subtask (and its goal) back in the queue."""
        t = self.task_or_404(tid)
        if t["kind"] != "subtask" or t["status"] != D.FAILED:
            raise WorkforceError("只能重试失败的子任务")
        root = self.db.task(t["root_id"])
        if root["status"] in (D.COMPLETED, D.CANCELLED):
            raise WorkforceError(f"所属任务已经{root['status']}，不能再重试")
        # attempts keeps counting (it keys message dedupe); the retry gets a fresh allowance on top.
        self.db.transition(tid, (D.FAILED,), D.PENDING, max_attempts=t["attempts"] + 2, error=None, finished_at=None)
        # Steps for the retried subtask are granted on top of the old limit.
        self.db.update_task(root["id"], status=D.WAITING, finished_at=None, error=None,
                            max_steps=max(root["max_steps"], root["steps"] + 1 + len(self.db.subtasks(root["id"]))),
                            deadline_at=_iso(_now() + timedelta(seconds=root["timeout_s"])))
        self.db.post(workspace_id=t["workspace_id"], kind="task", event="task.retry", task_id=tid, text=f"{by} 重新排队 {t['title']}")
        self._audit(f"重试子任务 {tid}", by, {"task": tid})
        self._dispatch(root["id"])
        return self.db.task(tid)

    def task_or_404(self, tid: str) -> dict:
        t = self.db.task(tid)
        if t is None:
            raise WorkforceError(f"没有任务 {tid}")
        return t

    def _end_goal(self, root: dict, status: str, reason: str) -> bool:
        """Fail or cancel a goal and everything under it. Running skills are told to stop, and whatever they
        return afterwards is discarded (see _execute). Returns False if the goal had already ended."""
        with self._goal_lock(root["id"]):
            if not self.db.transition(root["id"], (D.PENDING, D.RUNNING, D.WAITING, D.REVIEW), status, error=reason[:500]):
                return False
            for s in self.db.subtasks(root["id"]):
                if s["status"] not in D.FINAL and self.db.transition(s["id"], (s["status"],), D.CANCELLED, error=reason[:500]):
                    ev = self._cancel_events.get(s["id"])
                    if ev:
                        ev.set()
            for m in self.db.messages(root_id=root["id"], limit=1000):
                if m["recipient"] and m["status"] in (D.QUEUED, D.DELIVERED) and m["id"] not in self._inflight:
                    self.db.set_message_status(m["id"], (D.QUEUED, D.DELIVERED), D.EXPIRED)
            self.db.post(workspace_id=root["workspace_id"], kind="task", event=f"task.{status}", task_id=root["id"],
                         text=f"任务 {root['id']} {dict(failed='失败', cancelled='已取消')[status]}：{reason}")
            return True

    # =====================================================================
    # the bus
    # =====================================================================

    def send(self, sender: str, recipient: str, text: str, *, task_id: str | None = None, dedupe_key: str | None = None) -> int | None:
        """A directed message between agents (or from the user). Checks who may talk to whom."""
        rec = self._agent_or_404(recipient)
        if sender != "user":
            snd = self._agent_or_404(sender)
            if snd["status"] != D.AGENT_ACTIVE:
                raise WorkforceError(f"{snd['name']} 不在岗，不能发消息")
            shared = [w for w in self.db.workspaces() if {sender, recipient} <= set(w["members"] + [w["manager_id"]])]
            if not shared:
                raise WorkforceError(f"{snd['name']} 和 {rec['name']} 不在同一个工作区")
            peer_squad = any({sender, recipient} <= set(q["members"] + [q["manager_id"]]) and "peer_messages" in q["permissions"]
                             for q in self.db.squads() if q["status"] == D.SQUAD_ACTIVE)
            managing = any(sender == q["manager_id"] or recipient == q["manager_id"] for q in self.db.squads()
                           if {sender, recipient} <= set(q["members"] + [q["manager_id"]]))
            if not (snd["is_manager"] or rec["is_manager"] or managing or peer_squad or "message:peers" in snd["permissions"]):
                raise WorkforceError(f"{snd['name']} 没有给同事直接发消息的权限（message:peers，或 Squad 开启成员互发消息）")
            wid = shared[0]["id"]
        else:
            ws = [w for w in self.db.workspaces() if recipient in w["members"] + [w["manager_id"]]]
            wid = ws[0]["id"] if ws else ""
        return self.db.post(workspace_id=wid, kind="agent", event="agent.message", sender=sender, recipient=recipient,
                            text=text[:4000], task_id=task_id, dedupe_key=dedupe_key)

    def note(self, wid: str, text: str, by: str) -> int:
        """Human participation in the channel without starting a task."""
        self.workspace_or_404(wid)
        if not text.strip():
            raise WorkforceError("内容不能为空")
        return self.db.post(workspace_id=wid, kind="user", event="user.note", sender="user", text=text.strip()[:4000], data={"by": by})

    # =====================================================================
    # the loop
    # =====================================================================

    def start(self, interval_s: float = 1.0) -> None:  # pragma: no cover - background thread in the server process
        if self._thread:
            return
        self.recover()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # keep the loop alive; the error is on the timeline
                    self.db.post(workspace_id="", kind="system", event="engine.error", text=f"调度出错：{e}"[:500])
                # Finished work and new missions wake the loop at once; the interval is only the idle heartbeat.
                self._wake.wait(interval_s)
                self._wake.clear()

        self._thread = threading.Thread(target=loop, name="wf-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.plan_pool.shutdown(wait=True)
        self.pool.shutdown(wait=True)

    def recover(self) -> int:
        """After a restart: in-flight work from the previous process is put back in the queue."""
        n = self._recover_planning()
        for m in self.db.stale_messages(_now() + timedelta(days=1)):
            if m["status"] == D.DELIVERED:
                self.db.set_message_status(m["id"], (D.DELIVERED,), D.QUEUED, bump=True)
                n += 1
        for t in self.db.stale_tasks((D.RUNNING,), _now() + timedelta(days=1)):
            if t["kind"] == "subtask":
                self.db.transition(t["id"], (D.RUNNING,), D.ASSIGNED)
                n += 1
        if n:
            self.db.post(workspace_id="", kind="system", event="engine.recovered", text=f"重启后恢复了 {n} 项进行中的工作")
        return n

    def _recover_planning(self) -> int:
        """Goals the previous process had claimed for planning (status running) but never finished planning.

        Planning writes all subtasks and moves the goal to waiting in one transaction, so a running goal has
        either no subtasks (the plan was lost: plan again) or, from older versions, all of them (carry on).
        """
        n = 0
        for g in self.db.open_goals():
            if g["status"] != D.RUNNING or (g["plan_claim"] or {}).get("engine") == self.instance:
                continue
            claim = g["plan_claim"] or {}
            if self.db.subtasks(g["id"]):
                if self.db.transition(g["id"], (D.RUNNING,), D.WAITING):
                    self.db.post(workspace_id=g["workspace_id"], kind="system", event="plan.recovered", task_id=g["id"],
                                 text="重启前已写好的子任务继续执行")
                    n += 1
                continue
            if int(claim.get("attempt") or 1) >= MAX_PLAN_ATTEMPTS:
                self._end_goal(g, D.FAILED, f"规划被中断 {claim.get('attempt')} 次，不再重试")
                n += 1
                continue
            if not self.db.transition(g["id"], (D.RUNNING,), D.PENDING, plan_claim={**claim, "interrupted_at": _iso(_now())}):
                continue
            msg = self.db.goal_message(g["id"])
            if msg:
                self.db.requeue_message(msg["id"])
            else:
                self.db.post(workspace_id=g["workspace_id"], kind="agent", event="goal.submitted", sender="system", recipient=g["assigned_agent"],
                             task_id=g["id"], text=g["title"], dedupe_key=f"goal:{g['id']}:replan:{claim.get('attempt') or 1}")
            self.db.post(workspace_id=g["workspace_id"], kind="system", event="plan.requeued", task_id=g["id"],
                         text="规划在重启前被中断，已重新排队规划（不会重复创建子任务）")
            n += 1
        return n

    def tick(self) -> int:
        with self._tick_lock:
            done = [mid for mid, f in self._inflight.items() if f.done()]
            for mid in done:
                self._inflight.pop(mid)
            n = self._fire_schedules()
            n += self._enforce_deadlines()
            n += self._expire_messages()
            n += self._deliver()
            n += self._redispatch()
            n += self._poll_anchors()
            return n

    def drain(self, timeout_s: float = 30) -> None:
        """Wait for the agent runs and plans that are in flight right now (tests, CLI)."""
        for f in list(self._inflight.values()):
            f.result(timeout=timeout_s)

    def run_until_idle(self, max_rounds: int = 200, timeout_s: float = 60) -> None:
        """Drive the loop synchronously until nothing is left to do (tests, CLI)."""
        end = time.monotonic() + timeout_s
        for _ in range(max_rounds):
            n = self.tick()
            pending = [f for f in self._inflight.values() if not f.done()]
            for f in pending:
                f.result(timeout=max(0.1, end - time.monotonic()))
            # a job that finished after tick() looked is still in _inflight, and its follow-up messages are not delivered yet
            if n == 0 and not self._inflight:
                return
            if time.monotonic() > end:
                raise TimeoutError("workforce did not go idle")

    def _deliver(self) -> int:
        n = 0
        for a in self.db.agents(include_archived=False):
            if a["status"] != D.AGENT_ACTIVE:
                continue  # paused agents keep their inbox; it times out if they stay paused
            for m in self.db.inbox(a["id"]):
                if m["id"] in self._inflight:
                    continue
                if m["status"] == D.QUEUED and not self.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED, bump=True):
                    continue
                n += 1
                ev = m["event"]
                if ev == "task.assign":
                    self._inflight[m["id"]] = self.pool.submit(self._execute_safe, a["id"], m)
                elif ev == "goal.submitted":
                    goal = self._claim_plan(m)
                    if goal:
                        self._inflight[m["id"]] = self.plan_pool.submit(self._plan_job, goal["id"], a["id"], m["id"])
                elif ev == "task.result":
                    self._collect(m)
                else:
                    # handoffs wait for the dependent task to read them; plain notes are read now
                    if ev != "handoff":
                        self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
                    else:
                        n -= 1
        return n

    def _expire_messages(self) -> int:
        n = 0
        for m in self.db.stale_messages(_now() - self.msg_timeout):
            if m["id"] in self._inflight or m["event"] == "handoff":
                continue
            if m["status"] == D.DELIVERED and m["attempts"] < 3:
                self.db.set_message_status(m["id"], (D.DELIVERED,), D.QUEUED)
                n += 1
                continue
            if not self.db.set_message_status(m["id"], (D.QUEUED, D.DELIVERED), D.EXPIRED):
                continue
            n += 1
            self.db.post(workspace_id=m["workspace_id"], kind="system", event="message.expired", task_id=m["task_id"],
                         text=f"消息 #{m['id']} 超时未处理（收件人 {self._name(m['recipient'])}）")
            if m["event"] == "task.assign" and m["task_id"]:
                t = self.db.task(m["task_id"])
                if t and t["status"] == D.ASSIGNED:
                    self._fail_attempt(t, f"{self._name(m['recipient'])} 超时没有接手任务", transient=True)
            if m["event"] == "goal.submitted" and m["task_id"]:
                g = self.db.task(m["task_id"])
                if g and g["status"] == D.PENDING:
                    self._end_goal(g, D.FAILED, "主管超时没有接手任务")
        return n

    def _enforce_deadlines(self) -> int:
        n = 0
        now = _now()
        for g in self.db.open_goals():
            if g["status"] == D.REVIEW:
                continue  # waiting for a human, not for the agents
            if g["deadline_at"] and datetime.fromisoformat(g["deadline_at"]) < now:
                self._end_goal(g, D.FAILED, f"超时（{g['timeout_s']} 秒）")
                n += 1
        return n

    # =====================================================================
    # manager: plan, dispatch, collect, finalize
    # =====================================================================

    def _goal_lock(self, root_id: str) -> threading.RLock:
        with self._locks_lock:
            return self._goal_locks.setdefault(root_id, threading.RLock())

    def _plan(self, manager: dict, m: dict) -> None:
        """Claim and plan a goal on the calling thread (tests and tools; the loop plans on plan_pool)."""
        goal = self._claim_plan(m)
        if goal:
            self._plan_job(goal["id"], manager["id"], m["id"])

    def _claim_plan(self, m: dict) -> dict | None:
        """Take a submitted goal for planning: pending -> running, with a claim saying who and which attempt."""
        goal = self.db.task(m["task_id"]) if m["task_id"] else None
        if goal is None or goal["status"] != D.PENDING:
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            return None
        attempt = int((goal["plan_claim"] or {}).get("attempt") or 0) + 1
        claim = {"engine": self.instance, "at": _iso(_now()), "attempt": attempt, "message": m["id"]}
        if not self.db.transition(goal["id"], (D.PENDING,), D.RUNNING, plan_claim=claim):
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            return None
        return self.db.task(goal["id"])

    def _plan_job(self, goal_id: str, manager_id: str, mid: int) -> None:
        try:
            self._plan_claimed(goal_id, manager_id)
        except Exception as e:  # an unexpected error must not leave the goal in planning forever
            g = self.db.task(goal_id)
            if g and g["status"] == D.RUNNING:
                self._end_goal(g, D.FAILED, f"规划出错：{type(e).__name__}: {e}"[:300])
        finally:
            self.db.set_message_status(mid, (D.DELIVERED,), D.PROCESSED)
            self._wake.set()

    def _plan_claimed(self, goal_id: str, manager_id: str) -> None:
        goal = self.db.task(goal_id)
        manager = self.db.agent(manager_id)
        if goal is None or goal["status"] != D.RUNNING or manager is None:
            return
        attempt = (goal["plan_claim"] or {}).get("attempt")
        members = self._roster(goal)
        wid = goal["workspace_id"]
        self.db.post(workspace_id=wid, kind="agent", event="agent.started", sender=manager["id"], task_id=goal["id"],
                     text=f"收到任务，正在按 {len(members)} 名在岗员工的技能拆分工作流",
                     data={"executors": [a["id"] for a in members], "squad": goal.get("squad_id")})

        def gone() -> bool:
            g = self.db.task(goal_id)
            return g is None or g["status"] != D.RUNNING or (g["plan_claim"] or {}).get("attempt") != attempt

        broke = self.over_budget(manager)
        if broke and self.planner.ready(manager):
            self.db.post(workspace_id=wid, kind="system", event="plan.note", task_id=goal["id"], sender=manager["id"],
                         text=f"{manager['name']} {broke}，这次不调用模型，改用规则规划")
        try:
            plan = self.planner.plan(goal["description"], manager, members, self, use_model=broke is None, cancelled=gone)
        except PlanCancelled as e:
            self._charge(goal["id"], manager["id"], e.usage)
            self._discard_plan(goal, manager["id"])
            return
        except PlanError as e:
            if gone():
                self._discard_plan(goal, manager["id"])
                return
            self.db.bump_task(goal["id"], steps=1)
            self._end_goal(self.db.task(goal["id"]), D.FAILED, str(e))
            return
        self._charge(goal["id"], manager["id"], plan.usage)  # the model was paid for even if the plan is thrown away
        with self._goal_lock(goal_id), self.db.atomic():
            if gone():
                written = None
            elif self.db.subtasks(goal_id):
                return  # pragma: no cover - already planned (cannot happen with one claim, but never write a second set of subtasks)
            else:
                written = self._write_plan(goal, manager, plan)
        if written is None:
            self._discard_plan(goal, manager["id"])
            return
        self._dispatch(goal_id)

    def _discard_plan(self, goal: dict, manager_id: str) -> None:
        self.db.post(workspace_id=goal["workspace_id"], kind="system", event="plan.discarded", task_id=goal["id"], sender=manager_id,
                     text="任务在规划期间已结束（取消或超时），规划结果作废，没有创建子任务")

    def _write_plan(self, goal: dict, manager: dict, plan) -> list[str]:
        """Subtasks, the plan event and goal running -> waiting; the caller holds a transaction around it."""
        wid = goal["workspace_id"]
        self.db.update_task(goal["id"], planner=plan.planner)
        if plan.notes:
            self.db.post(workspace_id=wid, kind="system", event="plan.note", task_id=goal["id"], sender=manager["id"], text="；".join(plan.notes))
        keys: dict[str, str] = {}
        for step in plan.steps:
            t = self.db.add_task(workspace_id=wid, kind="subtask", parent_id=goal["id"], root_id=goal["id"], title=step.title[:120],
                                 skill=step.skill, input=step.input, assigned_agent=step.agent_id, priority=goal["priority"],
                                 status=D.PENDING, depends_on=[keys[k] for k in step.depends_on], created_by=manager["id"],
                                 max_steps=1, budget_usd="0", timeout_s=goal["timeout_s"])
            keys[step.key] = t["id"]
        lines = [f"{i + 1}. {s.title} → {self._name(s.agent_id)}" + (f"（等 {', '.join(s.depends_on)}）" if s.depends_on else "")
                 for i, s in enumerate(plan.steps)]
        self.db.post(workspace_id=wid, kind="agent", event="plan.created", sender=manager["id"], task_id=goal["id"],
                     text=f"拆成 {len(plan.steps)} 个子任务（{plan.planner_label}）：\n" + "\n".join(lines),
                     data={"planner": plan.planner, "steps": [{"key": s.key, "task": keys[s.key], "title": s.title, "skill": s.skill,
                                                               "agent": s.agent_id, "depends_on": [keys[k] for k in s.depends_on]} for s in plan.steps]})
        self.db.bump_task(goal["id"], steps=1)
        self.db.transition(goal["id"], (D.RUNNING,), D.WAITING)
        return list(keys.values())

    def _dispatch(self, root_id: str) -> int:
        with self._goal_lock(root_id):
            goal = self.db.task(root_id)
            if goal is None or goal["status"] != D.WAITING:
                return 0
            # Dynamic delegation: once the manager knows what kind of address it is, it adds the right follow-ups.
            # Done here, before anything is handed out, so the report never starts ahead of the follow-ups.
            if goal["planner"] == "rules":
                for s in self.db.subtasks(root_id):
                    if s["status"] == D.COMPLETED and s["skill"] == "wallet_analysis" and not self.db.has_message(f"expand:{s['id']}"):
                        self._expand(goal, s)
            subs = {s["id"]: s for s in self.db.subtasks(root_id)}
            n = 0
            for s in sorted(subs.values(), key=lambda x: (x["priority"], x["created_at"])):
                if s["status"] != D.PENDING:
                    continue
                deps = [subs[d] for d in s["depends_on"] if d in subs]
                if any(d["status"] not in D.FINAL for d in deps):
                    continue
                tolerant = SKILL_BY_ID.get(s["skill"]) and s["skill"] == "data_report"
                if not tolerant and any(d["status"] != D.COMPLETED for d in deps):
                    self.db.transition(s["id"], (D.PENDING,), D.CANCELLED, error="前置任务没有完成")
                    self.db.post(workspace_id=s["workspace_id"], kind="task", event="task.cancelled", task_id=s["id"],
                                 text=f"{s['title']} 取消：前置任务没有完成")
                    continue
                goal = self.db.task(root_id)
                squad = self.db.squad(goal["squad_id"]) if goal.get("squad_id") else None
                if squad and squad["status"] == D.SQUAD_PAUSED:
                    self.db.post(workspace_id=goal["workspace_id"], kind="system", event="squad.holding", task_id=root_id,
                                 text=f"Squad {squad['name']} 已暂停，{s['title']} 等恢复后再派", dedupe_key=f"hold:{root_id}:{s['id']}:{s['attempts']}")
                    break
                s = self._ensure_available(goal, s)
                if s is None:
                    continue
                agent = self.db.agent(s["assigned_agent"])
                if self.db.running_subtasks(agent=agent["id"]) >= agent.get("max_concurrency", 2):
                    self.db.post(workspace_id=goal["workspace_id"], kind="task", event="task.queued", task_id=s["id"], sender=goal["assigned_agent"],
                                 text=f"{s['title']} 排队：{agent['name']} 手上已有 {agent.get('max_concurrency', 2)} 个任务（并发上限）",
                                 dedupe_key=f"queued:{s['id']}:{s['attempts']}:agent")
                    continue
                if squad and squad["max_parallel"] and self.db.running_subtasks(squad_id=squad["id"]) >= squad["max_parallel"]:
                    self.db.post(workspace_id=goal["workspace_id"], kind="task", event="task.queued", task_id=s["id"], sender=goal["assigned_agent"],
                                 text=f"{s['title']} 排队：Squad {squad['name']} 同时最多执行 {squad['max_parallel']} 个任务",
                                 dedupe_key=f"queued:{s['id']}:{s['attempts']}:squad")
                    break
                if goal["steps"] + 1 > goal["max_steps"]:
                    self._end_goal(goal, D.FAILED, f"达到最大执行步数 {goal['max_steps']}")
                    return n
                if Decimal(goal["budget_usd"]) > 0 and Decimal(goal["cost_usd"]) >= Decimal(goal["budget_usd"]):
                    self._ask_budget(goal)
                    return n
                if not self.db.transition(s["id"], (D.PENDING,), D.ASSIGNED, attempts=s["attempts"] + 1):
                    continue
                self.db.bump_task(root_id, steps=1)
                self.db.post(workspace_id=s["workspace_id"], kind="agent", event="task.assign", sender=goal["assigned_agent"],
                             recipient=s["assigned_agent"], task_id=s["id"], dedupe_key=f"assign:{s['id']}:{s['attempts'] + 1}",
                             text=f"{s['title']}", data={"skill": s["skill"], "input": s["input"], "attempt": s["attempts"] + 1})
                self.db.post(workspace_id=s["workspace_id"], kind="task", event="task.assigned", task_id=s["id"], sender=goal["assigned_agent"],
                             text=f"{s['title']} 分配给 {self._name(s['assigned_agent'])}", data={"agent": s["assigned_agent"], "attempt": s["attempts"] + 1})
                n += 1
            self._maybe_finalize(root_id)
            return n

    def _ask_budget(self, goal: dict) -> None:
        if any(a["kind"] == "over_budget" and a["status"] == "pending" for a in self.db.approvals(task_id=goal["id"])):
            return
        aid = self.db.add_approval(workspace_id=goal["workspace_id"], task_id=goal["id"], kind="over_budget",
                                   title=f"任务 {goal['id']} 已用完预算 ${goal['budget_usd']}，是否追加同等预算继续？",
                                   detail={"cost_usd": goal["cost_usd"], "budget_usd": goal["budget_usd"]}, requested_by=goal["assigned_agent"])
        self.db.post(workspace_id=goal["workspace_id"], kind="system", event="approval.requested", task_id=goal["id"],
                     text=f"预算用完，等待人工审批 #{aid}", data={"approval": aid, "kind": "over_budget"})

    def _collect(self, m: dict) -> None:
        self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
        t = self.db.task(m["task_id"]) if m["task_id"] else None
        if t is None:
            return
        root = self.db.task(t["root_id"])
        if root["status"] != D.WAITING:
            return
        self._dispatch(root["id"])  # expands rule plans first, see _dispatch

    def _expand(self, root: dict, done: dict) -> None:
        addr = done["input"]["address"]
        follow = ["contract_analysis", "token_info", "contract_events"] if done["result"].get("is_contract") else ["tx_history"]
        if done["result"].get("is_contract"):
            follow.append("tx_history")
        members = self._roster(root)
        subs = self.db.subtasks(root["id"])
        reports = [s for s in subs if s["skill"] == "data_report" and s["status"] == D.PENDING]
        added, skipped = [], []
        for skill in follow:
            if any(s["skill"] == skill and s["input"].get("address") == addr for s in subs):
                continue
            agent = self.planner.pick(members, skill, self)
            if agent is None:
                skipped.append(SKILL_BY_ID[skill].name)
                continue
            t = self.db.add_task(workspace_id=root["workspace_id"], kind="subtask", parent_id=root["id"], root_id=root["id"],
                                 title=f"{SKILL_BY_ID[skill].name} {addr[:10]}…", skill=skill, input={"address": addr},
                                 assigned_agent=agent["id"], priority=root["priority"], status=D.PENDING, depends_on=[done["id"]],
                                 created_by=root["assigned_agent"], max_steps=1, budget_usd="0", timeout_s=root["timeout_s"])
            added.append(t)
        for r in reports:
            self.db.update_task(r["id"], depends_on=r["depends_on"] + [t["id"] for t in added])
        if added or skipped:
            kind = "合约" if done["result"].get("is_contract") else "外部账户"
            text = f"{addr[:10]}… 是{kind}，追加 {len(added)} 个子任务：" + "，".join(f"{t['title']} → {self._name(t['assigned_agent'])}" for t in added)
            if skipped:
                text += f"；团队里没有会 {', '.join(skipped)} 的员工，跳过"
            self.db.post(workspace_id=root["workspace_id"], kind="agent", event="plan.expanded", sender=root["assigned_agent"], task_id=root["id"],
                         text=text, data={"added": [t["id"] for t in added]}, dedupe_key=f"expand:{done['id']}")
        else:
            self.db.post(workspace_id=root["workspace_id"], kind="system", event="plan.checked", task_id=root["id"],
                         text=f"{addr[:10]}… 不需要追加子任务", dedupe_key=f"expand:{done['id']}")

    def _maybe_finalize(self, root_id: str) -> None:
        goal = self.db.task(root_id)
        subs = self.db.subtasks(root_id)
        if goal["status"] != D.WAITING or not subs or any(s["status"] not in D.FINAL for s in subs):
            return
        done = [s for s in subs if s["status"] == D.COMPLETED]
        if not done:
            self._end_goal(goal, D.FAILED, "所有子任务都没有完成")
            return
        report = next((s for s in reversed(done) if s["skill"] == "data_report"), None)
        result = {
            "report_md": report["result"]["report_md"] if report else None,
            "findings": report["result"].get("findings", []) if report else [],
            "subtasks": [{"id": s["id"], "title": s["title"], "skill": s["skill"], "agent": s["assigned_agent"], "status": s["status"],
                          "result_hash": s["result_hash"], "error": s["error"]} for s in subs],
        }
        if goal["planner"] == "direct":
            result["output"] = done[0]["result"]  # one agent, one skill: the deliverable is that skill's output
        h = report["result"]["sha256"] if report else result_hash(result)
        if not self.db.transition(root_id, (D.WAITING,), D.REVIEW, result=result, result_hash=h):
            return
        failed = [s for s in subs if s["status"] != D.COMPLETED]
        aid = self.db.add_approval(workspace_id=goal["workspace_id"], task_id=root_id, kind="task_result",
                                   title=f"审核任务结果：{goal['title']}",
                                   detail={"result_hash": h, "completed": len(done), "not_completed": len(failed), "cost_usd": goal["cost_usd"]},
                                   requested_by=goal["assigned_agent"])
        self.db.post(workspace_id=goal["workspace_id"], kind="agent", event="goal.review", sender=goal["assigned_agent"], task_id=root_id,
                     text=f"{len(done)}/{len(subs)} 个子任务完成，报告已汇总，提交人工审核", data={"approval": aid, "result_hash": h})
        self.db.post(workspace_id=goal["workspace_id"], kind="system", event="approval.requested", task_id=root_id,
                     text=f"等待审批 #{aid}：{goal['title']}", data={"approval": aid, "kind": "task_result"})

    # =====================================================================
    # worker
    # =====================================================================

    def _execute_safe(self, agent_id: str, m: dict) -> None:
        try:
            self._execute(agent_id, m)
        except Exception as e:  # pragma: no cover - last-resort guard so a thread never dies silently
            self.db.post(workspace_id=m["workspace_id"], kind="system", event="engine.error", task_id=m["task_id"], text=f"执行出错：{e}"[:500])
        finally:
            self._wake.set()

    def _execute(self, agent_id: str, m: dict) -> None:
        agent = self.db.agent(agent_id)
        t = self.db.task(m["task_id"])
        if t is None or t["status"] != D.ASSIGNED or t["assigned_agent"] != agent_id:
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            return
        wid = t["workspace_id"]
        reason = self.can_run(agent, t["skill"])
        if reason:
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            self.db.transition(t["id"], (D.ASSIGNED,), D.RUNNING)
            self._fail_attempt(self.db.task(t["id"]), f"权限拒绝：{reason}", transient=False, reassignable=True)
            return
        # Registered before the claim, so a cancel that lands right after it still reaches this run.
        stop = self._cancel_events[t["id"]] = threading.Event()
        try:
            if not self.db.transition(t["id"], (D.ASSIGNED,), D.RUNNING):
                self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
                return
            self._run_skill(agent, t, m, stop)
        finally:
            self._cancel_events.pop(t["id"], None)

    def _run_skill(self, agent: dict, t: dict, m: dict, stop: threading.Event) -> None:
        agent_id, wid = agent["id"], t["workspace_id"]
        skill = SKILL_BY_ID[t["skill"]]
        handoffs = [h for h in self.db.inbox(agent_id, limit=200) if h["event"] == "handoff" and (h["data"] or {}).get("task") in t["depends_on"]]
        upstream = [h["data"] for h in handoffs]
        self.db.post(workspace_id=wid, kind="agent", event="agent.working", sender=agent_id, task_id=t["id"],
                     text=f"开始：{t['title']}" + (f"（收到 {len(upstream)} 份同事交接的结果）" if upstream else ""))
        self.db.post(workspace_id=wid, kind="tool", event="tool.start", sender=agent_id, task_id=t["id"], text=f"{skill.id} v{skill.version}",
                     data={"skill": skill.id, "input": t["input"]})
        ctx = SkillContext(None, None, self.tokens, upstream, cancelled=stop.is_set, events=self.events)
        # This run's own reader: it counts only this run's RPC calls and stops reading once the task is cancelled.
        ctx.reader = self.reader.fork(ctx.check) if self.reader else None
        if self.etherscan:
            ctx.etherscan = lambda params: self._checked(ctx, self.etherscan, params)
        started = time.monotonic()
        try:
            validate(skill.input_schema, t["input"])
            ctx.check()
            if t["skill"] == AGENT_TASK:
                out = self.runtime.run(agent, t, ctx)  # the agent reasons and calls its own tools; each has its own timeout
            else:
                ctx.deadline = time.monotonic() + skill.timeout_s
                out = skill.run(ctx, dict(t["input"]))
            ctx.check()
            if t["skill"] == "data_report" and self.planner.ready(agent) and self.over_budget(agent) is None:
                summary, usage = self.planner.summarize(agent, out["report_md"])
                self._charge(t["id"], agent_id, usage)
                ctx.check()
                out["report_md"] += "\n\n## 摘要（模型生成）\n\n" + summary
                out["sha256"] = "0x" + hashlib.sha256(out["report_md"].encode()).hexdigest()
                ctx.log(f"模型摘要 {usage['input_tokens']}+{usage['output_tokens']} tokens")
        except Cancelled:
            self._discard_run(t, agent_id, ctx, started, m)
            return
        except Exception as e:
            error = str(e) if isinstance(e, SkillError) else f"{type(e).__name__}: {e}"[:300]
            with self._goal_lock(t["root_id"]):
                if stop.is_set() or (self.db.task(t["id"]) or {}).get("status") != D.RUNNING:
                    self._discard_run(t, agent_id, ctx, started, m)
                    return
                self._tool_done(t, agent_id, ctx, started, error=error)
                self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
                self._fail_attempt(self.db.task(t["id"]), error, transient=not isinstance(e, SkillError))
            return
        h = out.get("sha256") if t["skill"] == "data_report" else result_hash(out)
        # Commit under the goal lock: a cancel either lands first (and the result is discarded) or after (and
        # everything below is already on the timeline before the cancellation). Never both.
        with self._goal_lock(t["root_id"]):
            if stop.is_set() or not self.db.transition(t["id"], (D.RUNNING,), D.COMPLETED, result=out, result_hash=h, error=None):
                self._discard_run(t, agent_id, ctx, started, m)
                return
            self._tool_done(t, agent_id, ctx, started, output=out)
            for hm in handoffs:
                self.db.set_message_status(hm["id"], (D.QUEUED, D.DELIVERED), D.PROCESSED)
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)  # the assignment is done; do not deliver it again
            self.db.post(workspace_id=wid, kind="task", event="task.completed", sender=agent_id, task_id=t["id"],
                         text=f"{t['title']} 完成", data={"result_hash": h, "secs": round(time.monotonic() - started, 2)})
            self.db.remember("agent", agent_id, f"[{t['id']}] {t['title']}：{self._brief(out)}", source=t["id"], ttl_days=90)
            if t["skill"] == "data_report":
                self.db.remember("workspace", wid, f"[{t['root_id']}] {out['title']}：" + ("；".join(out["findings"]) or "无风险点"), source=t["id"])
            self._handoff(t, agent_id, out)
            self._report_to_manager(t, agent_id, ok=True)

    @staticmethod
    def _checked(ctx: SkillContext, fn: Callable[[dict], Any], params: dict) -> Any:
        ctx.check()
        out = fn(params)
        ctx.check()
        return out

    def _discard_run(self, t: dict, agent_id: str, ctx: SkillContext, started: float, m: dict) -> None:
        """The task was cancelled or timed out while this run was going: nothing it produced is committed."""
        self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
        self.db.post(workspace_id=t["workspace_id"], kind="tool", event="tool.cancelled", sender=agent_id, task_id=t["id"],
                     text="任务已取消或超时，停止执行，本次结果作废",
                     data={"skill": t["skill"], "secs": round(time.monotonic() - started, 3), "logs": ctx.logs,
                           "rpc_calls": ctx.reader.calls if ctx.reader else 0})

    def _tool_done(self, t: dict, agent_id: str, ctx: SkillContext, started: float, output: dict | None = None, error: str | None = None) -> None:
        secs = round(time.monotonic() - started, 3)
        calls = ctx.reader.calls if ctx.reader else 0  # this run's calls only (each run reads through its own fork)
        self.db.post(workspace_id=t["workspace_id"], kind="tool", event="tool.error" if error else "tool.done", sender=agent_id, task_id=t["id"],
                     text=(f"失败：{error}" if error else f"完成，用时 {secs}s"),
                     data={"skill": t["skill"], "secs": secs, "logs": ctx.logs, "error": error, "rpc_calls": calls,
                           "output_preview": json.dumps(output, ensure_ascii=False)[:1500] if output else None})

    def _handoff(self, t: dict, agent_id: str, out: dict) -> None:
        for s in self.db.subtasks(t["root_id"]):
            if t["id"] in s["depends_on"] and s["status"] == D.PENDING:
                self.db.post(workspace_id=t["workspace_id"], kind="agent", event="handoff", sender=agent_id, recipient=s["assigned_agent"],
                             task_id=s["id"], dedupe_key=f"handoff:{t['id']}:{s['id']}",
                             text=f"交接 {t['title']} 的结果给 {self._name(s['assigned_agent'])}",
                             data={"task": t["id"], "skill": t["skill"], "agent": agent_id, "agent_name": self._name(agent_id),
                                   "result": out, "error": None})

    def _report_to_manager(self, t: dict, agent_id: str, ok: bool) -> None:
        root = self.db.task(t["root_id"])
        self.db.post(workspace_id=t["workspace_id"], kind="agent", event="task.result", sender=agent_id, recipient=root["assigned_agent"],
                     task_id=t["id"], dedupe_key=f"result:{t['id']}:{t['attempts']}:{'ok' if ok else 'fail'}",
                     text=f"{t['title']}：{'完成' if ok else '失败'}")

    def _fail_attempt(self, t: dict, error: str, transient: bool, reassignable: bool | None = None) -> None:
        """A failed attempt: retry the same agent if it may be a blip, else let the manager hand it to someone else.

        Errors that come from the input itself (a SkillError) are not reassigned: another agent would fail the same way.
        """
        wid = t["workspace_id"]
        if transient and t["attempts"] < t["max_attempts"]:
            if self.db.transition(t["id"], (D.RUNNING, D.ASSIGNED), D.PENDING, error=error[:500]):
                self.db.post(workspace_id=wid, kind="task", event="task.retry", task_id=t["id"],
                             text=f"{t['title']} 第 {t['attempts']} 次失败，稍后重试：{error}"[:500])
                self._dispatch(t["root_id"])
            return
        if reassignable if reassignable is not None else transient:
            root = self.db.task(t["root_id"])
            alt = self._pick_other(root, t)
            if alt and self.db.transition(t["id"], (D.RUNNING, D.ASSIGNED), D.PENDING, error=error[:500], max_attempts=t["attempts"] + 1):
                self._reassign(root, self.db.task(t["id"]), alt, f"{self._name(t['assigned_agent'])} 没能完成（{error[:120]}）")
                self._dispatch(t["root_id"])
                return
        if not self.db.transition(t["id"], (D.RUNNING, D.ASSIGNED), D.FAILED, error=error[:500]):
            return
        self._announce_failure(t, error)

    def _announce_failure(self, t: dict, error: str) -> None:
        wid = t["workspace_id"]
        self.db.post(workspace_id=wid, kind="task", event="task.failed", task_id=t["id"], sender=t["assigned_agent"],
                     text=f"{t['title']} 失败：{error}"[:500])
        # Dependent tasks that tolerate failures (the report) still need to hear about it.
        for s in self.db.subtasks(t["root_id"]):
            if t["id"] in s["depends_on"] and s["status"] == D.PENDING:
                self.db.post(workspace_id=wid, kind="agent", event="handoff", sender=t["assigned_agent"], recipient=s["assigned_agent"],
                             task_id=s["id"], dedupe_key=f"handoff:{t['id']}:{s['id']}:failed",
                             text=f"{t['title']} 没有完成，告知 {self._name(s['assigned_agent'])}",
                             data={"task": t["id"], "skill": t["skill"], "agent": t["assigned_agent"], "agent_name": self._name(t["assigned_agent"]),
                                   "result": None, "error": error})
        self._report_to_manager(self.db.task(t["id"]), t["assigned_agent"], ok=False)

    # ---- the manager keeping work moving when an agent drops out ----

    def _tried_agents(self, tid: str) -> set[str]:
        return {(m["data"] or {}).get("agent") for m in self.db.messages(task_id=tid, limit=500) if m["event"] == "task.assigned"} - {None}

    def _pick_other(self, goal: dict, t: dict) -> dict | None:
        """Another in-squad agent that can run the task and has not tried it yet; never for a direct assignment."""
        if goal["planner"] == "direct":
            return None
        tried = self._tried_agents(t["id"]) | {t["assigned_agent"]}
        return self.planner.pick([a for a in self._roster(goal) if a["id"] not in tried], t["skill"], self)

    def _reassign(self, goal: dict, t: dict, alt: dict, why: str) -> None:
        old = t["assigned_agent"]
        self.db.update_task(t["id"], assigned_agent=alt["id"])
        self.db.reroute_handoffs(t["id"], alt["id"])  # results handed to the old agent go to the new one
        self.db.post(workspace_id=t["workspace_id"], kind="agent", event="task.reassigned", sender=goal["assigned_agent"], task_id=t["id"],
                     text=f"{why}；主管把 {t['title']} 改派给 {alt['name']}", data={"from": old, "to": alt["id"]})

    def _ensure_available(self, goal: dict, t: dict) -> dict | None:
        """Before handing out a task: is its agent still on duty, in the squad and able to run it? If not, reassign or fail it."""
        a = self.db.agent(t["assigned_agent"]) if t["assigned_agent"] else None
        if a is None:
            reason = "已被删除"
        elif a["status"] != D.AGENT_ACTIVE:
            reason = f"不在岗（{a['status']}）"
        else:
            reason = self.can_run(a, t["skill"]) or self.over_budget(a)
            if reason is None and goal["planner"] != "direct" and goal.get("squad_id") and a["id"] not in {x["id"] for x in self._roster(goal)}:
                reason = "已不在这个 Squad 里"
            if reason and reason.startswith(a["name"]):
                reason = reason[len(a["name"]):].strip()
        if reason is None:
            return t
        who = a["name"] if a else (t["assigned_agent"] or "—")
        alt = self._pick_other(goal, t)
        if alt:
            self._reassign(goal, t, alt, f"{who} {reason}")
            return self.db.task(t["id"])
        skill = SKILL_BY_ID.get(t["skill"])
        error = f"{who} {reason}，Squad 里没有其他能做{skill.name if skill else t['skill']}的在岗员工"
        if self.db.transition(t["id"], (D.PENDING,), D.FAILED, error=error[:500]):
            self._announce_failure(self.db.task(t["id"]), error)
        return None

    def _redispatch(self) -> int:
        """Tasks held back by a concurrency cap or a paused squad go out as soon as there is room."""
        n = 0
        for g in self.db.open_goals():
            if g["status"] == D.WAITING and any(x["status"] == D.PENDING for x in self.db.subtasks(g["id"])):
                n += self._dispatch(g["id"])
        return n

    def _charge(self, task_id: str, agent_id: str, usage: dict | None) -> None:
        if not usage:
            return
        cost = str(usage.get("cost_usd", "0"))
        tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        self.db.add_agent_usage(agent_id, cost, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        t = self.db.bump_task(task_id, cost_usd=cost, tokens=tokens)
        if t["root_id"] != task_id:
            self.db.bump_task(t["root_id"], cost_usd=cost, tokens=tokens)

    # =====================================================================
    # approvals
    # =====================================================================

    def decide(self, approval_id: int, approve: bool, by: str, note: str = "", anchor: bool = False) -> dict:
        a = self.db.approval(approval_id)
        if a is None:
            raise WorkforceError(f"没有审批 #{approval_id}")
        if a["status"] != "pending":
            raise WorkforceError(f"审批 #{approval_id} 已经处理过（{a['status']}）")
        goal = self.db.task(a["task_id"]) if a["task_id"] else None
        if anchor and not approve:
            raise WorkforceError("驳回时不能上链")
        if anchor and a["kind"] == "task_result" and self.anchor is None:
            raise WorkforceError("没有配置测试网钱包，不能把结果哈希写上链（见 CHAINSTAFF_ANCHOR_WALLET）")
        if anchor and goal:
            self._check_anchor_allowed(goal)
        if not self.db.decide_approval(approval_id, "approved" if approve else "rejected", by, note):
            raise WorkforceError(f"审批 #{approval_id} 刚被处理")
        wid = a["workspace_id"]
        verdict = "通过" if approve else "驳回"
        self.db.post(workspace_id=wid, kind="system", event=f"approval.{'approved' if approve else 'rejected'}", task_id=a["task_id"],
                     sender="user", text=f"{by} {verdict}审批 #{approval_id}" + (f"：{note}" if note else ""), data={"approval": approval_id})
        self._audit(f"{verdict}审批 #{approval_id}（{a['kind']}）", by, {"approval": approval_id, "task": a["task_id"], "anchor": anchor})
        if a["kind"] == "task_result" and goal:
            if approve:
                self.db.transition(goal["id"], (D.REVIEW,), D.COMPLETED)
                self.db.post(workspace_id=wid, kind="task", event="task.completed", task_id=goal["id"], text=f"任务 {goal['id']} 已完成（链下）",
                             data={"result_hash": goal["result_hash"], "offchain": True})
                if anchor:
                    self.anchor_task(goal["id"], by)
            else:
                self._end_goal(goal, D.FAILED, f"审批驳回：{note or by}")
        elif a["kind"] == "over_budget" and goal and goal["status"] not in D.FINAL:
            if approve:
                self.db.update_task(goal["id"], budget_usd=str(Decimal(goal["budget_usd"]) * 2))
                self._dispatch(goal["id"])
            else:
                self._end_goal(goal, D.FAILED, "超出预算，审批驳回")
        return self.db.approval(approval_id)

    def anchor_task(self, tid: str, by: str) -> dict:
        goal = self.task_or_404(tid)
        if goal["status"] != D.COMPLETED or goal["kind"] != "goal":
            raise WorkforceError("只有审批通过的任务才能上链")
        if self.anchor is None:
            raise WorkforceError("没有配置测试网钱包，不能写上链")
        self._check_anchor_allowed(goal)
        existing = [r for r in self.db.chain_records(task_id=tid) if r["status"] in ("pending", "confirmed")]
        if existing:
            raise WorkforceError(f"这个任务的哈希已经上链或正在确认：{existing[0]['tx_hash']}")
        rec = self.anchor.anchor(self.db, goal)
        self.db.post(workspace_id=goal["workspace_id"], kind="chain", event="chain.submitted", task_id=tid, sender="system",
                     text=f"结果哈希已广播到 {rec['network']}，等待确认" if rec["status"] == "pending" else f"上链失败：{rec['error']}",
                     data=rec)
        self._audit(f"任务 {tid} 结果哈希上链：{rec.get('tx_hash') or rec.get('error')}", by, {"task": tid, "record": rec["id"]})
        return rec

    def _check_anchor_allowed(self, goal: dict) -> None:
        sq = self.db.squad(goal["squad_id"]) if goal.get("squad_id") else None
        if sq and "anchor_results" not in sq["permissions"]:
            raise WorkforceError(f"Squad {sq['name']} 没有上链权限（anchor_results），这个结果只能链下完成")

    def _poll_anchors(self) -> int:
        if self.anchor is None:
            return 0
        n = 0
        for rec in self.db.chain_records(status="pending"):
            done = self.anchor.poll(self.db, rec)
            if done:
                n += 1
                t = self.db.task(rec["task_id"]) if rec["task_id"] else None
                self.db.post(workspace_id=t["workspace_id"] if t else "", kind="chain", event=f"chain.{done['status']}", task_id=rec["task_id"],
                             text=(f"链上确认：区块 {done['block']}" if done["status"] == "confirmed" else f"链上交易失败：{done.get('error')}"),
                             data=done)
        return n

    # =====================================================================
    # schedules
    # =====================================================================

    def add_schedule(self, body: dict, by: str) -> dict:
        wid = str(body.get("workspace_id", ""))
        self.workspace_or_404(wid)
        cron = str(body.get("cron", "")).strip()
        try:
            nxt = Cron.parse(cron).next_after(_now())
        except ValueError as e:
            raise WorkforceError(str(e)) from None
        goal = str(body.get("goal", "")).strip()
        if not goal:
            raise WorkforceError("定时任务内容不能为空")
        s = self.db.add_schedule(wid, str(body.get("name") or goal[:40])[:60], cron, goal[:4000], nxt)
        self._audit(f"新建定时任务 {s['name']}（{cron}）", by, {"schedule": s["id"]})
        return s

    def set_schedule_enabled(self, sid: int, enabled: bool, by: str) -> dict:
        s = self.db.schedule(sid)
        if s is None:
            raise WorkforceError(f"没有定时任务 #{sid}")
        f: dict[str, Any] = {"enabled": int(enabled)}
        if enabled:
            f["next_run_at"] = Cron.parse(s["cron"]).next_after(_now())
            f["failures"] = 0
        self.db.update_schedule(sid, **f)
        self._audit(f"{'恢复' if enabled else '暂停'}定时任务 {s['name']}", by, {"schedule": sid})
        return self.db.schedule(sid)

    def run_schedule_now(self, sid: int, by: str) -> dict:
        s = self.db.schedule(sid)
        if s is None:
            raise WorkforceError(f"没有定时任务 #{sid}")
        return self._run_schedule(s, by)

    def _run_schedule(self, s: dict, by: str) -> dict:
        try:
            goal = self.submit_goal(s["workspace_id"], s["goal"], by)
        except WorkforceError as e:
            failures = s["failures"] + 1
            # Three failures in a row pause the schedule instead of failing forever.
            self.db.update_schedule(s["id"], last_run_at=_now(), last_status="failed", failures=failures, enabled=0 if failures >= 3 else s["enabled"])
            self.db.add_schedule_run(s["id"], None, "failed", str(e))
            self.db.post(workspace_id=s["workspace_id"], kind="system", event="schedule.failed",
                         text=f"定时任务 {s['name']} 没能启动：{e}" + ("；连续 3 次失败，已暂停" if failures >= 3 else ""))
            return {"ok": False, "error": str(e)}
        self.db.update_schedule(s["id"], last_run_at=_now(), last_task_id=goal["id"], last_status="started", failures=0)
        self.db.add_schedule_run(s["id"], goal["id"], "started")
        return {"ok": True, "task": goal}

    def _fire_schedules(self) -> int:
        n = 0
        now = _now()
        for s in self.db.due_schedules(now):
            self.db.update_schedule(s["id"], next_run_at=Cron.parse(s["cron"]).next_after(now))
            self._run_schedule(s, f"schedule#{s['id']}")
            n += 1
        return n

    # =====================================================================
    # skills
    # =====================================================================

    def _seed_skills(self) -> None:
        states = self.db.skill_states()
        for s in REGISTRY.all():
            if s.id not in states:
                self.db.set_skill_state(s.id, True, True, s.version)

    def skill_state(self, sid: str) -> dict:
        st = self.db.skill_states().get(sid)
        if st is None and sid in SKILL_BY_ID:  # a tool registered after start: installed and enabled, like the built-ins
            self.db.set_skill_state(sid, True, True, SKILL_BY_ID[sid].version)
            st = self.db.skill_states().get(sid)
        return {"installed": bool(st and st["installed"]), "enabled": bool(st and st["enabled"]), "version": st["version"] if st else None}

    def skills_view(self) -> list[dict]:
        states = self.db.skill_states()
        agents = self.db.agents(include_archived=False)
        out = []
        for s in REGISTRY.all():
            st = states.get(s.id) or {}
            missing = [e for e in s.requires if not self._requirement_met(e)]
            out.append({**s.public(), "installed": bool(st.get("installed")), "enabled": bool(st.get("enabled")),
                        "installed_version": st.get("version"), "update_available": bool(st.get("installed")) and st.get("version") != s.version,
                        "missing": missing, "needs_chain": "chain:read" in s.permissions and self.reader is None,
                        "agents": [a["id"] for a in agents if s.id in a["skills"]]})
        return out

    def _requirement_met(self, env: str) -> bool:
        return self.etherscan is not None if env == "ETHERSCAN_API_KEY" else bool(os.environ.get(env))

    def skill_action(self, sid: str, action: str, by: str) -> dict:
        s = SKILL_BY_ID.get(sid)
        if s is None:
            raise WorkforceError(f"没有技能 {sid}")
        st = self.skill_state(sid)
        if action == "install":
            self.db.set_skill_state(sid, True, True, s.version)
        elif action == "uninstall":
            self.db.set_skill_state(sid, False, False, st["version"] or s.version)
        elif action == "enable":
            if not st["installed"]:
                raise WorkforceError("先安装再启用")
            self.db.set_skill_state(sid, True, True, st["version"])
        elif action == "disable":
            self.db.set_skill_state(sid, st["installed"], False, st["version"] or s.version)
        elif action == "update":
            if not st["installed"]:
                raise WorkforceError("先安装再更新")
            self.db.set_skill_state(sid, True, st["enabled"], s.version)
        else:
            raise WorkforceError("操作只能是 install / uninstall / enable / disable / update")
        self._audit(f"技能 {s.name}：{action}", by, {"skill": sid})
        return next(x for x in self.skills_view() if x["id"] == sid)

    def run_skill(self, sid: str, inp: dict, by: str) -> dict:
        """Run a skill directly from the console (a human, not an agent), e.g. to try it out."""
        s = SKILL_BY_ID.get(sid)
        if s is None:
            raise WorkforceError(f"没有技能 {sid}")
        if not self.skill_state(sid)["enabled"]:
            raise WorkforceError(f"技能 {s.name} 没有启用")
        if sid == AGENT_TASK:
            raise WorkforceError("自主执行要交给一个员工（它用自己的模型和权限），不能在技能页直接运行")
        ctx = SkillContext(self.reader, self.etherscan, self.tokens, deadline=time.monotonic() + s.timeout_s, events=self.events)
        try:
            validate(s.input_schema, inp)
            out = s.run(ctx, dict(inp))
        except SkillError as e:
            raise WorkforceError(str(e)) from None
        self.db.post(workspace_id="", kind="tool", event="tool.manual", sender="user", text=f"{by} 手动运行 {s.name}", data={"skill": sid, "input": inp, "logs": ctx.logs})
        return {"output": out, "logs": ctx.logs}

    # =====================================================================
    # memory
    # =====================================================================

    def memory_scopes(self, agent_id: str | None = None, workspace_id: str | None = None) -> list[tuple[str, str]]:
        """What an agent (or the owner, with neither argument) is allowed to read."""
        if agent_id:
            ws = [w["id"] for w in self.db.workspaces() if agent_id in w["members"] + [w["manager_id"]]]
            return [("agent", agent_id), ("project", ""), ("user", "")] + [("workspace", w) for w in ws]
        if workspace_id:
            return [("workspace", workspace_id), ("project", ""), ("user", "")]
        scopes = [("project", ""), ("user", "")] + [("workspace", w["id"]) for w in self.db.workspaces()]
        return scopes + [("agent", a["id"]) for a in self.db.agents()]

    def add_memory(self, body: dict, by: str) -> dict:
        scope = body.get("scope")
        if scope not in ("agent", "workspace", "project", "user"):
            raise WorkforceError("记忆范围只能是 agent / workspace / project / user")
        owner = str(body.get("owner") or "") if scope in ("agent", "workspace") else ""
        if scope == "agent":
            self._agent_or_404(owner)
        if scope == "workspace":
            self.workspace_or_404(owner)
        text = str(body.get("text", "")).strip()
        if not text:
            raise WorkforceError("内容不能为空")
        ttl = body.get("ttl_days")
        mid = self.db.remember(scope, owner, text, source=f"manual:{by}", ttl_days=int(ttl) if ttl else None)
        return self.db.memory(mid)

    # =====================================================================
    # views
    # =====================================================================

    def agent_views(self, since: datetime | None = None) -> list[dict]:
        stats = self.db.agent_task_stats(since)
        last = self.db.last_activity()
        open_rows, _ = self.db.tasks(status=[D.ASSIGNED, D.RUNNING], kind="subtask", limit=500)
        current = {}
        for t in open_rows:
            current.setdefault(t["assigned_agent"], t)
        managing, _ = self.db.tasks(status=[D.RUNNING, D.WAITING, D.REVIEW], kind="goal", limit=500)
        for g in managing:
            current.setdefault(g["assigned_agent"], g)
        waiting_rows, _ = self.db.tasks(status=[D.PENDING], kind="subtask", limit=1000)
        open_roots = {g["id"] for g in self.db.open_goals()}
        waiting = {}
        for t in waiting_rows:
            if t["root_id"] in open_roots:
                waiting.setdefault(t["assigned_agent"], t)
        squads_of: dict[str, list[dict]] = {}
        for q in self.db.squads():
            for x in [q["manager_id"], *q["members"]]:
                if x:
                    squads_of.setdefault(x, []).append({"id": q["id"], "name": q["name"], "workspace_id": q["workspace_id"],
                                                        "role": "manager" if x == q["manager_id"] else "member", "status": q["status"]})
        out = []
        for a in self.db.agents():
            s = stats.get(a["id"], {"by_status": {}, "secs": [], "cost": 0.0})
            done = s["by_status"].get(D.COMPLETED, 0)
            failed = s["by_status"].get(D.FAILED, 0)
            cur = current.get(a["id"])
            if a["status"] != D.AGENT_ACTIVE:
                live = a["status"]
            elif cur:
                # a mission parked in review is waiting on the owner, not on this agent
                live = "review" if cur["status"] == D.REVIEW else "busy"
            elif waiting.get(a["id"]):
                live, cur = "waiting", waiting[a["id"]]  # has work queued behind a dependency or a concurrency cap
            elif failed and self.db.tasks(agent=a["id"], status=[D.FAILED], since=_now() - timedelta(hours=24), limit=1)[1]:
                live = "error"
            else:
                live = "idle"
            out.append({**a, "live": live, "last_activity_at": last.get(a["id"]), "current_task": {"id": cur["id"], "title": cur["title"], "status": cur["status"]} if cur else None,
                        "tasks_completed": done, "tasks_failed": failed, "squads": squads_of.get(a["id"], []),
                        "running": self.db.running_subtasks(agent=a["id"]),
                        "success_rate": round(done / (done + failed), 4) if done + failed else None,
                        "avg_secs": round(sum(s["secs"]) / len(s["secs"]), 2) if s["secs"] else None})
        return out

    def overview(self, since: datetime | None = None) -> dict:
        agents = self.agent_views(since)
        counts = self.db.task_counts(since)
        goals_cost = self.db._db.execute(
            "SELECT COALESCE(SUM(CAST(cost_usd AS REAL)), 0) AS c, COALESCE(SUM(tokens), 0) AS t FROM wf_tasks WHERE kind = 'goal'"
            + (" AND created_at >= ?" if since else ""), (_iso(since),) if since else ()).fetchone()
        series = self.db._db.execute(
            "SELECT substr(created_at, 1, 13) AS h, status, COUNT(*) AS n FROM wf_tasks WHERE kind = 'subtask'"
            + (" AND created_at >= ?" if since else "") + " GROUP BY h, status ORDER BY h", (_iso(since),) if since else ()).fetchall()
        return {
            "agents": {"total": len([a for a in agents if a["status"] != D.AGENT_ARCHIVED]), "active": sum(a["status"] == D.AGENT_ACTIVE for a in agents),
                       "busy": sum(a["live"] == "busy" for a in agents), "idle": sum(a["live"] == "idle" for a in agents),
                       "waiting": sum(a["live"] == "waiting" for a in agents),
                       "error": sum(a["live"] == "error" for a in agents), "paused": sum(a["status"] == D.AGENT_PAUSED for a in agents)},
            "tasks": {s: counts.get(s, 0) for s in D.TASK_STATUSES},
            "running": counts.get(D.RUNNING, 0) + counts.get(D.ASSIGNED, 0) + counts.get(D.WAITING, 0),
            "pending_approvals": len(self.db.approvals(status="pending")),
            "squads": len(self.db.squads()),
            "running_missions": len([g for g in self.db.open_goals() if g["status"] != D.REVIEW]),
            "cost_usd": round(goals_cost["c"], 6), "tokens": int(goals_cost["t"]),
            "series": [{"hour": r["h"], "status": r["status"], "n": r["n"]} for r in series],
            "performance": sorted(agents, key=lambda a: -(a["tasks_completed"] or 0))[:10],
            "engine": {"llm": self.llm is not None, "compat": self.compat.model if self.compat else None,
                       "compat_service": self.compat.label if self.compat else None, "chain": self.reader is not None, "etherscan": self.etherscan is not None,
                       "anchor": self.anchor is not None, "planner": self.planner.label()},
        }

    # ---- helpers ----

    def _name(self, aid: str | None) -> str:
        if not aid:
            return "—"
        a = self.db.agent(aid)
        return a["name"] if a else aid

    @staticmethod
    def _brief(out: dict) -> str:
        if "report_md" in out:
            return f"报告 {out.get('sha256', '')[:12]}，{len(out.get('findings', []))} 个风险点"
        keys = [k for k in ("symbol", "bnb", "nonce", "count", "code_size", "risks") if k in out]
        return "，".join(f"{k}={out[k] if not isinstance(out[k], list) else len(out[k])}" for k in keys)[:300]

    def _audit(self, text: str, by: str, data: dict) -> None:
        self.store.log_event("audit", text, by, data, employee=AUDIT)
