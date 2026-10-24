import { useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal, Send } from "lucide-react";
import { useState } from "react";
import { post } from "../lib/api";
import { useAction, useAgentName, useWorkspaces } from "../lib/queries";
import type { Task, Workspace } from "../lib/types";
import { go, usePref } from "../lib/util";
import { Dialog } from "./ui";

/**
 * Hand a mission to a squad's AI manager, or leave a note on the workspace.
 * Enter sends, Shift+Enter is a new line; the draft survives a reload.
 */
export function TaskComposer({ ws, onSubmitted, autoFocus, rows = 2 }: { ws: Workspace; onSubmitted?: (t: Task) => void; autoFocus?: boolean; rows?: number }) {
  const name = useAgentName();
  const qc = useQueryClient();
  const [text, setText] = usePref<string>(`draft:${ws.id}`, "");
  const [mode, setMode] = useState<"mission" | "note">("mission");
  const [opts, setOpts] = useState(false);
  const [o, setO] = useState({ priority: "2", max_steps: "20", budget_usd: "1", timeout_s: "600" });
  const send = useAction(
    () =>
      mode === "mission"
        ? post<Task>(`/api/wf/workspaces/${ws.id}/goals`, { text, priority: Number(o.priority), max_steps: Number(o.max_steps), budget_usd: o.budget_usd, timeout_s: Number(o.timeout_s) })
        : post(`/api/wf/workspaces/${ws.id}/notes`, { text }),
    ["workspaces", "missions", "tasks"],
    mode === "mission" ? "Mission 已交给主管" : undefined,
  );
  const submit = () => {
    if (!text.trim() || send.isPending || (mode === "mission" && !ws.manager_id)) return;
    send.mutate(undefined, {
      onSuccess: (t) => {
        setText("");
        qc.invalidateQueries({ queryKey: ["timeline", ws.id] });
        if (mode === "mission") onSubmitted?.(t as Task);
      },
    });
  };
  return (
    <div className="composer">
      <div className="row gap6 to">
        <div className="seg">
          <button aria-pressed={mode === "mission"} onClick={() => setMode("mission")}>New mission</button>
          <button aria-pressed={mode === "note"} onClick={() => setMode("note")}>Note</button>
        </div>
        <span className="ellipsis">
          {mode === "mission" ? (ws.manager_id ? <>to <b className="dim">{name(ws.manager_id)}</b> · plans and delegates to {ws.members.length} agents</> : <span className="warn">这个工作区还没有 AI 主管，不能接 Mission</span>) : "所有人可见，不会启动任务"}
        </span>
      </div>
      <textarea
        className="input"
        rows={rows}
        autoFocus={autoFocus}
        placeholder={mode === "mission" ? "描述目标，例如：Analyze this BSC contract 0x…  (Enter 发送 · Shift+Enter 换行)" : "写一条备注"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        aria-label={mode === "mission" ? "Mission 目标" : "备注"}
      />
      <div className="row gap6">
        {mode === "mission" && (
          <button className="btn ghost sm" onClick={() => setOpts(!opts)} aria-expanded={opts} title="优先级、步数、预算、超时">
            <SlidersHorizontal size={12} />
            <span className="mono">P{o.priority} · {o.max_steps} steps · ${o.budget_usd} · {Math.round(Number(o.timeout_s) / 60)}m</span>
          </button>
        )}
        <div className="grow" />
        <button className="btn primary sm" onClick={submit} disabled={!text.trim() || send.isPending || (mode === "mission" && !ws.manager_id)}>
          <Send size={12} /> {mode === "mission" ? "Launch" : "Post"}
        </button>
      </div>
      {opts && mode === "mission" && (
        <div className="row wrap gap12 xs muted">
          <label className="row gap4">Priority
            <select className="input sm" value={o.priority} onChange={(e) => setO({ ...o, priority: e.target.value })}>
              {[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}
            </select>
          </label>
          <label className="row gap4">Max steps <input className="input sm mono" style={{ width: 50 }} value={o.max_steps} onChange={(e) => setO({ ...o, max_steps: e.target.value.replace(/\D/g, "") })} /></label>
          <label className="row gap4">Budget $ <input className="input sm mono" style={{ width: 60 }} value={o.budget_usd} onChange={(e) => setO({ ...o, budget_usd: e.target.value })} /></label>
          <label className="row gap4">Timeout s <input className="input sm mono" style={{ width: 70 }} value={o.timeout_s} onChange={(e) => setO({ ...o, timeout_s: e.target.value.replace(/\D/g, "") })} /></label>
        </div>
      )}
    </div>
  );
}

/** "New mission" from anywhere: pick the squad, write the goal, land in its workspace. */
export function NewMissionDialog({ onClose, workspaceId }: { onClose: () => void; workspaceId?: string }) {
  const ws = useWorkspaces();
  const spaces = (ws.data || []).filter((w) => !w.archived);
  const [id, setId] = useState(workspaceId || spaces.find((w) => w.manager_id)?.id || spaces[0]?.id || "");
  const current = spaces.find((w) => w.id === id);
  return (
    <Dialog title="New mission" onClose={onClose} wide>
      {!spaces.length ? (
        <div className="note">还没有工作区。到 Workspace 先建立一个 Squad（一个 AI 主管加几个员工）。</div>
      ) : (
        <>
          <label className="field">
            <span>Squad</span>
            <select className="input" value={id} onChange={(e) => setId(e.target.value)}>
              {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}{w.manager_id ? "" : "（没有主管）"}</option>)}
            </select>
          </label>
          {current && (
            <div style={{ margin: "0 -16px -14px" }}>
              <TaskComposer ws={current} autoFocus rows={4} onSubmitted={(t) => (onClose(), go("workspace", t.workspace_id, { m: t.id }))} />
            </div>
          )}
        </>
      )}
    </Dialog>
  );
}
