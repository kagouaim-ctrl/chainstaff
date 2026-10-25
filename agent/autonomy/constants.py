"""Names shared by every autonomy module: states, kinds, severities, permissions and limits.

Agent configuration (who the agent is, what it is responsible for, what it may do) is long-lived and lives in
wf_agents. Work is short-lived: an event or a hand-off creates a task, a worker claims it, and one run of the
agent's model handles it. The agent's state is derived from that work, never stored as a flag that can drift.
"""

from __future__ import annotations

import os

# ---- tasks (structured hand-offs) ----
T_QUEUED = "queued"                  # waiting for a worker and for the agent to have a free slot
T_RUNNING = "running"                # a worker holds the lease and the agent's run is executing
T_WAITING = "waiting"                # the run handed sub-tasks out and sleeps until their results come back
T_WAITING_APPROVAL = "waiting_approval"  # a human has to approve something this task produced
T_COMPLETED = "completed"
T_FAILED = "failed"
T_CANCELLED = "cancelled"
T_REJECTED = "rejected"              # the target agent refused it (outside its permissions or responsibility)
T_UNASSIGNED = "unassigned"          # no agent fits; a human has to assign it
TASK_STATUSES = (T_QUEUED, T_RUNNING, T_WAITING, T_WAITING_APPROVAL, T_COMPLETED, T_FAILED, T_CANCELLED, T_REJECTED, T_UNASSIGNED)
TASK_OPEN = (T_QUEUED, T_RUNNING, T_WAITING, T_WAITING_APPROVAL, T_UNASSIGNED)
TASK_FINAL = (T_COMPLETED, T_FAILED, T_CANCELLED, T_REJECTED)

# What a task asks for. The kind decides the permissions a target needs when the delegating agent names none.
TASK_KINDS = {
    "investigate": "调查一个事件或异常，找出原因",
    "analyze": "分析数据或证据，给出结论",
    "fix": "修改代码并运行测试，提交待审核的修复",
    "test": "运行测试并报告结果",
    "verify": "验证一个问题是否已经消失",
    "review": "审阅别人的结果",
    "report": "整理并汇报",
    "general": "其他工作",
}
KIND_PERMISSIONS = {"fix": ("code:write", "code:test", "code:propose"), "test": ("code:test",), "verify": ()}

# ---- runs (one execution of an agent for one task) ----
R_RUNNING, R_COMPLETED, R_FAILED, R_WAITING, R_INTERRUPTED, R_CANCELLED = "running", "completed", "failed", "waiting", "interrupted", "cancelled"

# ---- agent states (derived) ----
# Backend state machine. The console shows these, plus "investigating" for a running investigate task, "error"
# when the last run failed, and "offline" when no worker is alive to run anything.
A_IDLE, A_WATCHING, A_QUEUED, A_RUNNING, A_WAITING, A_WAITING_APPROVAL, A_PAUSED = (
    "idle", "watching", "queued", "running", "waiting", "waiting_approval", "paused")
A_INVESTIGATING, A_ERROR, A_OFFLINE, A_ARCHIVED = "investigating", "error", "offline", "archived"

WORK_MODES = {
    "event": "Event-driven：有事件或有人交任务时被唤醒",
    "continuous": "Continuous：长期负责，监听所有绑定的数据源，并定期自检",
    "on_demand": "On-demand：只在有人或其他 Agent 交任务时工作",
    "scheduled": "Scheduled：按 cron 定时工作",
}

# ---- missions ----
MISSION_TYPES = {
    "one_time": "One-time：完成一次就结束",
    "continuous": "Continuous：长期负责，每个事件触发一次独立执行，不会自动结束",
    "event_driven": "Event-driven：只在匹配的事件发生时执行",
    "scheduled": "Scheduled：按 cron 定时执行",
}
M_ACTIVE, M_PAUSED, M_STOPPED, M_COMPLETED, M_FAILED = "active", "paused", "stopped", "completed", "failed"

# ---- events ----
SEVERITIES = ("info", "low", "medium", "high", "critical")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
E_RECEIVED, E_PENDING_CONFIRMATION, E_PROCESSED, E_IGNORED, E_FAILED, E_REORGED = (
    "received", "pending_confirmation", "processed", "ignored", "failed", "reorged")

WATCHER_KINDS = {
    "bsc_events": "BSC 链上事件（代币转账、合约事件、地址）",
    "github_webhook": "GitHub Webhook（push、pull request、issue）",
    "ci_failure": "CI 失败（GitHub Actions workflow_run / check_run，或任意 CI 回调）",
    "app_logs": "应用错误日志（服务把错误日志 POST 过来）",
    "api_health": "API 健康检查（Worker 定时请求一个地址）",
}

# ---- permissions an autonomous agent can hold (on top of the skill permissions in agent.workforce.skills) ----
# risk: low = read only, medium = writes inside the sandbox / KAGOU itself, high = leaves KAGOU (needs approval).
AUTONOMY_PERMISSIONS = {
    "delegate": ("把任务交给其他 Agent", "low"),
    "code:read": ("在隔离副本里读仓库代码", "low"),
    "code:write": ("在隔离副本里修改代码（不会碰原仓库）", "medium"),
    "code:test": ("在隔离副本里运行仓库配置的测试命令", "medium"),
    "code:propose": ("把隔离副本里的改动提交为待审核补丁", "medium"),
    "github:draft_pr": ("把待审核补丁推送到新分支并开 Draft Pull Request（需要 KAGOU_GITHUB_TOKEN）", "high"),
    "net:http": ("请求工作区里登记的 API 地址（只读 GET）", "low"),
    "memory:write": ("写入长期记忆", "low"),
}
# Never granted to an agent at all; listed so the console can say so.
HUMAN_ONLY = {
    "merge:production": "合并到生产分支",
    "deploy:production": "部署生产服务",
    "contract:upgrade": "智能合约升级",
    "contract:admin": "合约管理员权限变更",
    "funds:transfer": "涉及真实资产的签名和转账",
    "secrets:read": "读取敏感凭据",
}
DEFAULT_PERMISSIONS = ["delegate", "memory:write"]  # read only by default


class Limits:
    """Loop guards. Every one has a server-wide value; read once per process."""

    def __init__(self, env: dict | None = None) -> None:
        e = env if env is not None else os.environ
        g = lambda n, d, lo, hi: _env_int_from(e, n, d, lo, hi)  # noqa: E731
        self.max_depth = g("KAGOU_MAX_DELEGATION_DEPTH", 4, 1, 10)
        self.max_attempts = g("KAGOU_TASK_MAX_ATTEMPTS", 2, 1, 5)
        self.task_timeout_s = g("KAGOU_TASK_TIMEOUT_S", 1800, 60, 86400)
        self.worker_concurrency = g("KAGOU_WORKER_CONCURRENCY", 4, 1, 64)
        self.agent_concurrency = g("KAGOU_AGENT_CONCURRENCY", 1, 1, 16)
        self.delegations_per_hour = g("KAGOU_MAX_DELEGATIONS_PER_HOUR", 30, 1, 1000)
        self.event_runs_per_hour = g("KAGOU_MAX_EVENT_RUNS_PER_HOUR", 20, 1, 1000)
        self.fix_attempts_per_issue = g("KAGOU_MAX_FIXES_PER_ISSUE", 2, 1, 20)
        self.lease_s = g("KAGOU_LEASE_S", 60, 10, 3600)
        self.heartbeat_s = g("KAGOU_HEARTBEAT_S", 5, 1, 60)
        self.worker_stale_s = g("KAGOU_WORKER_STALE_S", 30, 5, 600)
        self.max_tool_calls = g("KAGOU_RUN_MAX_TOOL_CALLS", 30, 1, 200)
        self.max_model_calls = g("KAGOU_RUN_MAX_MODEL_CALLS", 16, 1, 100)
        self.default_task_budget = str(e.get("KAGOU_TASK_BUDGET_USD") or "0.5")

    def public(self) -> dict:
        return dict(vars(self))


def _env_int_from(e, name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(e.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))
