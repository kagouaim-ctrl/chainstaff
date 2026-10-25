"""Activity, notifications and audit: the three records of what the system did.

Activity is the structured trail the console filters (who triggered what, why, who it went to, what came of it,
whether a human is needed). Notifications are the subset a person should look at; they live in the console only
(KAGOU sends nothing to Telegram or any outside service). Audit is the existing append-only events log, for
changes people make.
"""

from __future__ import annotations

from typing import Any

from . import constants as C
from .db import AutonomyDB

AUDIT = "autonomy"

# Activity types. The console groups by the prefix.
TYPES = {
    "agent.created": "Agent Created",
    "agent.updated": "Agent Updated",
    "agent.activated": "Agent Activated",
    "agent.paused": "Agent Paused",
    "agent.resumed": "Agent Resumed",
    "agent.archived": "Agent Archived",
    "agent.awakened": "Agent Awakened",
    "agent.finished": "Run Finished",
    "watcher.started": "Watcher Started",
    "watcher.paused": "Watcher Paused",
    "watcher.down": "Watcher Down",
    "watcher.recovered": "Watcher Recovered",
    "event.received": "Event Received",
    "event.ignored": "Event Ignored",
    "event.routed": "Event Routed",
    "event.reorged": "Event Reorged",
    "bug.detected": "Bug Detected",
    "alert.triggered": "Alert Triggered",
    "task.created": "Task Created",
    "task.delegated": "Task Delegated",
    "task.unassigned": "Task Unassigned",
    "task.rejected": "Task Rejected",
    "task.deduplicated": "Task Deduplicated",
    "task.blocked": "Delegation Blocked",
    "task.completed": "Task Completed",
    "task.failed": "Task Failed",
    "task.cancelled": "Task Cancelled",
    "task.retried": "Task Retried",
    "task.timeout": "Task Timed Out",
    "task.resumed": "Task Resumed",
    "investigation.completed": "Investigation Completed",
    "fix.started": "Fix Started",
    "tests.passed": "Tests Passed",
    "tests.failed": "Tests Failed",
    "patch.submitted": "Patch Submitted",
    "pr.created": "Pull Request Created",
    "patch.applied": "Patch Applied",
    "approval.requested": "Approval Requested",
    "approval.decided": "Approval Decided",
    "mission.created": "Mission Created",
    "mission.triggered": "Mission Triggered",
    "mission.paused": "Mission Paused",
    "mission.resumed": "Mission Resumed",
    "mission.stopped": "Mission Stopped",
    "mission.completed": "Mission Completed",
    "budget.exceeded": "Budget Exceeded",
    "worker.started": "Worker Started",
    "worker.stopped": "Worker Stopped",
    "worker.recovered": "Worker Recovered",
    "system.error": "System Error",
    "user.note": "User Note",
}


class Activity:
    def __init__(self, db: AutonomyDB) -> None:
        self.db = db

    def log(self, type_: str, title: str, *, severity: str = "info", workspace_id: str | None = None, agent_id: str | None = None,
            target_agent_id: str | None = None, mission_id: str | None = None, task_id: str | None = None, run_id: str | None = None,
            event_id: str | None = None, actor: str = "system", reason: str = "", result: str = "", needs_human: bool = False,
            data: Any = None) -> int:
        if severity not in C.SEV_RANK:
            severity = "info"
        return self.db.add_activity(type=type_, title=title, severity=severity, workspace_id=workspace_id, agent_id=agent_id,
                                    target_agent_id=target_agent_id, mission_id=mission_id, task_id=task_id, run_id=run_id, event_id=event_id,
                                    actor=actor, reason=reason, result=result, needs_human=needs_human, data=data)

    def notify(self, title: str, body: str = "", *, severity: str = "info", agent_id: str | None = None, workspace_id: str | None = None,
               task_id: str | None = None, mission_id: str | None = None, approval_id: int | None = None, dedupe_key: str | None = None) -> int | None:
        """A console notification. With an agent, its notify rules decide: an agent can mute low-severity news, never
        approvals or failures that need a human."""
        if agent_id and approval_id is None and severity in ("info", "low"):
            a = self.db.agent(agent_id)
            rules = (a or {}).get("notify_rules") or {}
            floor = rules.get("min_severity", "medium")
            if C.SEV_RANK.get(severity, 0) < C.SEV_RANK.get(floor, 2):
                return None
        return self.db.add_notification(severity=severity, title=title[:200], body=body[:2000], agent_id=agent_id, workspace_id=workspace_id,
                                        task_id=task_id, mission_id=mission_id, approval_id=approval_id, dedupe_key=dedupe_key)

    def audit(self, text: str, by: str, data: dict | None = None) -> None:
        self.db.store.log_event("audit", text, by, data or {}, employee=AUDIT)
