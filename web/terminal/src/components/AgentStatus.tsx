import type { Agent } from "../lib/types";

// The engine's live state for an agent, in the terminal's words.
const LIVE: Record<string, [string, string, boolean?]> = {
  busy: ["Running", "acc", true],
  review: ["In review", "warn"],
  idle: ["Idle", "ok"],
  error: ["Error", "bad"],
  paused: ["Paused", "mute"],
  archived: ["Archived", "mute"],
  active: ["Active", "ok"],
};

export const liveOf = (a: Pick<Agent, "live" | "status">) => a.live || a.status;

export function AgentStatus({ agent, label }: { agent: Pick<Agent, "live" | "status">; label?: boolean }) {
  const [l, tone, live] = LIVE[liveOf(agent)] || [liveOf(agent), ""];
  return (
    <span className={`st ${tone} ${live ? "live" : ""}`} title={l}>
      <i />
      {label !== false && <span className="t">{l}</span>}
    </span>
  );
}
