import type { Message } from "./types";

export type Tone = "ok" | "bad" | "acc" | "info" | "mute" | "";

export interface EventView {
  verb: string; // short label: Assigned, Handoff, Completed…
  tone: Tone;
  major: boolean; // shown on the mission timeline; minor ones only in logs / activity
  showTarget: boolean;
}

// How each engine event reads in the terminal. Tool output and delivery bookkeeping are minor:
// they belong in Logs, not in the story of the mission.
const MAP: Record<string, EventView> = {
  "user.instruction": { verb: "Mission brief", tone: "acc", major: true, showTarget: false },
  "user.note": { verb: "Note", tone: "mute", major: true, showTarget: false },
  "task.created": { verb: "Created", tone: "mute", major: false, showTarget: false },
  "goal.submitted": { verb: "Handed to", tone: "info", major: true, showTarget: true },
  "agent.started": { verb: "Planning", tone: "info", major: true, showTarget: false },
  "plan.created": { verb: "Plan", tone: "info", major: true, showTarget: false },
  "plan.note": { verb: "Plan note", tone: "mute", major: true, showTarget: false },
  "plan.expanded": { verb: "Delegated more", tone: "info", major: true, showTarget: false },
  "task.assign": { verb: "Assigned", tone: "info", major: true, showTarget: true },
  "task.assigned": { verb: "Assigned", tone: "info", major: false, showTarget: false },
  "agent.working": { verb: "Started", tone: "acc", major: true, showTarget: false },
  "tool.start": { verb: "Tool call", tone: "mute", major: false, showTarget: false },
  "tool.done": { verb: "Tool done", tone: "ok", major: false, showTarget: false },
  "tool.error": { verb: "Tool error", tone: "bad", major: false, showTarget: false },
  "tool.manual": { verb: "Manual run", tone: "mute", major: false, showTarget: false },
  "task.completed": { verb: "Completed", tone: "ok", major: true, showTarget: false },
  "task.failed": { verb: "Failed", tone: "bad", major: true, showTarget: false },
  "task.retry": { verb: "Retry", tone: "acc", major: true, showTarget: false },
  "task.cancelled": { verb: "Cancelled", tone: "mute", major: true, showTarget: false },
  handoff: { verb: "Handoff", tone: "info", major: true, showTarget: true },
  "task.result": { verb: "Reported to", tone: "mute", major: false, showTarget: true },
  "goal.review": { verb: "Submitted for review", tone: "acc", major: true, showTarget: false },
  "approval.requested": { verb: "Approval requested", tone: "acc", major: true, showTarget: false },
  "approval.approved": { verb: "Approved", tone: "ok", major: true, showTarget: false },
  "approval.rejected": { verb: "Rejected", tone: "bad", major: true, showTarget: false },
  "chain.submitted": { verb: "Broadcast", tone: "acc", major: true, showTarget: false },
  "chain.confirmed": { verb: "On-chain confirmed", tone: "ok", major: true, showTarget: false },
  "chain.failed": { verb: "On-chain failed", tone: "bad", major: true, showTarget: false },
  "message.expired": { verb: "Timed out", tone: "bad", major: true, showTarget: false },
  "engine.error": { verb: "Engine error", tone: "bad", major: true, showTarget: false },
  "agent.message": { verb: "Message", tone: "mute", major: true, showTarget: true },
  "task.reassigned": { verb: "Reassigned", tone: "acc", major: true, showTarget: false },
  "task.queued": { verb: "Queued", tone: "mute", major: true, showTarget: false },
  "squad.holding": { verb: "Squad paused", tone: "acc", major: true, showTarget: false },
  "squad.created": { verb: "Squad created", tone: "info", major: false, showTarget: false },
  "squad.member_added": { verb: "Joined squad", tone: "info", major: false, showTarget: false },
  "squad.member_removed": { verb: "Left squad", tone: "mute", major: false, showTarget: false },
  "squad.manager": { verb: "Manager set", tone: "info", major: false, showTarget: false },
  "squad.paused": { verb: "Squad paused", tone: "acc", major: false, showTarget: false },
  "squad.active": { verb: "Squad resumed", tone: "ok", major: false, showTarget: false },
};

export function describe(m: Message): EventView {
  return MAP[m.event] || { verb: m.event.replace(/[._]/g, " "), tone: m.event.includes("fail") || m.event.includes("error") ? "bad" : "mute", major: false, showTarget: !!m.recipient };
}

/** The node colour on a timeline rail. */
export const nodeClass = (t: Tone) => (t === "ok" ? "ok" : t === "bad" ? "bad" : t === "acc" ? "acc" : t === "info" ? "info" : "");
