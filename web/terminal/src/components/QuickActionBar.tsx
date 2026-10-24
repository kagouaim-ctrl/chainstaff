import { Plus, Send, ShieldCheck } from "lucide-react";
import { useMemo } from "react";
import { useAgents, useApprovals, useMissions, useOverview } from "../lib/queries";
import { go, usd } from "../lib/util";
import { Cell } from "./ui";

/** The workforce at a glance plus the three things people start from: a mission, the review queue, a new agent. */
export function QuickActionBar({ onNewMission, onNewAgent, onView }: { onNewMission: () => void; onNewAgent: () => void; onView?: (v: string) => void }) {
  const agents = useAgents();
  const approvals = useApprovals("pending");
  const running = useMissions({ status: "pending,running,waiting", limit: 200 });
  const ov = useOverview("24h");
  const c = useMemo(() => {
    const all = (agents.data || []).filter((a) => a.status !== "archived");
    return {
      total: all.length,
      online: all.filter((a) => a.status === "active").length,
      busy: all.filter((a) => a.live === "busy").length,
      idle: all.filter((a) => a.live === "idle").length,
      error: all.filter((a) => a.live === "error").length,
    };
  }, [agents.data]);
  const review = approvals.data?.length || 0;
  return (
    <div className="strip-wrap" role="toolbar" aria-label="Workforce summary">
      <div className="strip">
        <Cell label="Agents online" value={<>{c.online}<span className="muted">/{c.total}</span></>} onClick={() => onView?.("active")} />
        <Cell label="Running" value={c.busy} tone={c.busy ? "acc" : ""} onClick={() => onView?.("running")} />
        <Cell label="Idle" value={c.idle} onClick={() => onView?.("idle")} />
        <Cell label="Error 24h" value={c.error} tone={c.error ? "bad" : ""} onClick={() => onView?.("attention")} />
        <Cell label="Missions live" value={running.data?.length ?? "—"} onClick={() => go("tasks", undefined, { f: "running", kind: "goal" })} />
        <Cell label="Awaiting you" value={review} tone={review ? "acc" : ""} onClick={() => go("tasks", undefined, { f: "review", kind: "goal" })} />
        <Cell label="Spend 24h" value={ov.data ? usd(ov.data.cost_usd) : "—"} sub={ov.data ? `${ov.data.tokens.toLocaleString()} tok` : undefined} />
      </div>
      <div className="strip-act">
        {review > 0 && (
          <button className="btn sm outline-acc" onClick={() => go("tasks", undefined, { f: "review", kind: "goal" })}>
            <ShieldCheck size={12} /> Review {review}
          </button>
        )}
        <button className="btn sm" onClick={onNewAgent}><Plus size={12} /> New agent</button>
        <button className="btn sm primary" onClick={onNewMission}><Send size={12} /> New mission</button>
      </div>
    </div>
  );
}
