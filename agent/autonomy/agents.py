"""Agent configuration for autonomous work, templates, the derived agent state, and matching a task to an agent.

An agent is long-lived configuration: a name, a responsibility in the user's own words, the workspaces it may
work in, its tools and permissions, a work mode, a budget, approval and notification rules. Templates only fill
the form; any agent can hold any responsibility and any permission the server knows.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from agent.workforce.cron import Cron

from . import constants as C
from .db import AutonomyDB, now_dt, parse

TEMPLATES: list[dict] = [
    {"id": "bug_hunter", "name": "Bug Hunter", "work_mode": "continuous",
     "responsibility": "你负责持续检查这个项目是否存在 Bug。发现问题后主动调查，整理证据，并把需要分析或修复的问题交给合适的 Agent。修复合并后验证问题是否消失。",
     "permissions": ["delegate", "memory:write", "code:read", "code:test", "net:http"], "skills": [],
     "wake_on": ["app.error", "ci.failed", "health.down", "health.recovered", "github.workflow_failed", "patch.applied", "pr.merged"]},
    {"id": "investigator", "name": "Investigator", "work_mode": "event",
     "responsibility": "你负责分析其他 Agent 发现的问题：读取证据和代码，找出根本原因，给出修复方案，并把结构化的修复任务交给有权限的修复 Agent。",
     "permissions": ["delegate", "memory:write", "code:read", "code:test"], "skills": [], "wake_on": []},
    {"id": "fix_agent", "name": "Fix Agent", "work_mode": "event",
     "responsibility": "你负责接收修复任务：在隔离环境里修改代码、运行相关测试，测试通过后提交待审核的补丁或 Draft Pull Request。不合并，不部署。",
     "permissions": ["memory:write", "code:read", "code:write", "code:test", "code:propose"], "skills": [], "wake_on": []},
    {"id": "token_watcher", "name": "Token Watcher", "work_mode": "event",
     "responsibility": "你负责长期关注这个 BSC 代币：大额转账、新持有人、合约事件。只在事件值得注意时分析证据并通知用户。钱包之间有转账不等于有不法行为，结论必须区分链上事实和推断。",
     "permissions": ["delegate", "memory:write"], "skills": ["token_info", "wallet_analysis", "contract_events"],
     "extra": ["chain:read"], "wake_on": ["bsc.*"]},
    {"id": "contract_monitor", "name": "Contract Monitor", "work_mode": "event",
     "responsibility": "你负责监控这些合约的事件和状态变化（所有权、升级、暂停、权限）。发现异常时调查并通知用户，需要代码层面分析时交给其他 Agent。",
     "permissions": ["delegate", "memory:write"], "skills": ["contract_analysis", "contract_events"], "extra": ["chain:read"],
     "wake_on": ["bsc.contract_event", "bsc.large_transfer"]},
    {"id": "project_optimizer", "name": "Project Optimizer", "work_mode": "scheduled", "schedule_cron": "0 3 * * *",
     "responsibility": "你负责长期检查项目的测试、依赖和代码质量。发现测试失败、性能恶化或重复错误时创建调查任务；只在有明确改进时提交 Draft PR，不为了保持忙碌而修改代码。",
     "permissions": ["delegate", "memory:write", "code:read", "code:test"], "skills": []},
    {"id": "research_agent", "name": "Research Agent", "work_mode": "on_demand",
     "responsibility": "你负责按需调研：读取工作区里的资料和链上数据，给出有来源的结论。",
     "permissions": ["memory:write", "code:read"], "skills": ["token_info", "contract_analysis", "wallet_analysis"], "extra": ["chain:read"]},
    {"id": "documentation_agent", "name": "Documentation Agent", "work_mode": "on_demand",
     "responsibility": "你负责维护项目文档：读取代码变化，在隔离环境里更新文档，提交待审核的补丁。",
     "permissions": ["memory:write", "code:read", "code:write", "code:propose"], "skills": []},
]
TEMPLATE_BY_ID = {t["id"]: t for t in TEMPLATES}

STATE_LABELS = {
    C.A_WATCHING: "Watching", C.A_RUNNING: "Working", C.A_INVESTIGATING: "Investigating", C.A_WAITING: "Waiting",
    C.A_WAITING_APPROVAL: "Waiting Approval", C.A_PAUSED: "Paused", C.A_OFFLINE: "Offline", C.A_ERROR: "Error",
    C.A_QUEUED: "Queued", C.A_IDLE: "Idle", C.A_ARCHIVED: "Archived",
}


WAKE_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.([a-z0-9_]+|\*))*$|^\*$")


class AgentError(ValueError):
    pass


def clean_autonomy(body: dict, db: AutonomyDB, partial: bool = False) -> dict:
    """The autonomy fields of an agent body, checked. Base fields (name, model, skills, permissions) go through
    the workforce's own validation."""
    f: dict[str, Any] = {}
    if "responsibility" in body or not partial:
        r = str(body.get("responsibility") or "").strip()
        if not r and not partial:
            raise AgentError("请写下这个 Agent 的工作职责")
        f["responsibility"] = r[:4000]
    if "work_mode" in body or not partial:
        m = body.get("work_mode") or "event"
        if m not in C.WORK_MODES:
            raise AgentError(f"工作模式只能是 {', '.join(C.WORK_MODES)}")
        f["work_mode"] = m
    if "schedule_cron" in body or f.get("work_mode") == "scheduled":
        cron = str(body.get("schedule_cron") or "").strip() or None
        if f.get("work_mode") == "scheduled" and not cron:
            raise AgentError("Scheduled 模式需要 cron 表达式（例如 0 3 * * * 表示每天 03:00 UTC）")
        if cron:
            try:
                Cron.parse(cron)
            except ValueError as e:
                raise AgentError(f"cron 表达式不对：{e}") from None
        f["schedule_cron"] = cron
    if "workspace_ids" in body:
        ids = list(dict.fromkeys(str(w) for w in body.get("workspace_ids") or []))
        for w in ids:
            if db.workspace(w) is None:
                raise AgentError(f"没有工作区 {w}")
        f["workspace_ids"] = ids
    if "approval_rules" in body:
        ar = body.get("approval_rules") or {}
        if not isinstance(ar, dict):
            raise AgentError("审批规则必须是对象")
        # Patches and PRs always need a human; these only add more checkpoints, they cannot remove that one.
        f["approval_rules"] = {"patch": "always", "delegate_outside_mission": bool(ar.get("delegate_outside_mission", False)),
                               "max_task_budget_usd": _money(ar.get("max_task_budget_usd"), "单个任务预算上限")}
    if "notify_rules" in body:
        nr = body.get("notify_rules") or {}
        if not isinstance(nr, dict):
            raise AgentError("通知规则必须是对象")
        sev = nr.get("min_severity", "medium")
        if sev not in C.SEVERITIES:
            raise AgentError(f"通知级别只能是 {', '.join(C.SEVERITIES)}")
        f["notify_rules"] = {"min_severity": sev, "on_failure": bool(nr.get("on_failure", True)), "on_completion": bool(nr.get("on_completion", False))}
    if "wake_on" in body:
        w = body.get("wake_on")
        if w is not None:
            if not isinstance(w, list) or not all(isinstance(x, str) and WAKE_TYPE.fullmatch(x) for x in w):
                raise AgentError("wake_on 必须是事件类型列表，例如 [\"app.error\", \"bsc.*\"]")
            w = list(dict.fromkeys(w))[:30]
        f["wake_on"] = w
    if "template" in body:
        t = str(body.get("template") or "")
        f["template"] = t if t in TEMPLATE_BY_ID else ""
    return f


def _money(v: Any, what: str) -> str | None:
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise AgentError(f"{what} 格式不对：{v}") from None
    if not d.is_finite() or d < 0:
        raise AgentError(f"{what} 不能是负数")
    return str(d)


def has_workspace(agent: dict, wid: str) -> bool:
    return wid in (agent.get("workspace_ids") or [])


def holds(agent: dict, perms: tuple[str, ...] | list[str]) -> list[str]:
    """The permissions in `perms` the agent does not hold."""
    have = set(agent.get("permissions") or [])
    return [p for p in perms if p not in have]


# ---------------------------------------------------------------- state


def agent_state(db: AutonomyDB, agent: dict, *, worker_alive: bool, watched: set[str] | None = None) -> dict:
    """The agent's state as the backend sees it right now. `watched`: agent ids covered by a healthy watcher."""
    aid = agent["id"]
    if agent["status"] == "archived":
        return {"state": C.A_ARCHIVED, "detail": "已归档"}
    if agent["status"] == "paused":
        return {"state": C.A_PAUSED, "detail": "已暂停：不会被唤醒，排队的任务等恢复后再执行"}
    running = db.tasks(agent_id=aid, status=[C.T_RUNNING], limit=5, order="started_at")
    queued = db.count_tasks(agent_id=aid, status=[C.T_QUEUED])
    if not worker_alive:
        n = len(running) + queued
        return {"state": C.A_OFFLINE, "detail": "没有在线的 Worker，Agent 现在不能执行" + (f"（{n} 个任务在等）" if n else "")}
    legacy = db.wdb.running_subtasks(agent=aid)
    if running:
        t = running[0]
        st = C.A_INVESTIGATING if t["kind"] == "investigate" else C.A_RUNNING
        return {"state": st, "detail": t["objective"][:160], "task_id": t["id"]}
    if legacy:
        return {"state": C.A_RUNNING, "detail": f"执行旧流程的 {legacy} 个子任务"}
    if db.approvals(status="pending", agent_id=aid, limit=1):
        return {"state": C.A_WAITING_APPROVAL, "detail": "等待人工审批"}
    if queued:
        return {"state": C.A_QUEUED, "detail": f"{queued} 个任务排队中"}
    waiting = db.tasks(agent_id=aid, status=[C.T_WAITING], limit=1)
    if waiting:
        return {"state": C.A_WAITING, "detail": "等待其他 Agent 返回结果", "task_id": waiting[0]["id"]}
    last = db.last_run(aid)
    if last and last["status"] == C.R_FAILED and (parse(last["finished_at"]) or now_dt()) > now_dt() - timedelta(hours=24):
        return {"state": C.A_ERROR, "detail": (last.get("error") or "上一次执行失败")[:200], "task_id": last["task_id"]}
    if not agent.get("autonomous"):
        return {"state": C.A_IDLE, "detail": "没有设置长期职责（旧版员工）"}
    if watched is not None and aid in watched:
        return {"state": C.A_WATCHING, "detail": "监控组件正常运行，等待事件"}
    if agent.get("work_mode") == "scheduled" and agent.get("schedule_cron"):
        try:
            nxt = Cron.parse(agent["schedule_cron"]).next_after(now_dt())
            return {"state": C.A_WAITING, "detail": f"下一次定时执行 {nxt.strftime('%m-%d %H:%M')} UTC"}
        except ValueError:
            pass
    if agent.get("wake_on") == []:
        return {"state": C.A_WAITING, "detail": "等待其他 Agent 或你交给它任务"}
    return {"state": C.A_WAITING, "detail": "可以被事件或任务唤醒，目前没有工作"}


# ---------------------------------------------------------------- matching


WORD = re.compile(r"[a-z0-9]+|[一-鿿]")
KIND_WORDS = {
    "investigate": ("调查", "分析", "原因", "investigat", "analy", "root", "cause", "diagnos"),
    "analyze": ("分析", "analy", "research", "调研"),
    "fix": ("修复", "修改", "fix", "patch", "repair", "代码"),
    "test": ("测试", "test"),
    "verify": ("验证", "监控", "verify", "monitor", "检查"),
    "review": ("审", "review"),
    "report": ("报告", "汇报", "文档", "report", "doc"),
}


def _terms(text: str) -> set[str]:
    t = WORD.findall(text.lower())
    out = {w for w in t if len(w) > 2}
    cjk = [w for w in t if "一" <= w <= "鿿"]
    out |= {a + b for a, b in zip(cjk, cjk[1:])}
    return out


def relevance(agent: dict, kind: str, objective: str) -> float:
    resp = f"{agent.get('name', '')} {agent.get('role', '')} {agent.get('responsibility', '')}".lower()
    score = 0.0
    for w in KIND_WORDS.get(kind, ()):
        if w in resp:
            score += 2.0
    overlap = _terms(objective) & _terms(resp)
    score += min(len(overlap), 6) * 0.5
    return score


def match(db: AutonomyDB, *, workspace_id: str, kind: str, objective: str, required: tuple[str, ...], exclude: set[str]) -> tuple[dict | None, list[dict]]:
    """Pick the agent that should get a task. Returns (agent or None, the candidates considered with why each was or
    was not eligible), so the console can show the reasoning and nothing is handed over silently."""
    considered: list[dict] = []
    best, best_key = None, None
    for a in db.agents():
        why = None
        if a["id"] in exclude:
            why = "是发起方或已在这条交接链上（防止循环）"
        elif not a.get("autonomous"):
            why = "没有长期职责（旧版员工）"
        elif a["status"] != "active":
            why = f"状态是 {a['status']}"
        elif not has_workspace(a, workspace_id):
            why = "没有这个工作区的权限"
        else:
            missing = holds(a, required)
            if missing:
                why = "缺少权限：" + "、".join(missing)
        rel = relevance(a, kind, objective)
        if why is None and not required and rel <= 0:
            why = "职责和任务不相关"
        load = db.count_tasks(agent_id=a["id"], status=[C.T_QUEUED, C.T_RUNNING, C.T_WAITING])
        considered.append({"agent_id": a["id"], "name": a["name"], "eligible": why is None, "reason": why or "", "relevance": rel, "load": load})
        if why is None:
            key = (-rel, load, a["created_at"])
            if best_key is None or key < best_key:
                best, best_key = a, key
    return best, considered
