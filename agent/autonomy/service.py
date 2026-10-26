"""The autonomy services wired together, for the API process and for the worker.

    API server ──writes──▶ agents, missions, watchers, tasks, events          (never runs an agent)
    Worker     ──claims──▶ tasks; runs agents; processes events; polls health; enforces deadlines; recovers leases

Both build this object over the same database. The API process also holds the BSC listener (agent/bsc), whose
monitors feed bsc_events watchers.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from agent.workforce import Workforce
from agent.workforce.engine import WorkforceError

from . import constants as C
from .activity import Activity
from .agents import TEMPLATE_BY_ID, AgentError, clean_autonomy
from .db import AutonomyDB, iso, now_dt, parse
from .events import Events
from .github import GitHub, GitHubError
from .missions import Missions
from .runtime import AgentRunner
from .sandbox import Sandbox, SandboxError, apply_to_local
from .wakeup import Wakeup
from .watchers import Watchers, check_url
from .workspaces import clean as clean_resources
from .workspaces import repo as ws_repo

BASE_KEYS = ("name", "role", "description", "system_prompt", "provider", "model", "model_config", "skills", "permissions", "budget_usd", "max_concurrency")


class AutonomyError(ValueError):
    pass


class Autonomy:
    def __init__(self, wf: Workforce, *, env: dict | None = None, data_dir: str | Path | None = None, http_get=None, github_post=None,
                 providers: dict | None = None, network_isolation: bool | None = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.wf = wf
        self.store = wf.store
        self.db = AutonomyDB(wf.store, wf.db)
        self.limits = C.Limits(self.env)
        self.activity = Activity(self.db)
        self.wakeup = Wakeup(self.db, self.activity, self.limits)
        self.missions = Missions(self.db, self.activity, self.wakeup)
        self.events = Events(self.db, self.activity, self.missions, self.wakeup, self.limits, reader=wf.reader)
        self.watchers = Watchers(self.db, self.activity, self.events, env=self.env, get=http_get)
        root = Path(data_dir or self.env.get("KAGOU_DATA_DIR") or (Path(wf.store.path).parent if wf.store.path not in ("", ":memory:")
                                                                   else Path(tempfile.gettempdir()) / "kagou"))
        self.sandbox = Sandbox(root / "sandboxes", github_token=self.env.get("KAGOU_GITHUB_TOKEN") or None, network_isolation=network_isolation)
        token = self.env.get("KAGOU_GITHUB_TOKEN")
        self.github = GitHub(token, post=github_post) if token else None
        self.providers = providers if providers is not None else dict(wf.runtime.providers)
        self.default_model = wf.planner.model
        self.runner = AgentRunner(self)

    # ------------------------------------------------------------------ helpers

    def name(self, aid: str | None) -> str:
        if not aid:
            return "System"
        if aid in ("user", "console"):
            return "User"
        if aid.startswith("wch_"):
            w = self.db.watcher(aid)
            return f"Watcher {w['name']}" if w else aid
        if aid == "system":
            return "System"
        a = self.db.agent(aid)
        return a["name"] if a else aid

    def worker_alive(self) -> bool:
        for w in self.db.workers(5):
            hb = parse(w["heartbeat_at"])
            if w["status"] == "running" and hb and (now_dt() - hb).total_seconds() <= self.limits.worker_stale_s:
                return True
        return False

    def model_status(self) -> dict:
        provs = []
        for name, p in self.providers.items():
            provs.append({"provider": name, "model": getattr(p, "model", None) or self.default_model, "label": getattr(p, "label", name)})
        return {"configured": bool(provs), "providers": provs, "default_model": self.default_model}

    # ------------------------------------------------------------------ agents

    def _default_provider(self) -> tuple[str, str]:
        if "anthropic" in self.providers:
            return "anthropic", self.default_model
        if "openai_compat" in self.providers:
            return "openai_compat", getattr(self.providers["openai_compat"], "model", "")
        # Nothing configured yet: the agent is saved with the default Claude model and says it cannot run until a key is set.
        return "anthropic", self.default_model

    def create_agent(self, body: dict, by: str) -> dict:
        tpl = TEMPLATE_BY_ID.get(str(body.get("template") or ""))
        base = {k: body[k] for k in BASE_KEYS if k in body}
        if tpl:
            base.setdefault("permissions", list(dict.fromkeys([*tpl["permissions"], *tpl.get("extra", [])])))
            base.setdefault("skills", tpl["skills"])
            body = {"work_mode": tpl["work_mode"], "responsibility": tpl["responsibility"], **({"schedule_cron": tpl["schedule_cron"]} if tpl.get("schedule_cron") else {}),
                    **({"wake_on": tpl["wake_on"]} if "wake_on" in tpl else {}), **body}
        base.setdefault("permissions", list(C.DEFAULT_PERMISSIONS))
        base.setdefault("max_concurrency", self.limits.agent_concurrency)
        if not base.get("provider"):
            base["provider"], base["model"] = self._default_provider()
        try:
            auto = clean_autonomy(body, self.db, need_responsibility=bool(body.get("activate")))
            auto.setdefault("workspace_ids", [])
            if not auto.get("approval_rules"):
                auto["approval_rules"] = {"patch": "always", "delegate_outside_mission": False, "max_task_budget_usd": None}
            if not auto.get("notify_rules"):
                auto["notify_rules"] = {"min_severity": "medium", "on_failure": True, "on_completion": False}
            if body.get("activate") and auto.get("work_mode") != "on_demand" and not auto["workspace_ids"]:
                raise AgentError("激活前请绑定至少一个 Workspace（On-demand 模式除外）")
            a = self.wf.create_agent(base, by)
        except (AgentError, WorkforceError) as e:
            raise AutonomyError(str(e)) from None
        a = self.db.update_agent(a["id"], autonomous=1, **auto)
        self.activity.log("agent.created", f"{a['name']} 已创建", agent_id=a["id"], actor=by,
                          reason=(a.get("responsibility") or "")[:300], data={"template": auto.get("template") or None, "work_mode": a["work_mode"]})
        if body.get("activate"):
            a = self.activate(a["id"], by)["agent"]
        return a

    def update_agent(self, aid: str, body: dict, by: str) -> dict:
        a = self.agent_or_raise(aid)
        base = {k: body[k] for k in BASE_KEYS if k in body}
        try:
            auto = clean_autonomy(body, self.db, partial=True)
            if base:
                self.wf.update_agent(aid, base, by)
        except (AgentError, WorkforceError) as e:
            raise AutonomyError(str(e)) from None
        a = self.db.update_agent(aid, **auto) if auto else self.db.agent(aid)
        if a.get("activated_at") and a["status"] != "archived":
            self.missions.ensure_responsibility(a, by)
        self.activity.log("agent.updated", f"{a['name']} 设置已修改：{', '.join([*base, *auto]) or '无变化'}", agent_id=aid, actor=by)
        return a

    def activate(self, aid: str, by: str) -> dict:
        a = self.agent_or_raise(aid)
        if a["status"] == "archived":
            raise AutonomyError("已归档的 Agent 不能激活")
        if not (a.get("responsibility") or "").strip():
            raise AutonomyError("激活前请先写下工作职责")
        if a.get("work_mode") != "on_demand" and not a.get("workspace_ids"):
            raise AutonomyError("激活前请绑定至少一个 Workspace（On-demand 模式除外）")
        warnings = []
        if a.get("provider") not in self.providers:
            warnings.append(f"服务器没有配置 {a.get('provider')} 的 API Key：Agent 被唤醒时会执行失败并通知你，配置 Key 后自动恢复")
        if not self.worker_alive():
            warnings.append("现在没有在线的 Worker：任务会排队，Worker 启动后执行")
        a = self.db.update_agent(aid, autonomous=1, activated_at=a.get("activated_at") or iso(now_dt()))
        if a["status"] != "active":
            self.wf.set_agent_status(aid, "active", by)
            a = self.db.agent(aid)
        missions = self.missions.ensure_responsibility(a, by)
        self.activity.log("agent.activated", f"{a['name']} 已激活（{C.WORK_MODES[a['work_mode']].split('：')[0]}）", agent_id=aid, actor=by,
                          reason="；".join(warnings), data={"missions": [m["id"] for m in missions]}, severity="medium" if warnings else "info")
        return {"agent": a, "missions": missions, "warnings": warnings}

    def set_agent_status(self, aid: str, action: str, by: str) -> dict:
        a = self.agent_or_raise(aid)
        to = {"pause": "paused", "resume": "active", "archive": "archived"}.get(action)
        if to is None:
            raise AutonomyError("操作只能是 pause / resume / archive")
        if to == "archived" and self.db.count_tasks(agent_id=aid, status=[C.T_RUNNING, C.T_WAITING]):
            raise AutonomyError("这个 Agent 还有进行中的任务，等它们结束或取消后再归档")
        try:
            a = self.wf.set_agent_status(aid, to, by)
        except WorkforceError as e:
            raise AutonomyError(str(e)) from None
        if to == "archived":
            for m in self.db.missions(responsibility_of=aid, status=[C.M_ACTIVE, C.M_PAUSED], limit=100):
                self.missions.set_status(m["id"], "stop", by)
            for t in self.db.tasks(agent_id=aid, status=[C.T_QUEUED], limit=500):
                self.db.transition(t["id"], (C.T_QUEUED,), C.T_UNASSIGNED, to_agent_id=None)
        else:
            self.missions.pause_responsibility(aid, to == "paused")
        self.activity.log({"pause": "agent.paused", "resume": "agent.resumed", "archive": "agent.archived"}[action],
                          f"{a['name']} {dict(pause='已暂停', resume='已恢复', archive='已归档')[action]}", agent_id=aid, actor=by)
        return self.db.agent(aid)

    def give_task(self, aid: str, body: dict, by: str) -> dict:
        a = self.agent_or_raise(aid)
        if not a.get("autonomous"):
            raise AutonomyError("这个 Agent 没有设置长期职责，不能接收自主任务")
        wid = str(body.get("workspace_id") or (a.get("workspace_ids") or [None])[0] or "")
        from .wakeup import TaskError

        try:
            t, info = self.wakeup.create_task(workspace_id=wid, objective=str(body.get("objective") or ""), kind=body.get("kind") or "general",
                                              priority=body.get("priority", 2), evidence=body.get("evidence"), to_agent_id=aid, mission_id=body.get("mission_id"),
                                              budget_usd=body.get("budget_usd"), created_by=by, reason="用户直接交给这个 Agent",
                                              deadline_s=int(body["deadline_minutes"]) * 60 if body.get("deadline_minutes") else None)
        except TaskError as e:
            raise AutonomyError(str(e)) from None
        return {**t, "deduplicated": info["deduplicated"]}

    def agent_or_raise(self, aid: str) -> dict:
        a = self.db.agent(aid)
        if a is None:
            raise AutonomyError(f"没有 Agent {aid}")
        return a

    # ------------------------------------------------------------------ workspaces

    def create_workspace(self, body: dict, by: str) -> dict:
        try:
            res = clean_resources(body.get("resources"), self.env)
            ws = self.wf.create_workspace({"name": body.get("name"), "description": body.get("description", "")}, by)
        except (ValueError, WorkforceError) as e:
            raise AutonomyError(str(e)) from None
        self.db.set_resources(ws["id"], res)
        self.activity.audit(f"建立工作区 {ws['name']}", by, {"workspace": ws["id"]})
        return self.db.workspace(ws["id"])

    def update_workspace(self, wid: str, body: dict, by: str) -> dict:
        ws = self.db.workspace(wid)
        if ws is None:
            raise AutonomyError(f"没有工作区 {wid}")
        try:
            if "resources" in body:
                self.db.set_resources(wid, clean_resources(body["resources"], self.env))
            rest = {k: body[k] for k in ("name", "description", "archived") if k in body}
            if rest:
                self.wf.update_workspace(wid, rest, by)
        except (ValueError, WorkforceError) as e:
            raise AutonomyError(str(e)) from None
        self.activity.audit(f"修改工作区 {ws['name']}：{', '.join(body)}", by, {"workspace": wid})
        return self.db.workspace(wid)

    # ------------------------------------------------------------------ patches and approvals

    def submit_patch(self, ctx, copy, *, title: str, description: str, diff: dict, tests: dict | None) -> dict:
        t, a = ctx.task, ctx.agent
        repo = copy.repo
        meta: dict[str, Any] = {"repo": repo["name"], "provider": repo["provider"], "files": diff["files"], "stat": diff["stat"], "base": diff["base"],
                                "tests": {k: tests[k] for k in ("command", "exit_code", "passed", "secs", "network_isolated")} if tests else None}
        status = "pending_review"
        pr = None
        if repo["provider"] == "github" and "github:draft_pr" in (a.get("permissions") or []):
            if self.github is None:
                meta["pr_error"] = "没有配置 KAGOU_GITHUB_TOKEN，补丁只保存在 KAGOU 里等待审核"
            else:
                branch = f"kagou/{t['id']}"
                try:
                    copy.commit_branch(branch, title)
                    copy.push(branch)
                    body = (f"{description}\n\n---\nOpened by KAGOU agent **{a['name']}** for task `{t['id']}` (draft, needs human review).\n"
                            f"Tests in the isolated copy: {'passed' if tests and tests['passed'] else ('failed' if tests else 'not run')}.")
                    pr = self.github.draft_pr(repo["owner"], repo["repo"], head=branch, base=repo["branch"], title=title, body=body)
                    meta.update({"branch": branch, "pr_url": pr["url"], "pr_number": pr["number"]})
                    status = "pr_open"
                except (SandboxError, GitHubError) as e:
                    meta["pr_error"] = str(e)[:500]
        art = self.db.add_artifact(task_id=t["id"], run_id=ctx.run_id, workspace_id=t["workspace_id"], agent_id=a["id"], kind="patch", title=title,
                                   body=description, content=diff["diff"][:400_000], meta=meta, status=status)
        ctx.artifacts.append(art["id"])
        if pr:
            self.activity.log("pr.created", f"{a['name']} 开了 Draft PR #{pr['number']}：{title[:100]}", workspace_id=t["workspace_id"], agent_id=a["id"],
                              task_id=t["id"], run_id=ctx.run_id, mission_id=t["mission_id"], actor=a["id"], result=pr["url"] or "", data={"artifact_id": art["id"]})
        else:
            self.activity.log("patch.submitted", f"{a['name']} 提交了待审核补丁：{title[:100]}", workspace_id=t["workspace_id"], agent_id=a["id"], task_id=t["id"],
                              run_id=ctx.run_id, mission_id=t["mission_id"], actor=a["id"], result=diff["stat"],
                              data={"artifact_id": art["id"], "files": diff["files"], "pr_error": meta.get("pr_error")})
        what = (f"审核 Draft PR #{pr['number']}" if pr else ("应用补丁到本地仓库" if repo["provider"] == "local" else "审核补丁"))
        ap = self.db.add_approval(workspace_id=t["workspace_id"], task_id=t["id"], agent_id=a["id"], kind="patch", title=f"{what}：{title[:120]}",
                                  detail={"repo": repo["name"], "provider": repo["provider"], "files": diff["files"], "stat": diff["stat"],
                                          "tests_passed": tests["passed"] if tests else None, "pr_url": meta.get("pr_url")}, artifact_id=art["id"])
        self.activity.log("approval.requested", f"等待人工审批：{what}", workspace_id=t["workspace_id"], agent_id=a["id"], task_id=t["id"], run_id=ctx.run_id,
                          mission_id=t["mission_id"], needs_human=True, severity="medium", data={"approval_id": ap["id"], "artifact_id": art["id"]})
        self.activity.notify(f"{a['name']} 提交了修复，等你审核", f"{title}\n{diff['stat']}\n测试：{'通过' if tests and tests['passed'] else ('失败' if tests else '没有运行')}",
                             severity="medium", agent_id=a["id"], workspace_id=t["workspace_id"], task_id=t["id"], mission_id=t["mission_id"], approval_id=ap["id"])
        return {"artifact_id": art["id"], "approval_id": ap["id"], "status": status, "files": diff["files"], "stat": diff["stat"],
                "pr_url": meta.get("pr_url"), "pr_error": meta.get("pr_error"),
                "note": "补丁已提交，等待人工审核；你不能合并或部署。在结果里说明补丁和测试情况。"}

    def decide(self, approval_id: int, approve: bool, by: str, note: str = "") -> dict:
        ap = self.db.approval(approval_id)
        if ap is None:
            raise AutonomyError(f"没有审批 #{approval_id}")
        if ap["status"] != "pending":
            raise AutonomyError("这个审批已经处理过了")
        result: dict[str, Any] = {}
        art = self.db.artifact(ap["artifact_id"]) if ap["artifact_id"] else None
        if approve and ap["kind"] == "patch" and art:
            ws = self.db.workspace(art["workspace_id"])
            repo = ws_repo(ws, (art["meta"] or {}).get("repo")) if ws else None
            if repo and repo["provider"] == "local":
                try:
                    commit = apply_to_local(self.sandbox, repo, art["content"], f"{art['title']}\n\nApplied by KAGOU after approval by {by} (artifact {art['id']}, task {art['task_id']})")
                except SandboxError as e:
                    raise AutonomyError(f"补丁没有应用：{e}") from None
                self.db.update_artifact(art["id"], status="applied", meta={**art["meta"], "applied_commit": commit, "applied_by": by})
                result = {"applied_commit": commit}
                self.activity.log("patch.applied", f"补丁已应用到 {repo['name']}（{commit[:10]}）", workspace_id=art["workspace_id"], task_id=art["task_id"], actor=by,
                                  agent_id=art["agent_id"], result=commit)
                self.events.ingest(workspace_id=art["workspace_id"], source="system", type_="patch.applied", severity="medium",
                                   title=f"{art['title'][:120]} → {repo['name']}@{commit[:10]}", dedupe_key=f"applied:{art['id']}",
                                   payload={"origin_task_id": art["task_id"], "artifact_id": art["id"], "commit": commit, "repo": repo["name"]})
            elif repo and repo["provider"] == "github":
                self.db.update_artifact(art["id"], status="approved" if art["status"] != "merged" else "merged", meta={**art["meta"], "approved_by": by})
                result = {"note": "已记录你的审核。KAGOU 不会合并：请在 GitHub 上合并这个 Draft PR；合并后 Webhook 会触发验证。" if art["meta"].get("pr_url")
                          else "已记录你的审核。没有 Draft PR（见补丁的 pr_error），需要手动应用补丁。"}
        elif not approve and art:
            self.db.update_artifact(art["id"], status="rejected")
        if not self.db.decide_approval(approval_id, "approved" if approve else "rejected", by, note):
            raise AutonomyError("这个审批已经处理过了")
        self.activity.log("approval.decided", f"审批 #{approval_id} {'通过' if approve else '驳回'}：{ap['title'][:120]}", workspace_id=ap["workspace_id"], task_id=ap["task_id"],
                          agent_id=ap["agent_id"], actor=by, reason=note[:500], result=str(result.get("applied_commit") or result.get("note") or ""))
        self.activity.audit(f"审批 #{approval_id} {'通过' if approve else '驳回'}", by, {"approval": approval_id, **result})
        return {"approval": self.db.approval(approval_id), **result}

    # ------------------------------------------------------------------ http

    def http_check(self, url: str) -> dict:
        try:
            check_url(url, self.watchers.allow_private)
        except ValueError as e:
            from .tools import ToolError

            raise ToolError(str(e)) from None
        try:
            code, ms, preview = self.watchers.http_get(url, 10.0)
            return {"url": url, "status": code, "latency_ms": ms, "ok": 200 <= code < 400, "body_preview": preview[:400]}
        except Exception as e:
            return {"url": url, "status": None, "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    # ------------------------------------------------------------------ views (everything below reads the database; nothing is invented)

    def watched_agents(self, alive: bool) -> set[str]:
        """Agents whose standing responsibility has a healthy watcher in its workspace to wake it."""
        healthy_ws = set()
        for w in self.db.watchers(status="active"):
            if self.watchers.healthy(w, alive)[0]:
                healthy_ws.add(w["workspace_id"])
        out = set()
        for m in self.db.missions(status=[C.M_ACTIVE], limit=1000):
            if m.get("responsibility_of") and m["type"] in ("event_driven", "continuous") and m.get("triggers") and m["workspace_id"] in healthy_ws:
                out.add(m["responsibility_of"])
        return out

    def agent_row(self, a: dict, alive: bool, watched: set[str]) -> dict:
        from .agents import agent_state

        st = agent_state(self.db, a, worker_alive=alive, watched=watched)
        cur = self.db.task(st["task_id"]) if st.get("task_id") else None
        names = {w["id"]: w["name"] for w in self.db.workspaces()}
        return {"id": a["id"], "name": a["name"], "role": a.get("role", ""), "responsibility": a.get("responsibility") or "", "work_mode": a.get("work_mode") or "on_demand",
                "workspaces": [{"id": w, "name": names.get(w, w)} for w in (a.get("workspace_ids") or [])], "status": a["status"], "autonomous": bool(a.get("autonomous")),
                "activated": bool(a.get("activated_at")), "state": st["state"], "state_detail": st["detail"],
                "current_task": {"id": cur["id"], "objective": cur["objective"], "kind": cur["kind"], "status": cur["status"]} if cur else None,
                "last_wake_at": self.db.last_wake(a["id"]), "last_activity_at": self.db.last_activity_at(a["id"]), "provider": a.get("provider"), "model": a.get("model"),
                "model_ready": a.get("provider") in self.providers, "spent_usd": a.get("spent_usd"), "budget_usd": a.get("budget_usd"),
                "appearance": a.get("appearance") or {}}

    def agent_rows(self, include_archived: bool = False) -> list[dict]:
        alive = self.worker_alive()
        watched = self.watched_agents(alive)
        return [self.agent_row(a, alive, watched) for a in self.db.agents(include_archived)]

    def agent_detail(self, aid: str) -> dict:
        a = self.agent_or_raise(aid)
        alive = self.worker_alive()
        row = self.agent_row(a, alive, self.watched_agents(alive))
        open_ = self.db.tasks(agent_id=aid, status=list(C.TASK_OPEN), limit=50)
        recent = self.db.tasks(agent_id=aid, status=list(C.TASK_FINAL), limit=30, order="finished_at DESC")
        sent = self.db.tasks(from_agent_id=aid, limit=30)
        return {**row, "agent": {k: v for k, v in a.items() if k not in ("system_prompt",)}, "system_prompt": a.get("system_prompt") or "",
                "missions": self.db.missions(agent_id=aid, limit=50), "open_tasks": open_, "recent_tasks": recent, "delegated": sent,
                "runs": self.db.runs(agent_id=aid, limit=20), "approvals": self.db.approvals(status="pending", agent_id=aid),
                "task_counts": self.db.task_status_counts(agent_id=aid)}

    def task_detail(self, tid: str) -> dict:
        t = self.wakeup.task_or_raise(tid)
        root = self.db.task(t["root_id"]) if t["root_id"] != t["id"] else t
        chain = self.db.tasks(root_id=t["root_id"], limit=200, order="created_at")
        runs = self.db.runs(task_id=tid, limit=20)
        return {"task": t, "root": root, "tree": chain, "runs": runs, "steps": self.db.steps(runs[0]["id"]) if runs else [],
                "event": self.db.event(t["event_id"]) if t.get("event_id") else None, "artifacts": self.db.artifacts(task_id=tid),
                "approvals": self.db.approvals(task_id=tid), "activity": self.db.activity(task_id=tid, limit=100),
                "names": {x: self.name(x) for x in {c["to_agent_id"] for c in chain} | {c["from_agent_id"] for c in chain} if x}}

    def runtime_health(self) -> dict:
        alive = self.worker_alive()
        workers = self.db.workers(10)
        chain = None
        if self.watchers.chain is not None:
            try:
                chain = self.watchers.chain.status()
            except Exception as e:  # pragma: no cover - status reads in-memory listener state
                chain = {"error": str(e)}
        g = self.wf.gateway
        day, _ = g.spent("day")
        month, _ = g.spent("month")
        wp = {"spent_today_usd": str(day), "spent_month_usd": str(month), "daily_budget_usd": str(g.daily_budget) if g.daily_budget is not None else None,
              "monthly_budget_usd": str(g.monthly_budget) if g.monthly_budget is not None else None, "max_concurrent": g.max_concurrent}
        problems = []
        if not alive:
            problems.append("没有在线的 Worker：Agent 不会被唤醒")
        if not self.providers:
            problems.append("没有配置模型 API Key：Agent 被唤醒后会执行失败")
        stuck = self.db.count_tasks(status=[C.T_UNASSIGNED])
        if stuck:
            problems.append(f"{stuck} 个任务没有合适的 Agent 接手")
        failed_ev = len(self.db.events(status=C.E_FAILED, limit=100))
        if failed_ev:
            problems.append(f"{failed_ev} 个事件处理失败")
        level = "down" if not alive else ("degraded" if problems else "ok")
        return {"level": level, "worker_alive": alive, "workers": workers, "problems": problems, "models": self.model_status(), "gateway": wp,
                "chain": chain, "sandbox": self.sandbox.info(), "github_token": self.github is not None, "limits": self.limits.public()}

    def overview(self) -> dict:
        alive = self.worker_alive()
        rows = self.agent_rows()
        watchers = self.db.watchers()
        healthy = [w for w in watchers if self.watchers.healthy(w, alive)[0]]
        investigating = self.db.count_tasks(kind="investigate", status=[C.T_QUEUED, C.T_RUNNING, C.T_WAITING])
        active = self.db.missions(status=[C.M_ACTIVE], limit=1000)
        running_missions = [m for m in active if self.db.count_tasks(mission_id=m["id"], status=list(C.TASK_OPEN))]
        health = self.runtime_health()
        return {
            "kpis": {
                "total_agents": len(rows),
                "active_watchers": len(healthy), "watchers_total": len(watchers),
                "running_missions": len(running_missions), "active_missions": len(active),
                "active_investigations": investigating,
                "pending_approvals": len(self.db.approvals(status="pending", limit=500)),
                "runtime_health": health["level"],
            },
            "agents": rows,
            "health": {k: health[k] for k in ("level", "worker_alive", "problems", "models")},
            "unread": self.db.unread_count(),
            "last_activity_id": self.db.last_activity_id(),
        }
