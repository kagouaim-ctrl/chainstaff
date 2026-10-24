import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Paperclip, Send, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { newRequestKey, post, postOnce } from "../lib/api";
import { useAction, useAgentName, useAgents, usePreflight, useSquads, useWorkspaces } from "../lib/queries";
import type { PreflightCheck, PreflightFix, Squad, Task, Workspace } from "../lib/types";
import { go, usePref } from "../lib/util";
import { AgentForm } from "./AgentForm";
import { AddMemberDialog, SquadForm } from "./SquadForm";
import { Dialog } from "./ui";

const DEADLINES: [string, string][] = [["600", "10m"], ["1800", "30m"], ["3600", "1h"], ["14400", "4h"], ["86400", "24h"]];
const ACCEPT = ".txt,.csv,.json,.md,.log,.tsv";
const MAX_CHARS = 12000;

function useDebounced<T>(v: T, ms = 350): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = window.setTimeout(() => setD(v), ms);
    return () => window.clearTimeout(t);
  }, [v, ms]);
  return d;
}

/**
 * Mission Composer: the goal, which squad gets it, who manages it, priority, budget, deadline and
 * text attachments. Every change is checked against the server (preflight) before Launch is
 * possible, so a mission that cannot run is never created; each problem comes with its fix.
 * Enter sends, Shift+Enter is a new line; the draft survives a reload.
 */
export function TaskComposer({ ws, squadId, onSquad, onSubmitted, autoFocus, rows = 2 }: {
  ws: Workspace; squadId?: string | null; onSquad?: (id: string) => void; onSubmitted?: (t: Task) => void; autoFocus?: boolean; rows?: number;
}) {
  const name = useAgentName();
  const qc = useQueryClient();
  const squads = useSquads(ws.id);
  const list = (squads.data || []).filter((s) => s.workspace_id === ws.id);
  const [text, setText] = usePref<string>(`draft:${ws.id}`, "");
  const [mode, setMode] = useState<"mission" | "note">("mission");
  const [opts, setOpts] = useState(false);
  const [pick, setPick] = useState<string>("");
  const squad = list.find((s) => s.id === (squadId || pick)) || list.find((s) => s.status === "active" && s.manager_id) || list[0];
  const [manager, setManager] = useState("");
  const [o, setO] = useState({ priority: "2", max_steps: "20", budget_usd: "1", timeout_s: "600" });
  const [files, setFiles] = useState<{ name: string; text: string }[]>([]);
  const [fileErr, setFileErr] = useState("");
  const [fix, setFix] = useState<PreflightFix | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setManager(""), [squad?.id]);

  const body = useDebounced({ workspace_id: ws.id, squad_id: squad?.id, manager_id: manager || undefined, text, budget_usd: o.budget_usd, attachments: files });
  const pf = usePreflight(body, mode === "mission");
  const checks = pf.data?.checks || [];
  const errors = checks.filter((c) => c.level === "error");
  const warns = checks.filter((c) => c.level === "warn");
  const stale = body.text !== text || body.squad_id !== squad?.id || body.budget_usd !== o.budget_usd || body.manager_id !== (manager || undefined) || body.attachments !== files;
  const canLaunch = mode === "note" ? !!text.trim() : !!text.trim() && !!pf.data?.ok && !stale && !pf.isFetching;

  // One key per mission draft: resending the same draft (double press, retry after a dropped response)
  // reuses it and gets the same mission back; any change to the draft starts a new key.
  const reqKey = useRef<{ sig: string; key: string }>({ sig: "", key: "" });
  const missionKey = (payload: unknown) => {
    const sig = JSON.stringify(payload);
    if (reqKey.current.sig !== sig) reqKey.current = { sig, key: newRequestKey() };
    return reqKey.current.key;
  };
  const send = useAction(
    () => {
      if (mode !== "mission") return post(`/api/wf/workspaces/${ws.id}/notes`, { text });
      const payload = {
        text, manager_id: manager || undefined, priority: Number(o.priority), max_steps: Number(o.max_steps), budget_usd: o.budget_usd,
        timeout_s: Number(o.timeout_s), attachments: files.length ? files : undefined,
      };
      const path = `/api/wf/squads/${squad!.id}/missions`;
      return postOnce<Task>(path, payload, missionKey([path, payload]));
    },
    ["workspaces", "missions", "tasks", "squads"],
    mode === "mission" ? "Mission 已交给主管" : undefined,
  );
  const submit = () => {
    if (!canLaunch || send.isPending) return;
    send.mutate(undefined, {
      onSuccess: (t) => {
        reqKey.current = { sig: "", key: "" };
        setText("");
        setFiles([]);
        qc.invalidateQueries({ queryKey: ["timeline", ws.id] });
        if (mode === "mission") onSubmitted?.(t as Task);
      },
    });
  };
  const attach = async (list: FileList | null) => {
    setFileErr("");
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) {
      if (next.length >= 5) { setFileErr("最多 5 个附件"); break; }
      const t = await f.text();
      if (next.reduce((n, x) => n + x.text.length, 0) + t.length > MAX_CHARS) { setFileErr(`附件文本合计不能超过 ${MAX_CHARS} 字符`); break; }
      next.push({ name: f.name, text: t });
    }
    setFiles(next);
    if (input.current) input.current.value = "";
  };
  const team = squad ? [squad.manager_id, ...squad.members].filter(Boolean) as string[] : [];

  return (
    <div className="composer">
      <div className="row gap6 to wrap">
        <div className="seg">
          <button aria-pressed={mode === "mission"} onClick={() => setMode("mission")}>Mission</button>
          <button aria-pressed={mode === "note"} onClick={() => setMode("note")}>Note</button>
        </div>
        {mode === "mission" && (
          <>
            <label className="row gap4">Squad
              <select className="input sm" value={squad?.id || ""} onChange={(e) => (setPick(e.target.value), onSquad?.(e.target.value))} aria-label="Squad">
                {!list.length && <option value="">— 没有 Squad —</option>}
                {list.map((s) => <option key={s.id} value={s.id}>{s.name}{s.status === "paused" ? "（暂停）" : ""}</option>)}
              </select>
            </label>
            <label className="row gap4">Manager
              <select className="input sm" value={manager || squad?.manager_id || ""} onChange={(e) => setManager(e.target.value === squad?.manager_id ? "" : e.target.value)} aria-label="Manager" disabled={!squad}>
                {!squad?.manager_id && <option value="">— 未指定 —</option>}
                {team.map((id) => <option key={id} value={id}>{name(id)}{id === squad?.manager_id ? " · default" : ""}</option>)}
              </select>
            </label>
          </>
        )}
        {mode === "note" && <span>所有人可见，不会启动任务</span>}
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
      {files.length > 0 && (
        <div className="chips">
          {files.map((f, i) => (
            <span key={i} className="tag">{f.name} · {f.text.length}c<button className="btn ghost xs icon" aria-label="移除附件" onClick={() => setFiles(files.filter((_, k) => k !== i))}><X size={10} /></button></span>
          ))}
        </div>
      )}
      {fileErr && <div className="bad xs">{fileErr}</div>}
      {mode === "mission" && <PreflightLine checks={checks} errors={errors} warns={warns} loading={pf.isFetching || stale} hasText={!!text.trim()} onFix={setFix} planner={pf.data?.planner} executors={pf.data?.executors.length || 0} />}
      <div className="row gap6">
        {mode === "mission" && (
          <>
            <button className="btn ghost sm icon" onClick={() => input.current?.click()} title="添加文本附件（txt/csv/json/md），内容会一起交给主管" aria-label="添加附件"><Paperclip size={12} /></button>
            <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => attach(e.target.files)} />
            <button className="btn ghost sm" onClick={() => setOpts(!opts)} aria-expanded={opts} title="优先级、预算、截止时间">
              <SlidersHorizontal size={12} />
              <span className="mono">P{o.priority} · ${o.budget_usd} · {DEADLINES.find(([v]) => v === o.timeout_s)?.[1] || `${o.timeout_s}s`}</span>
            </button>
          </>
        )}
        <div className="grow" />
        <button className={`btn primary ${send.isPending ? "loading" : ""}`} onClick={submit} disabled={!canLaunch || send.isPending} title={errors[0]?.text}>
          <Send size={14} /> {mode === "mission" ? "Launch" : "Post"}
        </button>
      </div>
      {opts && mode === "mission" && (
        <div className="row wrap gap12 xs muted">
          <label className="row gap4">Priority
            <select className="input sm" value={o.priority} onChange={(e) => setO({ ...o, priority: e.target.value })}>
              {[["0", "P0 Urgent"], ["1", "P1 High"], ["2", "P2 Normal"], ["3", "P3 Low"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="row gap4">Budget $ <input className="input sm mono" style={{ width: 64 }} value={o.budget_usd} onChange={(e) => setO({ ...o, budget_usd: e.target.value })} /></label>
          <label className="row gap4">Deadline
            <select className="input sm" value={o.timeout_s} onChange={(e) => setO({ ...o, timeout_s: e.target.value })}>
              {DEADLINES.map(([v, l]) => <option key={v} value={v}>in {l}</option>)}
            </select>
          </label>
          <label className="row gap4">Max steps <input className="input sm mono" style={{ width: 50 }} value={o.max_steps} onChange={(e) => setO({ ...o, max_steps: e.target.value.replace(/\D/g, "") })} /></label>
        </div>
      )}
      {fix && <FixDialog fix={fix} ws={ws} squad={squad} onClose={() => setFix(null)} onSquad={(id) => (setPick(id), onSquad?.(id))} />}
    </div>
  );
}

const FIX_LABEL: Record<PreflightFix, string> = {
  create_squad: "Create squad", configure_squad: "Configure squad", resume_squad: "Resume squad", assign_manager: "Assign manager",
  add_agent: "Add agent", create_agent: "Create agent", configure_manager: "Configure manager",
};

/** One line when all is well; otherwise each blocking reason with the button that fixes it. */
function PreflightLine({ checks, errors, warns, loading, hasText, onFix, planner, executors }: {
  checks: PreflightCheck[]; errors: PreflightCheck[]; warns: PreflightCheck[]; loading: boolean; hasText: boolean; onFix: (f: PreflightFix) => void;
  planner?: string | null; executors: number;
}) {
  if (!checks.length) return <div className="pf muted xs">{loading ? "检查中…" : ""}</div>;
  if (errors.length)
    return (
      <div className="pf col gap4">
        {errors.map((c) => (
          <div key={c.key} className="pf-row bad">
            <AlertTriangle size={12} />
            <span className="grow">{c.text}</span>
            {c.fix && <button className="btn xs" onClick={() => onFix(c.fix!)}>{FIX_LABEL[c.fix]}</button>}
            {c.key === "executors" && <button className="btn xs" onClick={() => onFix("create_agent")}>Create agent</button>}
          </div>
        ))}
      </div>
    );
  return (
    <div className="pf row gap6 xs wrap">
      <span className={hasText ? "ok row gap4" : "muted row gap4"}><Check size={12} /> {hasText ? "Ready" : "Squad ready"}</span>
      <span className="muted">· {executors} agents can execute · {planner === "model" ? "model planner" : "rule planner"}</span>
      {warns.map((c) => <span key={c.key} className="warn">· {c.text}</span>)}
    </div>
  );
}

function FixDialog({ fix, ws, squad, onClose, onSquad }: { fix: PreflightFix; ws: Workspace; squad?: Squad; onClose: () => void; onSquad: (id: string) => void }) {
  const resume = useAction(() => post(`/api/wf/squads/${squad!.id}/status`, { status: "active" }), ["squads", "preflight"], "Squad 已恢复");
  const add = useAction((id: string) => post(`/api/wf/squads/${squad!.id}/members`, { agent_id: id }), ["squads", "agents", "preflight"], "已加入 Squad");
  const agents = useAgents();
  useEffect(() => {
    if (fix === "resume_squad" && squad) resume.mutate(undefined, { onSettled: onClose });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (fix === "resume_squad") return null;
  if (fix === "create_squad" || !squad) return <SquadForm workspaceId={ws.id} onClose={onClose} onSaved={(s) => onSquad(s.id)} />;
  if (fix === "add_agent") return <AddMemberDialog squad={squad} onClose={onClose} />;
  if (fix === "create_agent") return <AgentForm onClose={onClose} onSaved={(a) => add.mutate(a.id)} />;
  if (fix === "configure_manager") {
    const m = agents.data?.find((a) => a.id === squad.manager_id);
    return m ? <AgentForm agent={m} onClose={onClose} /> : <SquadForm squad={squad} onClose={onClose} />;
  }
  return <SquadForm squad={squad} onClose={onClose} />;
}

/** "New mission" from anywhere: pick the workspace and squad, write the goal, land in its workspace. */
export function NewMissionDialog({ onClose, workspaceId, squadId }: { onClose: () => void; workspaceId?: string; squadId?: string }) {
  const ws = useWorkspaces();
  const squads = useSquads();
  const spaces = (ws.data || []).filter((w) => !w.archived);
  const firstReady = useMemo(() => squads.data?.find((s) => s.ready && s.status === "active"), [squads.data]);
  const [id, setId] = useState(workspaceId || "");
  const [sq, setSq] = useState(squadId || "");
  const wid = id || firstReady?.workspace_id || spaces[0]?.id || "";
  const current = spaces.find((w) => w.id === wid);
  const [creating, setCreating] = useState(false);
  return (
    <Dialog title="New mission" onClose={onClose} wide>
      {!spaces.length ? (
        <div className="note row sp">
          <span>还没有工作区和 Squad。先建一个 Squad（一位主管加几位有技能的员工）。</span>
          <button className="btn primary sm" onClick={() => setCreating(true)}>Create squad</button>
        </div>
      ) : (
        <>
          {spaces.length > 1 && (
            <label className="field">
              <span>Workspace</span>
              <select className="input" value={wid} onChange={(e) => (setId(e.target.value), setSq(""))}>
                {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>
          )}
          {current && (
            <div>
              <TaskComposer ws={current} squadId={sq || (firstReady?.workspace_id === wid ? firstReady.id : undefined)} onSquad={setSq} autoFocus rows={4}
                onSubmitted={(t) => (onClose(), go("workspace", t.workspace_id, { m: t.id, ...(t.squad_id ? { s: t.squad_id } : {}) }))} />
            </div>
          )}
        </>
      )}
      {creating && <SquadForm onClose={() => setCreating(false)} />}
    </Dialog>
  );
}
