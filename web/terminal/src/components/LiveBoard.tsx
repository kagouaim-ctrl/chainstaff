import { memo } from "react";
import { PHASE_LABEL, PHASE_TONE, useMissionPhases, type LiveState } from "../lib/live";
import { useAgentName } from "../lib/queries";
import type { TaskDetail } from "../lib/types";
import { ago } from "../lib/util";

const ACTIVE = new Set(["reasoning", "tool", "rpc", "queued"]);

/**
 * One line per task of the mission: who has it and what it is doing this second
 * (AI reasoning, calling a tool, waiting on the chain RPC, done, failed), with the model's latest text.
 */
export const LiveBoard = memo(function LiveBoard({ d, onTask }: { d: TaskDetail; onTask: (id: string) => void }) {
  const phases = useMissionPhases(d);
  const name = useAgentName();
  const rows = d.subtasks.map((t, i) => ({ t, n: i + 1, s: phases.get(t.id) })).filter((r) => r.s || ["running", "assigned"].includes(r.t.status));
  const active = rows.filter((r) => r.s && ACTIVE.has(r.s.phase)).length;
  // Once nothing is running the plan and the feed tell the story; the board is for while it runs.
  if (!rows.length || (!active && !["pending", "running", "assigned", "waiting"].includes(d.root.status))) return null;
  return (
    <div className="live-board" aria-live="polite">
      <div className="live-board-h">
        <span className="label">Live</span>
        <span className="muted xs">{active ? `${active} 个任务进行中` : "没有进行中的任务"}</span>
      </div>
      {rows.map(({ t, n, s }) => (
        <button key={t.id} className="live-row" onClick={() => onTask(t.id)}>
          <span className="mono muted">#{n}</span>
          <span className="ellipsis live-title">{t.title}</span>
          <span className="muted xs ellipsis live-who">{name(t.assigned_agent)}</span>
          <Phase s={s} fallback={t.status} />
          {s?.detail && <span className="live-detail xs ellipsis">{s.detail}</span>}
        </button>
      ))}
    </div>
  );
});

function Phase({ s, fallback }: { s?: LiveState; fallback: string }) {
  if (!s) return <span className="st mute"><i /><span className="t">{fallback}</span></span>;
  const live = ACTIVE.has(s.phase) && s.phase !== "queued";
  return (
    <span className={`st ${PHASE_TONE[s.phase]} ${live ? "live" : ""}`} title={`${PHASE_LABEL[s.phase]} · ${ago(s.since)}`}>
      <i />
      <span className="t">
        {PHASE_LABEL[s.phase]}
        {s.tool && (s.phase === "tool" || s.phase === "rpc") ? <span className="mono"> · {s.tool}</span> : null}
      </span>
      {(s.modelCalls > 0 || s.toolCalls > 0) && <span className="muted xs mono">{s.modelCalls}m/{s.toolCalls}t</span>}
    </span>
  );
}
