"""Mission Orchestrator: long-lived goals that agents own, and the rules that turn events and schedules into work.

A mission is not a one-shot job. One-time missions end with their task; continuous, event-driven and scheduled
missions stay active until a person pauses or stops them, and every trigger starts an independent task (its own
runs, its own result) under the mission. An agent activated with a standing responsibility gets one such mission
automatically, so it is woken by the events of its workspace without anyone writing trigger rules.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from agent.workforce.cron import Cron

from . import constants as C
from .activity import Activity
from .db import AutonomyDB, iso, now_dt


class MissionError(ValueError):
    pass


# Event types that are problems in a project; a responsibility mission listens to these by default.
DEFAULT_TRIGGER_TYPES = ["app.error", "ci.failed", "health.down", "github.workflow_failed", "bsc.*", "patch.applied", "pr.merged"]


def clean_triggers(db: AutonomyDB, wid: str, triggers: Any) -> list[dict]:
    if triggers in (None, ""):
        return []
    if not isinstance(triggers, list):
        raise MissionError("triggers 必须是列表")
    out = []
    for tr in triggers[:20]:
        if not isinstance(tr, dict):
            raise MissionError("每个 trigger 必须是对象")
        d: dict[str, Any] = {}
        if tr.get("watcher_id"):
            w = db.watcher(str(tr["watcher_id"]))
            if w is None or w["workspace_id"] != wid:
                raise MissionError(f"这个工作区没有 Watcher {tr['watcher_id']}")
            d["watcher_id"] = w["id"]
        if tr.get("source"):
            if tr["source"] not in C.WATCHER_KINDS and tr["source"] not in ("manual", "system"):
                raise MissionError(f"没有这种事件来源：{tr['source']}")
            d["source"] = tr["source"]
        types = tr.get("types") or []
        if not isinstance(types, list) or not all(isinstance(t, str) and t for t in types):
            raise MissionError("types 必须是事件类型列表，例如 [\"app.error\", \"bsc.*\"]")
        d["types"] = [t[:60] for t in types][:20]
        sev = tr.get("min_severity") or "medium"
        if sev not in C.SEVERITIES:
            raise MissionError(f"min_severity 只能是 {', '.join(C.SEVERITIES)}")
        d["min_severity"] = sev
        kind = tr.get("task_kind")
        if kind and kind not in C.TASK_KINDS:
            raise MissionError(f"task_kind 只能是 {', '.join(C.TASK_KINDS)}")
        if kind:
            d["task_kind"] = kind
        out.append(d)
    return out


def trigger_matches(tr: dict, ev: dict) -> bool:
    if tr.get("watcher_id") and tr["watcher_id"] != ev.get("watcher_id"):
        return False
    if tr.get("source") and tr["source"] != ev["source"]:
        return False
    types = tr.get("types") or []
    if types and not any(ev["type"] == t or (t.endswith("*") and ev["type"].startswith(t[:-1])) for t in types):
        return False
    return C.SEV_RANK.get(ev["severity"], 0) >= C.SEV_RANK.get(tr.get("min_severity") or "medium", 2)


def _money(v: Any) -> str:
    try:
        d = Decimal(str(v if v not in (None, "") else 0))
    except InvalidOperation:
        raise MissionError(f"预算格式不对：{v}") from None
    if not d.is_finite() or d < 0:
        raise MissionError("预算不能是负数")
    return str(d)


class Missions:
    def __init__(self, db: AutonomyDB, activity: Activity, wakeup) -> None:
        self.db = db
        self.act = activity
        self.wakeup = wakeup

    def _agents(self, wid: str, lead: str | None, ids: list[str]) -> tuple[str | None, list[str]]:
        ids = list(dict.fromkeys([*(ids or []), *([lead] if lead else [])]))
        for aid in ids:
            a = self.db.agent(aid)
            if a is None:
                raise MissionError(f"没有 Agent {aid}")
            if wid not in (a.get("workspace_ids") or []):
                raise MissionError(f"{a['name']} 没有这个工作区的权限，先在 Agent 设置里绑定工作区")
        return (lead or (ids[0] if ids else None)), ids

    def create(self, body: dict, by: str, *, responsibility_of: str | None = None) -> dict:
        wid = str(body.get("workspace_id") or "")
        if self.db.workspace(wid) is None:
            raise MissionError("请选择工作区")
        name = str(body.get("name") or "").strip()[:120]
        goal = str(body.get("goal") or "").strip()[:4000]
        if not name or not goal:
            raise MissionError("Mission 需要名称和目标")
        typ = body.get("type") or "one_time"
        if typ not in C.MISSION_TYPES:
            raise MissionError(f"类型只能是 {', '.join(C.MISSION_TYPES)}")
        lead, ids = self._agents(wid, body.get("lead_agent_id"), body.get("agent_ids") or [])
        if not lead:
            raise MissionError("Mission 至少要有一个负责的 Agent")
        triggers = clean_triggers(self.db, wid, body.get("triggers"))
        cron = str(body.get("schedule_cron") or "").strip() or None
        nxt = None
        if cron:
            try:
                nxt = iso(Cron.parse(cron).next_after(now_dt()))
            except ValueError as e:
                raise MissionError(str(e)) from None
        if typ == "scheduled" and not cron:
            raise MissionError("Scheduled Mission 需要 cron 表达式")
        if typ == "event_driven" and not triggers:
            raise MissionError("Event-driven Mission 需要至少一个触发条件")
        perms = [p for p in body.get("permissions") or [] if isinstance(p, str)]
        m = self.db.add_mission(workspace_id=wid, name=name, goal=goal, type=typ, lead_agent_id=lead, agent_ids=ids, triggers=triggers,
                                schedule_cron=cron, next_run_at=nxt, permissions=perms, budget_usd=_money(body.get("budget_usd")),
                                responsibility_of=responsibility_of, created_by=by)
        self.act.log("mission.created", f"Mission {name}（{C.MISSION_TYPES[typ].split('：')[0]}）", workspace_id=wid, mission_id=m["id"], agent_id=lead, actor=by,
                     data={"type": typ, "triggers": triggers, "cron": cron})
        self.act.audit(f"建立 Mission {name}（{m['id']}）", by, {"mission": m["id"]})
        if typ == "one_time":
            self._start(m, by, objective=goal, kind=body.get("task_kind") or "general", reason="One-time Mission 开始")
        return self.db.mission(m["id"])

    def update(self, mid: str, body: dict, by: str) -> dict:
        m = self.get(mid)
        f: dict[str, Any] = {}
        for k, n in (("name", 120), ("goal", 4000)):
            if k in body:
                v = str(body[k] or "").strip()[:n]
                if not v:
                    raise MissionError(f"{k} 不能为空")
                f[k] = v
        if "lead_agent_id" in body or "agent_ids" in body:
            f["lead_agent_id"], f["agent_ids"] = self._agents(m["workspace_id"], body.get("lead_agent_id", m["lead_agent_id"]), body.get("agent_ids", m["agent_ids"]))
        if "triggers" in body:
            if m.get("responsibility_of"):
                raise MissionError("这是 Agent 的长期职责 Mission：唤醒它的事件在 Agent 设置里修改")
            f["triggers"] = clean_triggers(self.db, m["workspace_id"], body["triggers"])
        if "schedule_cron" in body:
            cron = str(body["schedule_cron"] or "").strip() or None
            f["schedule_cron"] = cron
            try:
                f["next_run_at"] = iso(Cron.parse(cron).next_after(now_dt())) if cron else None
            except ValueError as e:
                raise MissionError(str(e)) from None
        if "budget_usd" in body:
            f["budget_usd"] = _money(body["budget_usd"])
        if "permissions" in body:
            f["permissions"] = [p for p in body["permissions"] or [] if isinstance(p, str)]
        m = self.db.update_mission(mid, **f)
        self.act.audit(f"修改 Mission {m['name']}：{', '.join(f) or '无变化'}", by, {"mission": mid})
        return m

    def set_status(self, mid: str, action: str, by: str) -> dict:
        m = self.get(mid)
        to = {"pause": C.M_PAUSED, "resume": C.M_ACTIVE, "stop": C.M_STOPPED}.get(action)
        if to is None:
            raise MissionError("操作只能是 pause / resume / stop")
        if m["status"] in (C.M_STOPPED, C.M_COMPLETED, C.M_FAILED) and to != C.M_STOPPED:
            raise MissionError(f"Mission 已经{m['status']}，不能{action}")
        m = self.db.update_mission(mid, status=to)
        if to == C.M_STOPPED:
            for t in self.db.tasks(mission_id=mid, status=[C.T_QUEUED, C.T_UNASSIGNED], limit=500):
                self.db.transition(t["id"], (C.T_QUEUED, C.T_UNASSIGNED), C.T_CANCELLED, error="Mission 已停止")
        typ = {"pause": "mission.paused", "resume": "mission.resumed", "stop": "mission.stopped"}[action]
        self.act.log(typ, f"Mission {m['name']} {dict(pause='已暂停', resume='已恢复', stop='已停止')[action]}", workspace_id=m["workspace_id"], mission_id=mid,
                     actor=by, reason="暂停后不再因为事件或定时创建新任务；进行中的任务会做完" if action == "pause" else "")
        self.act.audit(f"Mission {m['name']} → {to}", by, {"mission": mid})
        return m

    def get(self, mid: str) -> dict:
        m = self.db.mission(mid)
        if m is None:
            raise MissionError(f"没有 Mission {mid}")
        return m

    # ------------------------------------------------------------------ responsibility missions

    def ensure_responsibility(self, agent: dict, by: str) -> list[dict]:
        """An activated agent with a standing responsibility owns one mission per workspace it was given: woken by that
        workspace's events (event-driven and continuous agents), by its schedule (scheduled agents), or only by tasks."""
        out = []
        mode = agent.get("work_mode") or "event"
        for wid in (agent.get("workspace_ids") or []) if mode != "on_demand" else []:
            existing = self.db.missions(workspace_id=wid, responsibility_of=agent["id"], limit=1)
            typ = {"event": "event_driven", "continuous": "continuous", "scheduled": "scheduled"}[mode]
            wake = agent.get("wake_on")
            wake = DEFAULT_TRIGGER_TYPES if wake is None else wake
            triggers = [] if mode == "scheduled" or not wake else [{"types": list(wake), "min_severity": "medium"}]
            cron = agent.get("schedule_cron") if mode in ("scheduled", "continuous") else None
            f = dict(goal=agent.get("responsibility") or "", type=typ, triggers=triggers, schedule_cron=cron,
                     next_run_at=iso(Cron.parse(cron).next_after(now_dt())) if cron else None)
            if existing:
                m = existing[0]
                keep = dict(f)
                if m["status"] in (C.M_STOPPED,):
                    keep["status"] = C.M_ACTIVE
                out.append(self.db.update_mission(m["id"], **keep))
                continue
            m = self.db.add_mission(workspace_id=wid, name=f"{agent['name']} · 长期职责", lead_agent_id=agent["id"], agent_ids=[agent["id"]],
                                    permissions=[], budget_usd="0", responsibility_of=agent["id"], created_by=by, **f)
            self.act.log("mission.created", f"{agent['name']} 的长期职责 Mission 已建立", workspace_id=wid, mission_id=m["id"], agent_id=agent["id"], actor=by,
                         data={"type": typ, "triggers": triggers})
            out.append(m)
        # Workspaces the agent was removed from (or an on-demand agent): its standing mission there stops.
        for m in self.db.missions(responsibility_of=agent["id"], status=[C.M_ACTIVE, C.M_PAUSED], limit=100):
            if mode == "on_demand" or m["workspace_id"] not in (agent.get("workspace_ids") or []):
                self.db.update_mission(m["id"], status=C.M_STOPPED)
        return out

    def pause_responsibility(self, agent_id: str, paused: bool) -> None:
        for m in self.db.missions(responsibility_of=agent_id, status=[C.M_ACTIVE] if paused else [C.M_PAUSED], limit=100):
            self.db.update_mission(m["id"], status=C.M_PAUSED if paused else C.M_ACTIVE)

    # ------------------------------------------------------------------ starting work

    def _start(self, m: dict, by: str, *, objective: str, kind: str, reason: str, event: dict | None = None, to_agent_id: str | None = None,
               evidence: Any = None) -> dict | None:
        from .wakeup import DelegationBlocked, TaskError

        try:
            t, info = self.wakeup.create_task(workspace_id=m["workspace_id"], objective=objective, kind=kind if kind in C.TASK_KINDS else "general",
                                              priority=1 if event and C.SEV_RANK.get(event["severity"], 0) >= 3 else 2, evidence=evidence,
                                              to_agent_id=to_agent_id or m["lead_agent_id"], mission_id=m["id"], event_id=event["id"] if event else None,
                                              fingerprint=(event or {}).get("fingerprint"), created_by=by, reason=reason)
        except DelegationBlocked as e:
            self.act.log("mission.triggered", f"Mission {m['name']} 没有创建任务：{e}", workspace_id=m["workspace_id"], mission_id=m["id"],
                         event_id=event["id"] if event else None, severity="low", reason=str(e))
            return None
        except TaskError as e:
            self.act.log("system.error", f"Mission {m['name']} 创建任务失败：{e}", workspace_id=m["workspace_id"], mission_id=m["id"], severity="medium")
            return None
        if not info["deduplicated"]:
            self.db.update_mission(m["id"], run_count=m["run_count"] + 1, last_run_at=iso(now_dt()))
            self.act.log("mission.triggered", f"Mission {m['name']} 开始一次执行", workspace_id=m["workspace_id"], mission_id=m["id"], task_id=t["id"],
                         target_agent_id=t["to_agent_id"], event_id=event["id"] if event else None, reason=reason, actor=by)
        return t

    def due_schedules(self) -> int:
        n = 0
        now = now_dt()
        for m in self.db.missions(status=[C.M_ACTIVE], limit=500):
            if not m["schedule_cron"] or not m["next_run_at"] or m["next_run_at"] > iso(now):
                continue
            try:
                nxt = iso(Cron.parse(m["schedule_cron"]).next_after(now))
            except ValueError:
                continue
            # Move the schedule first: a crash after this line skips one run instead of running it twice.
            self.db.update_mission(m["id"], next_run_at=nxt)
            objective = f"定时执行（{m['schedule_cron']} UTC）：{m['goal'][:1500]}"
            if self._start(m, "schedule", objective=objective, kind="general", reason=f"cron {m['schedule_cron']}"):
                n += 1
        return n

    def run_now(self, mid: str, by: str) -> dict:
        m = self.get(mid)
        if m["status"] != C.M_ACTIVE:
            raise MissionError("只有进行中的 Mission 可以立即执行")
        t = self._start(m, by, objective=m["goal"], kind="general", reason="人工立即执行")
        if t is None:
            raise MissionError("没有创建任务，原因见 Activity")
        return t
