import { ChevronLeft, ChevronRight, Plus, Send, ShieldCheck } from "lucide-react";
import { useMemo } from "react";
import { useAgents, useApprovals, useMissions, useOverview } from "../lib/queries";
import { go, usd, usePref } from "../lib/util";
import { Cell } from "./ui";

/** The workforce at a glance plus the three things people start from: a mission, the review queue, a new agent. */
export function QuickActionBar({ onNewMission, onNewAgent, onView }: { onNewMission: () => void; onNewAgent: () => void; onView?: (v: string) => void }) {
  const agents = useAgents();
  const approvals = useApprovals("pending");
  const running = useMissions({ status: "pending,running,waiting", limit: 200 });
  const ov = useOverview("24h");
  const [more, setMore] = usePref<boolean>("metrics-more", false);
  const c = useMemo(() => {
    const all = (agents.data || []).filter((a) => a.status !== "archived");
    return {
      total: all.length,
      online: all.filter((a) => a.status === "active").length,
      busy: all.filter((a) => a.live === "busy").length,
      waiting: all.filter((a) => a.live === "waiting").length,
      idle: all.filter((a) => a.live === "idle").length,
      error: all.filter((a) => a.live === "error").length,
      paused: all.filter((a) => a.status === "paused").length,
    };
  }, [agents.data]);
  const review = approvals.data?.length || 0;
  return (
    <div className="strip-wrap" role="toolbar" aria-label="Workforce summary">
      <div className="strip">
        <Cell label="Active agents" value={<>{c.busy}<span className="muted">/{c.online}</span></>} sub={`${c.busy} running · ${c.online} on duty`} tone={c.busy ? "acc" : ""} onClick={() => onView?.("running")} />
        <Cell label="Running missions" value={running.data?.length ?? "—"} onClick={() => go("tasks", undefined, { f: "running", kind: "goal" })} />
        <Cell label="Pending approvals" value={review} tone={review ? "acc" : ""} onClick={() => go("tasks", undefined, { f: "review", kind: "goal" })} />
        <Cell label="Execution cost" value={ov.data ? usd(ov.data.cost_usd) : "—"} sub="last 24h" />
        {more && (
          <>
            <Cell label="Idle" value={c.idle} onClick={() => onView?.("idle")} />
            <Cell label="Waiting" value={c.waiting} />
            <Cell label="Failed 24h" value={c.error} tone={c.error ? "bad" : ""} onClick={() => onView?.("attention")} />
            <Cell label="Paused" value={c.paused} />
            <Cell label="Squads" value={ov.data?.squads ?? "—"} onClick={() => go("squads")} />
            <Cell label="Tokens 24h" value={ov.data ? ov.data.tokens.toLocaleString() : "—"} />
          </>
        )}
      </div>
      <div className="strip-act">
        <button className="btn ghost sm icon" onClick={() => setMore(!more)} aria-expanded={more} title={more ? "收起指标" : "更多指标"} aria-label={more ? "收起指标" : "更多指标"}>
          {more ? <ChevronLeft size={13} /> : <ChevronRight size={13} />}
        </button>
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
