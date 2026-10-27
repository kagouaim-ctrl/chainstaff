"""Wake-up engine: every piece of work becomes a structured task in the persistent queue, and every task wakes
exactly one agent (or waits for a human when none fits).

    event ─▶ trigger rule ─▶ task (queued) ─▶ worker claims it ─▶ agent run ─▶ complete / delegate / reject
                                                                     │
                                         delegate ─▶ child task ─▶ another agent wakes up
                                         child final ─▶ parent re-queued with the child's result (the parent wakes again)

Guards on every task: maximum delegation depth, circular delegation, deduplication of open work, a per-agent
delegation rate, a cap on fix attempts per issue, retry limit, deadline, mission and task budget. A guard that
stops a hand-off says so in the activity log and to the delegating agent; nothing is dropped silently, and no
agent is shown as having taken work it was never given.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from . import constants as C
from .activity import Activity
from .agents import has_workspace, holds, match
from .db import AutonomyDB, iso, now_dt


class TaskError(ValueError):
    """A task that cannot be created or changed; the message goes to the user or to the delegating agent."""


class DelegationBlocked(TaskError):
    def __init__(self, guard: str, message: str) -> None:
        super().__init__(message)
        self.guard = guard


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())[:300]


def dedupe_key(workspace_id: str, kind: str, to: str | None, objective: str, anchor: str | None) -> str:
    raw = "|".join([workspace_id, kind, to or "*", anchor or _norm(objective)])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Wakeup:
    def __init__(self, db: AutonomyDB, activity: Activity, limits: C.Limits) -> None:
        self.db = db
        self.act = activity
        self.limits = limits

    # ------------------------------------------------------------------ create

    def create_task(self, *, workspace_id: str, objective: str, kind: str = "general", priority: int = 2, evidence: Any = None,
                    to_agent_id: str | None = None, from_agent_id: str | None = None, mission_id: str | None = None,
                    parent: dict | None = None, event_id: str | None = None, fingerprint: str | None = None,
                    required: list[str] | None = None, follow_up: bool = False, deadline_s: int | None = None,
                    budget_usd: Any = None, created_by: str = "system", reason: str = "", is_result: bool = False) -> tuple[dict, dict]:
        """Create a task and pick who does it. Returns (task, info): info says whether it was deduplicated and which
        agents were considered. Raises DelegationBlocked when a guard stops it."""
        objective = str(objective or "").strip()
        if not objective:
            raise TaskError("任务目标不能为空")
        if kind not in C.TASK_KINDS:
            raise TaskError(f"任务类型只能是 {', '.join(C.TASK_KINDS)}")
        try:
            priority = max(0, min(3, int(priority)))
        except (TypeError, ValueError):
            priority = 2
        if self.db.workspace(workspace_id) is None:
            raise TaskError(f"没有工作区 {workspace_id}")
        mission = self.db.mission(mission_id) if mission_id else None
        if mission_id and mission is None:
            raise TaskError(f"没有 Mission {mission_id}")
        if mission and mission["workspace_id"] != workspace_id:
            raise TaskError("Mission 不属于这个工作区")

        depth = (parent["depth"] + (0 if is_result else 1)) if parent else 0
        if parent:
            chain = [*parent["chain"], *([parent["to_agent_id"]] if parent["to_agent_id"] else [])]
        else:
            chain = [from_agent_id] if from_agent_id else []
        chain = list(dict.fromkeys(c for c in chain if c))
        ws_log = dict(workspace_id=workspace_id, mission_id=mission_id, agent_id=from_agent_id, actor=from_agent_id or created_by)

        if depth > self.limits.max_depth:
            self._blocked("depth", f"交接深度已达上限 {self.limits.max_depth}，不再继续向下分配", objective, ws_log)
        if from_agent_id and not is_result:
            since = now_dt() - timedelta(hours=1)
            if self.db.recent_delegations(from_agent_id, since) >= self.limits.delegations_per_hour:
                self._blocked("rate", f"这个 Agent 一小时内已经分配了 {self.limits.delegations_per_hour} 个任务，暂停分配（防止任务风暴）", objective, ws_log)
        if mission and mission["status"] not in (C.M_ACTIVE,):
            self._blocked("mission", f"Mission {mission['name']} 状态是 {mission['status']}，不接受新任务", objective, ws_log)
        if mission and Decimal(mission["budget_usd"] or "0") > 0 and Decimal(mission["cost_usd"] or "0") >= Decimal(mission["budget_usd"]):
            self._blocked("budget", f"Mission 预算 ${mission['budget_usd']} 已用完", objective, ws_log)
        if parent and Decimal(parent["budget_usd"] or "0") > 0 and Decimal(parent["cost_usd"] or "0") >= Decimal(parent["budget_usd"]):
            self._blocked("budget", f"上游任务 {parent['id']} 的预算已用完", objective, ws_log)

        need = tuple(dict.fromkeys(list(required or []) or list(C.KIND_PERMISSIONS.get(kind, ()))))
        for p in need:
            if p not in C.AUTONOMY_PERMISSIONS and p not in ("chain:read", "net:etherscan", "report:write"):
                raise TaskError(f"没有权限 {p}")
        exclude = set() if is_result else set(chain) | ({from_agent_id} if from_agent_id else set())

        if kind == "fix" and fingerprint:
            n = self.db.fix_count(fingerprint, now_dt() - timedelta(hours=24))
            if n >= self.limits.fix_attempts_per_issue:
                self._blocked("fix_cap", f"这个问题 24 小时内已经有 {n} 次修复任务，不再自动修复（防止反复修改代码），请人工处理", objective, ws_log)

        considered: list[dict] = []
        target = None
        if to_agent_id:
            target = self.db.agent(to_agent_id)
            why = None
            if target is None:
                why = f"没有 Agent {to_agent_id}"
            elif to_agent_id in exclude:
                why = f"{target['name']} 已经在这条交接链上，交回去会形成循环（{' → '.join(self._names(chain + [to_agent_id]))}）"
            elif target["status"] != "active":
                why = f"{target['name']} 当前状态是 {target['status']}"
            elif not has_workspace(target, workspace_id):
                why = f"{target['name']} 没有这个工作区的权限"
            elif not target.get("autonomous"):
                why = f"{target['name']} 没有设置长期职责，不能接收自主任务"
            else:
                missing = holds(target, need)
                if missing:
                    why = f"{target['name']} 缺少权限：{'、'.join(missing)}"
            if why:
                self._blocked("circular" if "循环" in why else "target", why, objective, ws_log)
        else:
            target, considered = match(self.db, workspace_id=workspace_id, kind=kind, objective=objective, required=need, exclude=exclude)

        anchor = f"result:{parent['id']}" if is_result and parent else (fingerprint or event_id)
        key = dedupe_key(workspace_id, kind, target["id"] if target else None, objective, (anchor + ":" + (parent["root_id"] if parent else "")) if anchor else None)
        existing = self.db.open_task_by_dedupe(key)
        if existing:
            self.act.log("task.deduplicated", f"相同的任务已经在处理：{existing['id']}", task_id=existing["id"], reason=objective[:300], **ws_log)
            return existing, {"deduplicated": True, "considered": considered}

        budget = self._budget(budget_usd, target)
        deadline = now_dt() + timedelta(seconds=max(60, min(int(deadline_s or self.limits.task_timeout_s), 7 * 86400)))
        status = C.T_QUEUED if target else C.T_UNASSIGNED
        t = self.db.add_task(workspace_id=workspace_id, mission_id=mission_id, from_agent_id=from_agent_id, to_agent_id=target["id"] if target else None,
                             kind=kind, objective=objective[:4000], priority=priority, evidence=_evidence(evidence), required=list(need), status=status,
                             parent_id=parent["id"] if parent else None, root_id=parent["root_id"] if parent else None, depth=depth, chain=chain,
                             follow_up=int(bool(follow_up)), event_id=event_id, fingerprint=fingerprint, dedupe_key=key,
                             max_attempts=self.limits.max_attempts, budget_usd=budget, deadline_at=iso(deadline), created_by=created_by)
        if target:
            typ = "task.delegated" if from_agent_id else "task.created"
            title = (f"{self._name(from_agent_id)} → {target['name']}：{objective[:120]}" if from_agent_id else f"新任务交给 {target['name']}：{objective[:120]}")
            self.act.log(typ, title, task_id=t["id"], target_agent_id=target["id"], event_id=event_id, reason=reason or _why_matched(target, considered, to_agent_id),
                         data={"kind": kind, "priority": priority, "depth": depth, "considered": considered[:20]}, **ws_log)
            if kind == "fix":
                self.act.log("fix.started", f"修复任务已交给 {target['name']}", task_id=t["id"], target_agent_id=target["id"], **ws_log)
        else:
            self.act.log("task.unassigned", f"没有合适的 Agent 接手：{objective[:120]}", task_id=t["id"], event_id=event_id, severity="medium", needs_human=True,
                         reason="；".join(f"{c['name']}：{c['reason']}" for c in considered if not c["eligible"])[:1500] or "工作区里没有可用的 Agent",
                         data={"considered": considered[:20], "required": list(need)}, **ws_log)
            self.act.notify(f"任务没有人接手：{objective[:80]}", "没有 Agent 同时具备这个工作区的权限、所需权限和相关职责。请在 Missions 里手动指派，或新建一个 Agent。",
                            severity="medium", workspace_id=workspace_id, task_id=t["id"], mission_id=mission_id, dedupe_key=f"unassigned:{t['id']}")
        return t, {"deduplicated": False, "considered": considered}

    def _blocked(self, guard: str, message: str, objective: str, ws_log: dict) -> None:
        self.act.log("task.blocked", f"交接被拦下：{message}", severity="medium", reason=objective[:300], data={"guard": guard}, **ws_log)
        raise DelegationBlocked(guard, message)

    def _budget(self, v: Any, target: dict | None) -> str:
        try:
            b = Decimal(str(v)) if v not in (None, "") else Decimal(self.limits.default_task_budget)
        except InvalidOperation:
            raise TaskError(f"预算格式不对：{v}") from None
        if b < 0:
            raise TaskError("预算不能是负数")
        cap = ((target or {}).get("approval_rules") or {}).get("max_task_budget_usd")
        if cap:
            b = min(b, Decimal(cap))
        return str(b)

    def _name(self, aid: str | None) -> str:
        if not aid:
            return "System"
        a = self.db.agent(aid)
        return a["name"] if a else aid

    def _names(self, ids: list[str]) -> list[str]:
        return [self._name(i) for i in ids]

    # ------------------------------------------------------------------ assign / reject

    def assign(self, tid: str, agent_id: str, by: str) -> dict:
        """A human picks the agent for an unassigned (or rejected) task. The same checks as an agent's hand-off apply."""
        t = self.task_or_raise(tid)
        if t["status"] not in (C.T_UNASSIGNED, C.T_REJECTED):
            raise TaskError("只能指派没有人接手的任务")
        a = self.db.agent(agent_id)
        if a is None or a["status"] != "active" or not a.get("autonomous"):
            raise TaskError("这个 Agent 不能接收任务（不存在、未启用或没有长期职责）")
        if not has_workspace(a, t["workspace_id"]):
            raise TaskError(f"{a['name']} 没有这个工作区的权限")
        missing = holds(a, t["required"])
        if missing:
            raise TaskError(f"{a['name']} 缺少权限：{'、'.join(missing)}")
        if agent_id in t["chain"]:
            raise TaskError(f"{a['name']} 已经在这条交接链上，会形成循环")
        if not self.db.transition(tid, (C.T_UNASSIGNED, C.T_REJECTED), C.T_QUEUED, to_agent_id=agent_id):
            raise TaskError("任务状态已经变了，请刷新")
        self.act.log("task.delegated", f"人工指派给 {a['name']}：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                     task_id=tid, target_agent_id=agent_id, actor=by, reason="人工指派")
        self.act.audit(f"指派任务 {tid} → {a['name']}", by, {"task": tid, "agent": agent_id})
        return self.db.task(tid)

    def reject(self, t: dict, agent: dict, reason: str, run_id: str | None = None) -> dict:
        """The agent refused the task. Another eligible agent gets it if there is one; else a human must assign it."""
        ev = dict(t["evidence"] or {})
        rejected = list(dict.fromkeys([*(ev.get("rejected_by") or []), agent["id"]]))
        ev["rejected_by"] = rejected
        ev.setdefault("rejections", []).append({"agent_id": agent["id"], "reason": reason[:500]})
        self.act.log("task.rejected", f"{agent['name']} 拒绝了任务：{reason[:160]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                     task_id=t["id"], agent_id=agent["id"], run_id=run_id, actor=agent["id"], reason=reason[:1000], severity="low")
        exclude = set(t["chain"]) | set(rejected) | ({t["from_agent_id"]} if t["from_agent_id"] else set())
        alt, considered = match(self.db, workspace_id=t["workspace_id"], kind=t["kind"], objective=t["objective"], required=tuple(t["required"]), exclude=exclude)
        if alt and self.db.transition(t["id"], (C.T_RUNNING,), C.T_QUEUED, to_agent_id=alt["id"], evidence=ev, checkpoint=None, lease_owner=None, lease_until=None):
            self.act.log("task.delegated", f"改交给 {alt['name']}：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                         task_id=t["id"], agent_id=agent["id"], target_agent_id=alt["id"], actor="system", reason=f"{agent['name']} 拒绝后重新匹配",
                         data={"considered": considered[:20]})
        elif self.db.transition(t["id"], (C.T_RUNNING,), C.T_UNASSIGNED, to_agent_id=None, evidence=ev, checkpoint=None, lease_owner=None, lease_until=None):
            self.act.log("task.unassigned", f"任务被拒绝后没有其他 Agent 能接手：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                         task_id=t["id"], severity="medium", needs_human=True, reason=reason[:500])
            self.act.notify(f"任务没有人接手：{t['objective'][:80]}", f"{agent['name']} 拒绝了这个任务（{reason[:200]}），也没有其他合适的 Agent。",
                            severity="medium", workspace_id=t["workspace_id"], task_id=t["id"], mission_id=t["mission_id"], dedupe_key=f"unassigned:{t['id']}:{len(rejected)}")
        return self.db.task(t["id"])

    # ------------------------------------------------------------------ finish / propagate

    def finished(self, t: dict) -> None:
        """A task reached a final state: wake whoever waits on it, and send the result back when asked to."""
        t = self.db.task(t["id"]) or t
        if t["parent_id"]:
            parent = self.db.task(t["parent_id"])
            if parent and parent["status"] == C.T_WAITING and t["id"] in (parent["waiting_on"] or []):
                kids = [self.db.task(k) for k in parent["waiting_on"]]
                if all(k is None or k["status"] in C.TASK_FINAL for k in kids):
                    if self.db.transition(parent["id"], (C.T_WAITING,), C.T_QUEUED, lease_owner=None, lease_until=None):
                        self.act.log("task.resumed", f"{self._name(parent['to_agent_id'])} 收到 {len(kids)} 个子任务的结果，重新唤醒",
                                     workspace_id=parent["workspace_id"], mission_id=parent["mission_id"], task_id=parent["id"],
                                     agent_id=parent["to_agent_id"], reason="等待的子任务都已结束", data={"children": parent["waiting_on"]})
            elif parent and t["follow_up"] and t["from_agent_id"]:
                self._follow_up(t, parent)
        elif t["follow_up"] and t["from_agent_id"]:
            self._follow_up(t, None)
        if t["mission_id"] and t["root_id"] == t["id"]:
            m = self.db.mission(t["mission_id"])
            if m and m["type"] == "one_time" and m["status"] == C.M_ACTIVE:
                st = C.M_COMPLETED if t["status"] == C.T_COMPLETED else C.M_FAILED
                self.db.update_mission(m["id"], status=st, result={"task_id": t["id"], "status": t["status"], "summary": (t["result"] or {}).get("summary") if isinstance(t["result"], dict) else None, "error": t["error"]})
                self.act.log("mission.completed", f"Mission {m['name']} {'完成' if st == C.M_COMPLETED else '失败'}", workspace_id=m["workspace_id"],
                             mission_id=m["id"], task_id=t["id"], result=(t["error"] or "")[:300], severity="info" if st == C.M_COMPLETED else "medium")

    def _follow_up(self, t: dict, parent: dict | None) -> None:
        """The sender asked to hear back: a review task for it, carrying this task's result. Results never ask for
        another result, so this cannot ping-pong."""
        sender = self.db.agent(t["from_agent_id"])
        if not sender or sender["status"] != "active":
            return
        res = t["result"] if isinstance(t["result"], dict) else {"value": t["result"]}
        objective = (f"你交给 {self._name(t['to_agent_id'])} 的任务「{t['objective'][:200]}」已经结束，状态 {t['status']}。"
                     "阅读结果，决定是否需要后续动作（例如验证修复、通知用户、继续监控）；不需要就直接完成。")
        try:
            self.create_task(workspace_id=t["workspace_id"], objective=objective, kind="review", priority=t["priority"],
                             evidence={"returned_task_id": t["id"], "status": t["status"], "result": res, "error": t["error"]},
                             to_agent_id=sender["id"], from_agent_id=t["to_agent_id"], mission_id=t["mission_id"], parent=t,
                             fingerprint=t["fingerprint"], created_by="system", reason="任务结果返回给发起方", is_result=True)
        except TaskError as e:
            self.act.log("system.error", f"结果没能返回给 {sender['name']}：{e}", workspace_id=t["workspace_id"], task_id=t["id"], severity="low")

    # ------------------------------------------------------------------ cancel / retry

    def cancel(self, tid: str, by: str, reason: str = "") -> dict:
        t = self.task_or_raise(tid)
        if t["status"] in C.TASK_FINAL:
            raise TaskError("任务已经结束")
        n = 0
        for x in [t, *self._descendants(tid)]:
            if x["status"] not in C.TASK_FINAL and self.db.transition(x["id"], C.TASK_OPEN, C.T_CANCELLED, error=reason or f"被 {by} 取消"):
                n += 1
                self.act.log("task.cancelled", f"任务已取消：{x['objective'][:120]}", workspace_id=x["workspace_id"], mission_id=x["mission_id"], task_id=x["id"],
                             agent_id=x["to_agent_id"], actor=by, reason=reason)
        self.act.audit(f"取消任务 {tid}（含 {n - 1} 个子任务）", by, {"task": tid})
        self.finished(self.db.task(tid))
        return self.db.task(tid)

    def _descendants(self, tid: str) -> list[dict]:
        out, todo = [], [tid]
        while todo:
            kids = self.db.tasks(parent_id=todo.pop(), limit=500, order="created_at")
            out += kids
            todo += [k["id"] for k in kids]
        return out

    def retry(self, tid: str, by: str) -> dict:
        t = self.task_or_raise(tid)
        if t["status"] not in (C.T_FAILED, C.T_CANCELLED):
            raise TaskError("只能重试失败或取消的任务")
        if not t["to_agent_id"]:
            raise TaskError("任务没有指派 Agent，请先指派")
        if not self.db.transition(tid, (C.T_FAILED, C.T_CANCELLED), C.T_QUEUED, error=None, finished_at=None, attempts=0, checkpoint=None,
                                  deadline_at=iso(now_dt() + timedelta(seconds=self.limits.task_timeout_s))):
            raise TaskError("任务状态已经变了，请刷新")
        self.act.log("task.retried", f"人工重试：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"], task_id=tid,
                     agent_id=t["to_agent_id"], actor=by)
        self.act.audit(f"重试任务 {tid}", by, {"task": tid})
        return self.db.task(tid)

    def task_or_raise(self, tid: str) -> dict:
        t = self.db.task(tid)
        if t is None:
            raise TaskError(f"没有任务 {tid}")
        return t

    # ------------------------------------------------------------------ deadlines

    def enforce_deadlines(self) -> int:
        n = 0
        for t in self.db.overdue_tasks():
            if self.db.transition(t["id"], (C.T_QUEUED, C.T_WAITING, C.T_UNASSIGNED, C.T_RUNNING), C.T_FAILED, error="超过截止时间"):
                n += 1
                self.act.log("task.timeout", f"任务超时：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"], task_id=t["id"],
                             agent_id=t["to_agent_id"], severity="medium", needs_human=True, reason=f"截止时间 {t['deadline_at']}")
                self.act.notify(f"任务超时：{t['objective'][:80]}", "任务在截止时间前没有完成。", severity="medium", agent_id=t["to_agent_id"],
                                workspace_id=t["workspace_id"], task_id=t["id"], dedupe_key=f"timeout:{t['id']}")
                self.finished(self.db.task(t["id"]))
        return n


def _evidence(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, dict):
        out = v
    else:
        out = {"note": v}
    s = json.dumps(out, ensure_ascii=False, default=str)
    if len(s) > 30000:
        return {"truncated": True, "preview": s[:30000]}
    return out


def _why_matched(target: dict, considered: list[dict], named: str | None) -> str:
    if named:
        return f"发起方指定了 {target['name']}，权限和工作区检查通过"
    if not considered:
        return ""
    c = next((x for x in considered if x["agent_id"] == target["id"]), None)
    others = sum(1 for x in considered if x["eligible"]) - 1
    return f"自动匹配：{target['name']} 有所需权限和工作区，职责相关度 {c['relevance'] if c else 0:.1f}，进行中任务 {c['load'] if c else 0} 个" + \
        (f"（另有 {others} 个候选）" if others > 0 else "")
