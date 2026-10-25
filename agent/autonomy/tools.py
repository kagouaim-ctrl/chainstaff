"""Tool Registry for autonomous agents.

A tool is offered to an agent's model only if the agent holds the tool's permission (and, for chain skills, has the
skill), and only if the mission does not restrict it. Every call is checked again on the server before it runs,
so a model naming a tool it was not offered gets an error, not a result. Tool results are data, never instructions.

Collaboration tools (complete, reject, delegate, notify, recall) are how the run ends or hands work on: the engine
turns them into structured tasks and state changes, not into chat messages.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit

from . import constants as C
from .sandbox import SandboxError
from .workspaces import repo as ws_repo

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import RunContext


class ToolError(Exception):
    """The tool could not do what was asked; the message goes back to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[["RunContext", dict], Any]
    permission: str | None = None   # None: every autonomous agent has it
    risk: str = "low"               # low | medium | high
    group: str = "collaboration"
    always: bool = False            # offered even when the mission restricts permissions

    def param(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def public(self) -> dict:
        return {"name": self.name, "description": self.description, "permission": self.permission, "risk": self.risk, "group": self.group,
                "input_schema": self.schema}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}

# ---------------------------------------------------------------- collaboration


def _complete(ctx: "RunContext", a: dict) -> dict:
    summary = str(a.get("summary") or "").strip()
    if not summary:
        raise ToolError("summary 不能为空")
    outcome = a.get("outcome") or "resolved"
    if outcome not in ("resolved", "no_issue", "needs_human", "partial"):
        raise ToolError("outcome 只能是 resolved / no_issue / needs_human / partial")
    ctx.finish = {"action": "complete", "summary": summary[:4000], "outcome": outcome, "details": a.get("details") or {},
                  "findings": a.get("findings") or []}
    return {"ok": True, "note": "任务会在这一轮结束后标记为完成。"}


def _reject(ctx: "RunContext", a: dict) -> dict:
    reason = str(a.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason 不能为空")
    if ctx.delegated:
        raise ToolError("这一轮已经分配了子任务，不能再拒绝这个任务")
    ctx.finish = {"action": "reject", "reason": reason[:1000]}
    return {"ok": True}


def _delegate(ctx: "RunContext", a: dict) -> dict:
    from .wakeup import TaskError

    if "delegate" not in ctx.agent["permissions"]:
        raise ToolError("你没有把任务交给其他 Agent 的权限（delegate）")
    wait = bool(a.get("wait_for_result", True))
    try:
        t, info = ctx.svc.wakeup.create_task(
            workspace_id=ctx.task["workspace_id"], objective=str(a.get("objective") or ""), kind=a.get("kind") or "general",
            priority=a.get("priority", ctx.task["priority"]), evidence=a.get("evidence"), to_agent_id=a.get("to_agent_id") or None,
            from_agent_id=ctx.agent["id"], mission_id=ctx.task["mission_id"], parent=ctx.task, event_id=ctx.task["event_id"],
            fingerprint=ctx.task["fingerprint"], required=a.get("required_permissions") or None, follow_up=bool(a.get("follow_up", False)) and not wait,
            deadline_s=int(a["deadline_minutes"]) * 60 if a.get("deadline_minutes") else None, created_by=ctx.agent["id"],
            budget_usd=a.get("budget_usd"))
    except TaskError as e:
        raise ToolError(f"交接没有成功：{e}") from None
    if wait and t["status"] not in C.TASK_FINAL:
        ctx.waiting_on.append(t["id"])
    ctx.delegated.append(t["id"])
    to = ctx.svc.db.agent(t["to_agent_id"]) if t["to_agent_id"] else None
    return {"task_id": t["id"], "status": t["status"], "assigned_to": {"id": to["id"], "name": to["name"]} if to else None,
            "deduplicated": info["deduplicated"],
            "note": ("没有合适的 Agent，已创建待分配任务并通知用户。" if not to else
                     ("这个任务已经存在，没有重复创建。" if info["deduplicated"] else "")) +
                    (" 你会在它结束后被重新唤醒并收到结果；这一轮结束时不要调用 complete_task。" if wait and t["status"] not in C.TASK_FINAL else "")}


def _notify(ctx: "RunContext", a: dict) -> dict:
    ctx.notes += 1
    if ctx.notes > 3:
        raise ToolError("这一轮已经发了 3 条通知")
    sev = a.get("severity") or "medium"
    if sev not in C.SEVERITIES:
        raise ToolError(f"severity 只能是 {', '.join(C.SEVERITIES)}")
    nid = ctx.svc.activity.notify(str(a.get("title") or "")[:200] or "Agent 通知", str(a.get("body") or "")[:2000], severity=sev,
                                  agent_id=ctx.agent["id"], workspace_id=ctx.task["workspace_id"], task_id=ctx.task["id"], mission_id=ctx.task["mission_id"])
    ctx.svc.activity.log("alert.triggered", f"{ctx.agent['name']} 通知用户：{str(a.get('title') or '')[:120]}", severity=sev, workspace_id=ctx.task["workspace_id"],
                         agent_id=ctx.agent["id"], task_id=ctx.task["id"], run_id=ctx.run_id, actor=ctx.agent["id"], needs_human=sev in ("high", "critical"),
                         reason=str(a.get("body") or "")[:500])
    return {"ok": True, "notification_id": nid, "note": None if nid else "按这个 Agent 的通知规则，这个级别不会推送给用户，已写入活动记录"}


def _remember(ctx: "RunContext", a: dict) -> dict:
    scope = a.get("scope") or "agent"
    if scope not in ("agent", "workspace"):
        raise ToolError("scope 只能是 agent 或 workspace")
    text = str(a.get("text") or "").strip()
    if not text:
        raise ToolError("text 不能为空")
    owner = ctx.agent["id"] if scope == "agent" else ctx.task["workspace_id"]
    mid = ctx.svc.db.wdb.remember(scope, owner, text[:4000], source=f"{ctx.agent['id']}:{ctx.task['id']}", ttl_days=a.get("ttl_days"))
    return {"ok": True, "memory_id": mid}


def _recall(ctx: "RunContext", a: dict) -> dict:
    rows = ctx.svc.db.wdb.search_memory([("agent", ctx.agent["id"]), ("workspace", ctx.task["workspace_id"])], str(a.get("query") or ""), limit=10)
    return {"memories": [{"id": r["id"], "scope": r["scope"], "text": r["text"], "at": r["created_at"]} for r in rows]}


def _get_event(ctx: "RunContext", a: dict) -> dict:
    e = ctx.svc.db.event(str(a.get("event_id") or ""))
    if not e or e["workspace_id"] != ctx.task["workspace_id"]:
        raise ToolError("这个工作区里没有这个事件")
    return {k: e[k] for k in ("id", "source", "type", "severity", "title", "payload", "fingerprint", "occurrences", "occurred_at", "chain_status")}


def _list_events(ctx: "RunContext", a: dict) -> dict:
    rows = ctx.svc.db.events(workspace_id=ctx.task["workspace_id"], type_=a.get("type") or None, limit=min(int(a.get("limit") or 20), 50))
    return {"events": [{k: e[k] for k in ("id", "type", "severity", "title", "fingerprint", "occurrences", "occurred_at", "processing_status")} for e in rows]}


def _get_task(ctx: "RunContext", a: dict) -> dict:
    t = ctx.svc.db.task(str(a.get("task_id") or ""))
    if not t or t["workspace_id"] != ctx.task["workspace_id"]:
        raise ToolError("这个工作区里没有这个任务")
    return {k: t[k] for k in ("id", "kind", "objective", "status", "from_agent_id", "to_agent_id", "evidence", "result", "error", "created_at", "finished_at")}


# ---------------------------------------------------------------- code (isolated copy)


def _copy(ctx: "RunContext", name: str | None = None):
    repo = ws_repo(ctx.workspace, name)
    if repo is None:
        raise ToolError("这个工作区没有登记仓库" if not name else f"这个工作区没有仓库 {name}")
    try:
        return ctx.repo_copy(repo)
    except SandboxError as e:
        raise ToolError(f"准备隔离副本失败：{e}") from None


def _wrap(fn: Callable[..., Any]) -> Callable[["RunContext", dict], Any]:
    def run(ctx: "RunContext", a: dict) -> Any:
        try:
            return fn(ctx, a)
        except SandboxError as e:
            raise ToolError(str(e)) from None
    return run


def _repo_list(ctx, a):
    c = _copy(ctx, a.get("repo"))
    return {"repo": c.repo["name"], "files": c.list(a.get("path") or ".", int(a.get("depth") or 2))}


def _repo_read(ctx, a):
    return _copy(ctx, a.get("repo")).read(str(a.get("path") or ""), a.get("start"), a.get("end"))


def _repo_search(ctx, a):
    return {"hits": _copy(ctx, a.get("repo")).search(str(a.get("pattern") or ""), a.get("path") or ".")}


def _repo_write(ctx, a):
    out = _copy(ctx, a.get("repo")).write(str(a.get("path") or ""), str(a.get("content") or ""))
    ctx.changed = True
    return out


def _repo_replace(ctx, a):
    out = _copy(ctx, a.get("repo")).replace(str(a.get("path") or ""), str(a.get("old") or ""), str(a.get("new") or ""))
    ctx.changed = True
    return out


def _run_tests(ctx, a):
    c = _copy(ctx, a.get("repo"))
    r = c.run_tests()
    ctx.last_tests = r
    ctx.svc.activity.log("tests.passed" if r["passed"] else "tests.failed",
                         f"{ctx.agent['name']} 运行测试：{'通过' if r['passed'] else '失败'}（exit {r['exit_code']}，{r['secs']}s）",
                         severity="info" if r["passed"] else "low", workspace_id=ctx.task["workspace_id"], agent_id=ctx.agent["id"], task_id=ctx.task["id"],
                         run_id=ctx.run_id, mission_id=ctx.task["mission_id"], actor=ctx.agent["id"], result=r["output"][-600:],
                         data={"command": r["command"], "exit_code": r["exit_code"], "network_isolated": r["network_isolated"]})
    return r


def _submit_patch(ctx, a):
    c = _copy(ctx, a.get("repo"))
    d = c.diff()
    if not d["files"]:
        raise ToolError("隔离副本里没有任何改动，没有可提交的补丁")
    title = str(a.get("title") or "").strip()[:200] or f"Fix: {ctx.task['objective'][:80]}"
    desc = str(a.get("description") or "").strip()[:8000]
    tests = ctx.last_tests
    return ctx.svc.submit_patch(ctx, c, title=title, description=desc, diff=d, tests=tests)


# ---------------------------------------------------------------- http


def _http_check(ctx, a):
    eps = (ctx.workspace.get("resources") or {}).get("endpoints") or []
    name = str(a.get("endpoint") or "")
    ep = next((e for e in eps if e["name"] == name), None)
    if ep is None:
        raise ToolError(f"工作区里没有登记这个 API 地址：{name}（可用：{', '.join(e['name'] for e in eps) or '无'}）")
    return ctx.svc.http_check(ep["url"])


# ---------------------------------------------------------------- chain observations


def _token_flows(ctx, a):
    ids = [w["id"] for w in ctx.svc.db.watchers(workspace_id=ctx.task["workspace_id"], kind="bsc_events")]
    addr = a.get("address")
    rows = ctx.svc.db.flows(ids, addr, limit=min(int(a.get("limit") or 50), 200))
    return {"observed_since": "the watcher started (not the full chain history)", "flows": rows,
            "caution": "A transfer between two wallets is an observable fact, not evidence of wrongdoing or common ownership."}


def _token_holders(ctx, a):
    ws = ctx.svc.db.watchers(workspace_id=ctx.task["workspace_id"], kind="bsc_events")
    out = []
    for w in ws:
        out.append({"watcher_id": w["id"], "token": (w["config"] or {}).get("address"), "holders_seen": ctx.svc.db.holder_count(w["id"]),
                    "top": ctx.svc.db.holders(w["id"], limit=min(int(a.get("limit") or 20), 100))})
    return {"note": "Balances are balanceOf reads at the block of the last transfer the watcher saw for each address.", "tokens": out}


TOOLS: list[Tool] = [
    Tool("complete_task", "Finish your task with a structured result. Call it once, when the work is done (or there is nothing to do).",
         _obj({"summary": S, "outcome": {"type": "string", "enum": ["resolved", "no_issue", "needs_human", "partial"]},
               "details": {"type": "object"}, "findings": {"type": "array", "items": {"type": "object"}}}, ["summary"]), _complete, always=True),
    Tool("reject_task", "Refuse this task because it is outside your responsibility or your permissions. The system will look for another agent.",
         _obj({"reason": S}, ["reason"]), _reject, always=True),
    Tool("delegate_task", "Hand a structured task to another agent (or let the system pick one). Use for work you should not or cannot do yourself.",
         _obj({"objective": S, "kind": {"type": "string", "enum": list(C.TASK_KINDS)}, "priority": {"type": "integer", "minimum": 0, "maximum": 3},
               "evidence": {"type": "object"}, "to_agent_id": S, "required_permissions": {"type": "array", "items": S},
               "wait_for_result": {"type": "boolean"}, "follow_up": {"type": "boolean"}, "deadline_minutes": I}, ["objective", "kind"]),
         _delegate, permission="delegate"),
    Tool("notify_user", "Tell the user about something that needs their attention (at most 3 per run).",
         _obj({"title": S, "body": S, "severity": {"type": "string", "enum": list(C.SEVERITIES)}}, ["title"]), _notify, always=True),
    Tool("remember", "Save a fact to long-term memory (your own, or shared with the workspace).",
         _obj({"text": S, "scope": {"type": "string", "enum": ["agent", "workspace"]}, "ttl_days": I}, ["text"]), _remember, permission="memory:write"),
    Tool("recall", "Search your memory and the workspace memory.", _obj({"query": S}, ["query"]), _recall, always=True),
    Tool("get_event", "Read one event of this workspace (the evidence a watcher recorded).", _obj({"event_id": S}, ["event_id"]), _get_event, always=True, group="evidence"),
    Tool("list_events", "Recent events of this workspace.", _obj({"type": S, "limit": I}), _list_events, always=True, group="evidence"),
    Tool("get_task", "Read another task of this workspace (objective, evidence, result).", _obj({"task_id": S}, ["task_id"]), _get_task, always=True, group="evidence"),
    Tool("repo_list", "List files in your isolated copy of the workspace repository.", _obj({"path": S, "depth": I, "repo": S}), _wrap(_repo_list),
         permission="code:read", group="code"),
    Tool("repo_read", "Read a file (with line numbers) from your isolated copy.", _obj({"path": S, "start": I, "end": I, "repo": S}, ["path"]),
         _wrap(_repo_read), permission="code:read", group="code"),
    Tool("repo_search", "Search the isolated copy with a regular expression.", _obj({"pattern": S, "path": S, "repo": S}, ["pattern"]),
         _wrap(_repo_search), permission="code:read", group="code"),
    Tool("repo_write", "Write a whole file in your isolated copy (never the original repository).", _obj({"path": S, "content": S, "repo": S}, ["path", "content"]),
         _wrap(_repo_write), permission="code:write", risk="medium", group="code"),
    Tool("repo_replace", "Replace one exact occurrence of text in a file of your isolated copy.", _obj({"path": S, "old": S, "new": S, "repo": S}, ["path", "old", "new"]),
         _wrap(_repo_replace), permission="code:write", risk="medium", group="code"),
    Tool("run_tests", "Run the repository's configured test command in your isolated copy (no network, no secrets, time limit).",
         _obj({"repo": S}), _wrap(_run_tests), permission="code:test", risk="medium", group="code"),
    Tool("submit_patch", "Submit the changes in your isolated copy as a patch for human review (and a draft PR when the workspace allows it).",
         _obj({"title": S, "description": S, "repo": S}, ["title"]), _wrap(_submit_patch), permission="code:propose", risk="medium", group="code"),
    Tool("http_check", "GET one API endpoint registered in the workspace and report status, latency and a short body preview.",
         _obj({"endpoint": S}, ["endpoint"]), _http_check, permission="net:http", group="http"),
    Tool("token_flows", "Transfers between addresses the workspace's BSC watchers have observed (counts and totals).", _obj({"address": S, "limit": I}),
         _token_flows, permission="chain:read", group="chain"),
    Tool("token_holders", "Holders the workspace's BSC watchers have seen, with balanceOf at the last observed transfer.", _obj({"limit": I}),
         _token_holders, permission="chain:read", group="chain"),
]
TOOL_BY_NAME = {t.name: t for t in TOOLS}


def offered(svc, agent: dict, mission: dict | None) -> tuple[list[Tool], list[Any]]:
    """(autonomy tools, chain skills) this agent may use in this mission."""
    perms = set(agent.get("permissions") or [])
    limit = set((mission or {}).get("permissions") or [])
    tools = [t for t in TOOLS if (t.permission is None or t.permission in perms) and (t.always or not limit or t.permission is None or t.permission in limit)]
    skills = []
    wf = getattr(svc, "wf", None)
    if wf is not None:
        from agent.workforce.skills import SKILL_BY_ID

        for sid in agent.get("skills") or []:
            s = SKILL_BY_ID.get(sid)
            if s and s.agent_tool and wf.can_run(agent, sid) is None and (not limit or all(p in limit for p in s.permissions)):
                skills.append(s)
    return tools, skills


def call_chain_skill(svc, skill, args: dict, deadline_s: float | None = None) -> Any:
    from agent.workforce.skills import SkillContext, validate

    wf = svc.wf
    validate(skill.input_schema, args)
    ctx = SkillContext(wf.reader.fork(None) if wf.reader else None, wf.etherscan, wf.tokens, [], events=wf.events,
                       deadline=time.monotonic() + (deadline_s or skill.timeout_s))
    return skill.run(ctx, dict(args))


def describe_endpoint(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def dumps(v: Any, n: int) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"…（已截断，原长 {len(s)} 字符）"
