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
from .planner import Planner, PlanError
from .skills import PERMISSIONS, SKILL_BY_ID, SKILLS, SkillContext, SkillError, validate

EXTRA_PERMISSIONS = {"message:peers": "可以直接给同工作区的其他员工发消息"}
ALL_PERMISSIONS = {**PERMISSIONS, **EXTRA_PERMISSIONS}
PROVIDERS = ("none", "anthropic")
AUDIT = "workforce"


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
                 price_per_mtok: tuple[Decimal, Decimal] | None = None) -> None:
        self.store = store
        self.db = WorkforceDB(store)
        self.reader = reader
        self.etherscan = etherscan
        self.tokens = tokens or {}
        self.planner = Planner(llm, price_per_mtok)
        self.llm = llm
        self.anchor = anchor
        self.msg_timeout = timedelta(seconds=msg_timeout_s)
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wf-agent")
        self._inflight: dict[int, Future] = {}
        self._tick_lock = threading.Lock()
        self._goal_locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seed_skills()

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
            f["model_config"] = {k: mc[k] for k in ("max_tokens", "effort") if k in mc}
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
        if "onchain" in body:
            oc = body["onchain"] or {}
            f["onchain"] = {k: str(oc[k])[:80] for k in ("agent_id", "address", "network", "tx") if oc.get(k)}
        return f

    def create_agent(self, body: dict, by: str) -> dict:
        f = self._clean_agent(body)
        if f.get("provider") == "anthropic" and not f.get("model"):
            f["model"] = self.planner.model
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
        keep = ("role", "description", "system_prompt", "provider", "model", "model_config", "skills", "permissions", "is_manager", "budget_usd")
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
        for ws in self.db.workspaces():
            if aid in ws["members"] or ws["manager_id"] == aid:
                self.db.update_workspace(ws["id"], members=[m for m in ws["members"] if m != aid],
                                         manager_id=None if ws["manager_id"] == aid else ws["manager_id"])
        self._audit(f"删除员工 {a['name']}（{aid}）", by, {"agent": aid})

    def _agent_or_404(self, aid: str) -> dict:
        a = self.db.agent(aid)
        if a is None:
            raise WorkforceError(f"没有员工 {aid}")
        return a

    def _open_tasks_of(self, aid: str) -> int:
        return self.db.tasks(agent=aid, status=[D.PENDING, D.ASSIGNED, D.RUNNING, D.WAITING], limit=1)[1]

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
        self.db.post(workspace_id=ws["id"], kind="system", event="workspace.created", text=f"工作区 {name} 已建立", data={"members": members, "manager": manager})
        self._audit(f"建立工作区 {name}", by, {"workspace": ws["id"]})
        return ws

    def update_workspace(self, wid: str, body: dict, by: str) -> dict:
        ws = self.workspace_or_404(wid)
        f: dict[str, Any] = {}
        if "name" in body:
            f["name"] = str(body["name"]).strip()[:60] or ws["name"]
        if "description" in body:
            f["description"] = str(body["description"])[:500]
        if "manager_id" in body or "members" in body:
            f["manager_id"], f["members"] = self._check_members(body.get("manager_id", ws["manager_id"]), body.get("members", ws["members"]))
        if "archived" in body:
            f["archived"] = int(bool(body["archived"]))
        ws = self.db.update_workspace(wid, **f)
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

    # =====================================================================
    # goals: submit, cancel, retry
    # =====================================================================

    def submit_goal(self, wid: str, text: str, by: str, *, priority: int = 2, max_steps: int = 20,
                    budget_usd: Any = "1", timeout_s: int = 600) -> dict:
        ws = self.workspace_or_404(wid)
        text = text.strip()
        if not text:
            raise WorkforceError("任务内容不能为空")
        if not ws["manager_id"]:
            raise WorkforceError("这个工作区还没有 AI 主管，先指定一个主管")
        manager = self.db.agent(ws["manager_id"])
        if manager["status"] != D.AGENT_ACTIVE:
            raise WorkforceError(f"主管 {manager['name']} 不在岗（{manager['status']}）")
        if not (0 <= int(priority) <= 3):
            raise WorkforceError("优先级只能是 0–3")
        if not (1 <= int(max_steps) <= 100):
            raise WorkforceError("最大步数只能是 1–100")
        if not (10 <= int(timeout_s) <= 86400):
            raise WorkforceError("超时只能是 10 秒到 24 小时")
        budget = _dec(budget_usd, "预算")
        deadline = _now() + timedelta(seconds=int(timeout_s))
        goal = self.db.add_task(workspace_id=wid, kind="goal", title=text.splitlines()[0][:120], description=text[:4000],
                                assigned_agent=manager["id"], priority=int(priority), status=D.PENDING, max_steps=int(max_steps),
                                budget_usd=str(budget), timeout_s=int(timeout_s), deadline_at=_iso(deadline), created_by=by)
        self.db.post(workspace_id=wid, kind="user", event="user.instruction", sender="user", text=text, task_id=goal["id"], data={"by": by})
        self.db.post(workspace_id=wid, kind="task", event="task.created", task_id=goal["id"], text=f"任务 {goal['id']} 已创建，交给主管 {manager['name']}",
                     data={"priority": goal["priority"], "max_steps": goal["max_steps"], "budget_usd": goal["budget_usd"], "deadline": goal["deadline_at"]})
        self.db.post(workspace_id=wid, kind="agent", event="goal.submitted", sender="user", recipient=manager["id"], task_id=goal["id"],
                     text=text, dedupe_key=f"goal:{goal['id']}")
        self._audit(f"提交任务 {goal['id']}：{goal['title']}", by, {"task": goal["id"]})
        return goal

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
                                planner="direct", started_at=_iso(_now()), created_by=by)
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

    def _end_goal(self, root: dict, status: str, reason: str) -> None:
        for s in self.db.subtasks(root["id"]):
            if s["status"] not in D.FINAL:
                self.db.transition(s["id"], (s["status"],), D.CANCELLED, error=reason[:500])
        for m in self.db.messages(root_id=root["id"], limit=1000):
            if m["recipient"] and m["status"] in (D.QUEUED, D.DELIVERED) and m["id"] not in self._inflight:
                self.db.set_message_status(m["id"], (D.QUEUED, D.DELIVERED), D.EXPIRED)
        self.db.transition(root["id"], (D.PENDING, D.RUNNING, D.WAITING, D.REVIEW), status, error=reason[:500])
        self.db.post(workspace_id=root["workspace_id"], kind="task", event=f"task.{status}", task_id=root["id"],
                     text=f"任务 {root['id']} {dict(failed='失败', cancelled='已取消')[status]}：{reason}")

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
            if not (snd["is_manager"] or rec["is_manager"] or "message:peers" in snd["permissions"]):
                raise WorkforceError(f"{snd['name']} 没有给同事直接发消息的权限（message:peers）")
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
                self._stop.wait(interval_s)

        self._thread = threading.Thread(target=loop, name="wf-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.pool.shutdown(wait=True)

    def recover(self) -> int:
        """After a restart: in-flight work from the previous process is put back in the queue."""
        n = 0
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

    def tick(self) -> int:
        with self._tick_lock:
            done = [mid for mid, f in self._inflight.items() if f.done()]
            for mid in done:
                self._inflight.pop(mid)
            n = self._fire_schedules()
            n += self._enforce_deadlines()
            n += self._expire_messages()
            n += self._deliver()
            n += self._poll_anchors()
            return n

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
                    self._plan(a, m)
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
        goal = self.db.task(m["task_id"])
        self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
        if goal is None or not self.db.transition(goal["id"], (D.PENDING,), D.RUNNING):
            return
        ws = self.db.workspace(goal["workspace_id"])
        members = [a for a in (self.db.agent(x) for x in ws["members"]) if a and a["status"] == D.AGENT_ACTIVE]
        wid = goal["workspace_id"]
        self.db.post(workspace_id=wid, kind="agent", event="agent.started", sender=manager["id"], task_id=goal["id"],
                     text="收到任务，正在拆分工作流")
        try:
            plan = self.planner.plan(goal["description"], manager, members, self)
        except PlanError as e:
            self.db.bump_task(goal["id"], steps=1)
            self._end_goal(self.db.task(goal["id"]), D.FAILED, str(e))
            return
        self._charge(goal["id"], manager["id"], plan.usage)
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
        self._dispatch(goal["id"])

    def _dispatch(self, root_id: str) -> int:
        with self._goal_lock(root_id):
            goal = self.db.task(root_id)
            if goal is None or goal["status"] != D.WAITING:
                return 0
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
        with self._goal_lock(root["id"]):
            # Dynamic delegation: once the manager knows what kind of address it is, it adds the right follow-ups.
            if t["status"] == D.COMPLETED and t["skill"] == "wallet_analysis" and root["planner"] == "rules":
                self._expand(root, t)
            self._dispatch(root["id"])

    def _expand(self, root: dict, done: dict) -> None:
        addr = done["input"]["address"]
        follow = ["contract_analysis", "token_info", "contract_events"] if done["result"].get("is_contract") else ["tx_history"]
        if done["result"].get("is_contract"):
            follow.append("tx_history")
        ws = self.db.workspace(root["workspace_id"])
        members = [a for a in (self.db.agent(x) for x in ws["members"]) if a and a["status"] == D.AGENT_ACTIVE]
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
                         text=text, data={"added": [t["id"] for t in added]})

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
            self._fail_attempt(self.db.task(t["id"]), f"权限拒绝：{reason}", transient=False)
            return
        if not self.db.transition(t["id"], (D.ASSIGNED,), D.RUNNING):
            return
        skill = SKILL_BY_ID[t["skill"]]
        handoffs = [h for h in self.db.inbox(agent_id, limit=200) if h["event"] == "handoff" and (h["data"] or {}).get("task") in t["depends_on"]]
        upstream = [h["data"] for h in handoffs]
        self.db.post(workspace_id=wid, kind="agent", event="agent.working", sender=agent_id, task_id=t["id"],
                     text=f"开始：{t['title']}" + (f"（收到 {len(upstream)} 份同事交接的结果）" if upstream else ""))
        self.db.post(workspace_id=wid, kind="tool", event="tool.start", sender=agent_id, task_id=t["id"], text=f"{skill.id} v{skill.version}",
                     data={"skill": skill.id, "input": t["input"]})
        ctx = SkillContext(self.reader, self.etherscan, self.tokens, upstream)
        started = time.monotonic()
        try:
            validate(skill.input_schema, t["input"])
            out = skill.run(ctx, dict(t["input"]))
            if t["skill"] == "data_report" and agent["provider"] == "anthropic" and self.llm:
                summary, usage = self.planner.summarize(agent, out["report_md"])
                self._charge(t["id"], agent_id, usage)
                out["report_md"] += "\n\n## 摘要（模型生成）\n\n" + summary
                out["sha256"] = "0x" + hashlib.sha256(out["report_md"].encode()).hexdigest()
                ctx.log(f"模型摘要 {usage['input_tokens']}+{usage['output_tokens']} tokens")
        except SkillError as e:
            self._tool_done(t, agent_id, ctx, started, error=str(e))
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            self._fail_attempt(self.db.task(t["id"]), str(e), transient=False)
            return
        except Exception as e:
            self._tool_done(t, agent_id, ctx, started, error=f"{type(e).__name__}: {e}"[:300])
            self.db.set_message_status(m["id"], (D.DELIVERED,), D.PROCESSED)
            self._fail_attempt(self.db.task(t["id"]), f"{type(e).__name__}: {e}"[:300], transient=True)
            return
        self._tool_done(t, agent_id, ctx, started, output=out)
        for h in handoffs:
            self.db.set_message_status(h["id"], (D.QUEUED, D.DELIVERED), D.PROCESSED)
        h = out.get("sha256") if t["skill"] == "data_report" else result_hash(out)
        self.db.transition(t["id"], (D.RUNNING,), D.COMPLETED, result=out, result_hash=h, error=None)
        self.db.post(workspace_id=wid, kind="task", event="task.completed", sender=agent_id, task_id=t["id"],
                     text=f"{t['title']} 完成", data={"result_hash": h, "secs": round(time.monotonic() - started, 2)})
        self.db.remember("agent", agent_id, f"[{t['id']}] {t['title']}：{self._brief(out)}", source=t["id"], ttl_days=90)
        if t["skill"] == "data_report":
            self.db.remember("workspace", wid, f"[{t['root_id']}] {out['title']}：" + ("；".join(out["findings"]) or "无风险点"), source=t["id"])
        self._handoff(t, agent_id, out)
        self._report_to_manager(t, agent_id, ok=True)

    def _tool_done(self, t: dict, agent_id: str, ctx: SkillContext, started: float, output: dict | None = None, error: str | None = None) -> None:
        secs = round(time.monotonic() - started, 3)
        calls = ctx.reader.calls if ctx.reader else 0
        self.db.post(workspace_id=t["workspace_id"], kind="tool", event="tool.error" if error else "tool.done", sender=agent_id, task_id=t["id"],
                     text=(f"失败：{error}" if error else f"完成，用时 {secs}s"),
                     data={"skill": t["skill"], "secs": secs, "logs": ctx.logs, "error": error, "rpc_calls_total": calls,
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

    def _fail_attempt(self, t: dict, error: str, transient: bool) -> None:
        wid = t["workspace_id"]
        if transient and t["attempts"] < t["max_attempts"]:
            if self.db.transition(t["id"], (D.RUNNING, D.ASSIGNED), D.PENDING, error=error[:500]):
                self.db.post(workspace_id=wid, kind="task", event="task.retry", task_id=t["id"],
                             text=f"{t['title']} 第 {t['attempts']} 次失败，稍后重试：{error}"[:500])
                self._dispatch(t["root_id"])
            return
        if not self.db.transition(t["id"], (D.RUNNING, D.ASSIGNED), D.FAILED, error=error[:500]):
            return
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
        existing = [r for r in self.db.chain_records(task_id=tid) if r["status"] in ("pending", "confirmed")]
        if existing:
            raise WorkforceError(f"这个任务的哈希已经上链或正在确认：{existing[0]['tx_hash']}")
        rec = self.anchor.anchor(self.db, goal)
        self.db.post(workspace_id=goal["workspace_id"], kind="chain", event="chain.submitted", task_id=tid, sender="system",
                     text=f"结果哈希已广播到 {rec['network']}，等待确认" if rec["status"] == "pending" else f"上链失败：{rec['error']}",
                     data=rec)
        self._audit(f"任务 {tid} 结果哈希上链：{rec.get('tx_hash') or rec.get('error')}", by, {"task": tid, "record": rec["id"]})
        return rec

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
        for s in SKILLS:
            if s.id not in states:
                self.db.set_skill_state(s.id, True, True, s.version)

    def skill_state(self, sid: str) -> dict:
        st = self.db.skill_states().get(sid)
        return {"installed": bool(st and st["installed"]), "enabled": bool(st and st["enabled"]), "version": st["version"] if st else None}

    def skills_view(self) -> list[dict]:
        states = self.db.skill_states()
        agents = self.db.agents(include_archived=False)
        out = []
        for s in SKILLS:
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
        ctx = SkillContext(self.reader, self.etherscan, self.tokens)
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
            elif failed and self.db.tasks(agent=a["id"], status=[D.FAILED], since=_now() - timedelta(hours=24), limit=1)[1]:
                live = "error"
            else:
                live = "idle"
            out.append({**a, "live": live, "last_activity_at": last.get(a["id"]), "current_task": {"id": cur["id"], "title": cur["title"], "status": cur["status"]} if cur else None,
                        "tasks_completed": done, "tasks_failed": failed,
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
                       "error": sum(a["live"] == "error" for a in agents), "paused": sum(a["status"] == D.AGENT_PAUSED for a in agents)},
            "tasks": {s: counts.get(s, 0) for s in D.TASK_STATUSES},
            "running": counts.get(D.RUNNING, 0) + counts.get(D.ASSIGNED, 0) + counts.get(D.WAITING, 0),
            "pending_approvals": len(self.db.approvals(status="pending")),
            "cost_usd": round(goals_cost["c"], 6), "tokens": int(goals_cost["t"]),
            "series": [{"hour": r["h"], "status": r["status"], "n": r["n"]} for r in series],
            "performance": sorted(agents, key=lambda a: -(a["tasks_completed"] or 0))[:10],
            "engine": {"llm": self.llm is not None, "chain": self.reader is not None, "etherscan": self.etherscan is not None,
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
