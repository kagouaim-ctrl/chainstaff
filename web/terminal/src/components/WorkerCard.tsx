import { Boxes, MessageSquare, Settings } from "lucide-react";
import type { AtAgentRow } from "../lib/at";
import { ago, fmtTime, go } from "../lib/util";
import { AgentState } from "./AtBits";
import { WorkerAvatar } from "./WorkerAvatar";

/**
 * A worker at a glance: who it is, what it is for, what it is doing now. Actions appear on hover (always on
 * touch screens), so a page of cards stays quiet.
 */
export function WorkerCard({ a, onTask }: { a: AtAgentRow; onTask?: (id: string) => void }) {
  const ws = a.workspaces[0];
  const draft = !a.activated && a.status !== "archived";
  const last = a.last_activity_at || a.last_wake_at;
  return (
    <article className={`wk-card ${a.status === "paused" ? "paused" : ""}`} onClick={() => go("agents", a.id)}>
      <div className="wk-top">
        <WorkerAvatar id={a.id} appearance={a.appearance} size={44} dim={a.status === "paused"} />
        <div className="col gap2 grow" style={{ minWidth: 0 }}>
          <h3 className="wk-name ellipsis">{a.name}</h3>
          <span className="wk-role ellipsis" title={a.responsibility}>{a.role || firstLine(a.responsibility) || (draft ? "Draft · no responsibility yet" : "—")}</span>
        </div>
      </div>
      <div className="wk-state">
        {draft ? <span className="badge acc">Not activated</span> : <AgentState state={a.state} detail={a.state_detail} />}
      </div>
      <div className="wk-meta">
        <span className="wk-k">Current task</span>
        {a.current_task ? (
          <button className="wk-task ellipsis" title={a.current_task.objective} onClick={(e) => (e.stopPropagation(), onTask ? onTask(a.current_task!.id) : go("missions", undefined, { task: a.current_task!.id }))}>
            {a.current_task.objective}
          </button>
        ) : <span className="wk-v muted ellipsis" title={a.state_detail}>{draft ? "Set it up to start working" : a.state_detail || "None"}</span>}
        <span className="wk-k">Last active</span>
        <span className="wk-v muted" title={fmtTime(last)}>{last ? `${ago(last)} ago`.replace("now ago", "just now") : "Never"}</span>
      </div>
      <div className="wk-actions" onClick={(e) => e.stopPropagation()}>
        <button className="btn sm" onClick={() => go("agents", a.id)}><MessageSquare size={13} /> Chat</button>
        <button className="btn ghost sm icon" disabled={!ws} title={ws ? `Workspace: ${ws.name}` : "No workspace yet"} aria-label="Workspace" onClick={() => ws && go("workspace", ws.id)}><Boxes size={14} /></button>
        <button className="btn ghost sm icon" title="Settings" aria-label="Settings" onClick={() => go("agents", a.id, { tab: "overview" })}><Settings size={14} /></button>
      </div>
    </article>
  );
}

const firstLine = (s: string) => s.split(/[\n。.!！]/)[0]?.trim().slice(0, 80);
