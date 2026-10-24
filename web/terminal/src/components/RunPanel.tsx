import { ArrowRight, Play, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { post } from "../lib/api";
import { useAction, useAgentName, useSkills, useWorkspaces } from "../lib/queries";
import type { AgentDetail, Task } from "../lib/types";
import { go } from "../lib/util";
import { Seg } from "./ui";

export type RunMode = "run" | "assign";

/**
 * The two ways to give an agent work, without leaving the page:
 * - Run: one of its skills, right now, on this agent (a one-task mission, still reviewed by you).
 * - Assign: a mission for the squad it belongs to; that squad's AI manager plans and delegates it.
 */
export function RunPanel({ agent, mode, onMode, presetSkill, onDone }: {
  agent: AgentDetail;
  mode: RunMode;
  onMode: (m: RunMode) => void;
  presetSkill?: string;
  onDone?: () => void;
}) {
  const skills = useSkills();
  const name = useAgentName();
  const ws = useWorkspaces();
  const spaces = useMemo(() => (ws.data || []).filter((w) => !w.archived && (w.members.includes(agent.id) || w.manager_id === agent.id)), [ws.data, agent.id]);
  const runnable = agent.skills.filter((s) => s !== "data_report");
  const [skill, setSkill] = useState(presetSkill && runnable.includes(presetSkill) ? presetSkill : runnable.find((s) => !agent.can_run[s]) || runnable[0] || "");
  useEffect(() => {
    if (presetSkill && runnable.includes(presetSkill)) setSkill(presetSkill);
  }, [presetSkill]); // eslint-disable-line react-hooks/exhaustive-deps
  const [wsId, setWsId] = useState("");
  const space = spaces.find((w) => w.id === wsId) || spaces[0];
  const [input, setInput] = useState<Record<string, string>>({});
  const [goal, setGoal] = useState("");
  const [title, setTitle] = useState("");
  const [o, setO] = useState({ priority: "2", budget_usd: "1", timeout_s: "600", max_steps: "20" });
  const [done, setDone] = useState<Task | null>(null);

  const def = skills.data?.find((s) => s.id === skill);
  const props: Record<string, any> = def?.input_schema?.properties || {};
  const required: string[] = def?.input_schema?.required || [];
  const blocked = skill ? agent.can_run[skill] : "这个员工没有可以单独运行的技能";
  const body = () => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input)) {
      if (v === "" || !(k in props)) continue;
      out[k] = props[k]?.type === "integer" ? Number(v) : props[k]?.type === "array" ? v.split(",").map((x) => x.trim()).filter(Boolean) : v;
    }
    return out;
  };
  const keys = ["agents", "missions", "tasks", "workspaces", "task"];
  const run = useAction(
    () => post<Task>(`/api/wf/agents/${agent.id}/run`, { skill, input: body(), title, workspace_id: space?.id, priority: Number(o.priority), budget_usd: o.budget_usd, timeout_s: Number(o.timeout_s) }),
    keys,
    `已派给 ${agent.name}`,
  );
  const assign = useAction(
    () => post<Task>(`/api/wf/workspaces/${space!.id}/goals`, { text: goal, priority: Number(o.priority), budget_usd: o.budget_usd, timeout_s: Number(o.timeout_s), max_steps: Number(o.max_steps) }),
    keys,
    "Mission 已提交",
  );
  const manager = space?.manager_id;
  const canRun = !!skill && !blocked && required.every((k) => (input[k] || "").trim()) && agent.status === "active" && !!space;
  const canAssign = !!goal.trim() && !!space && !!manager;

  if (done)
    return (
      <div className="col gap6">
        <div className="note acc row sp">
          <span className="ellipsis">已创建 <span className="mono">{done.id}</span> · {done.title}</span>
          <button className="btn sm primary" onClick={() => go("workspace", done.workspace_id, { m: done.id })}>Open mission <ArrowRight size={12} /></button>
        </div>
        <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => (setDone(null), setGoal(""), setInput({}), setTitle(""))}>再派一个</button>
      </div>
    );

  return (
    <div className="col gap6">
      <div className="row sp">
        <Seg<RunMode> value={mode} onChange={onMode} options={[["run", "Run task"], ["assign", "Assign mission"]]} />
        {spaces.length > 1 && (
          <select className="input sm" value={space?.id} onChange={(e) => setWsId(e.target.value)} aria-label="工作区" style={{ maxWidth: 150 }}>
            {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
      </div>
      {!spaces.length ? (
        <div className="note">{agent.name} 还不在任何工作区里。到 Workspace 把它加进一个 Squad 后才能派活。</div>
      ) : mode === "run" ? (
        <>
          {runnable.length === 0 ? (
            <div className="note">{agent.is_manager ? "主管不直接执行技能。用 Assign mission，让它拆分并分派给员工。" : "这个员工没有可以单独运行的技能（数据报告只在 Mission 里汇总上游结果）。"}</div>
          ) : (
            <>
              <div className="chips">
                {runnable.map((s) => (
                  <button key={s} className="chip" aria-pressed={s === skill} onClick={() => (setSkill(s), setInput({}))} title={agent.can_run[s] || ""}>
                    {skills.data?.find((x) => x.id === s)?.name || s}
                    {agent.can_run[s] && <span className="bad">!</span>}
                  </button>
                ))}
              </div>
              {blocked && <div className="bad xs">{blocked}</div>}
              {Object.entries(props).map(([k, p]) => (
                <label key={k} className="field">
                  <span>{k}{required.includes(k) ? " *" : ""} <span className="muted mono">{p.type}</span></span>
                  <input className="input mono sm" value={input[k] || ""} placeholder={p.description || (p.type === "array" ? "逗号分隔" : k === "address" ? "0x…" : "")} onChange={(e) => setInput({ ...input, [k]: e.target.value.trim() })} />
                </label>
              ))}
              <input className="input sm" placeholder="标题（可选）" value={title} onChange={(e) => setTitle(e.target.value)} />
            </>
          )}
        </>
      ) : (
        <>
          <textarea className="input" rows={3} placeholder="例如：Analyze this BSC contract 0x…" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <div className="muted xs">
            {manager ? <>由 <b className="dim">{name(manager)}</b> 在 <b className="dim">{space?.name}</b> 里拆分并分派；{agent.is_manager ? "" : `${agent.name} 会在需要它的技能时被分到任务。`}</> : <span className="warn">{space?.name} 还没有 AI 主管，不能接 Mission。</span>}
          </div>
        </>
      )}
      {!!spaces.length && (
        <div className="row wrap gap6 xs muted">
          <label className="row gap4">P
            <select className="input sm" value={o.priority} onChange={(e) => setO({ ...o, priority: e.target.value })} aria-label="优先级">
              {[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}
            </select>
          </label>
          <label className="row gap4">$<input className="input sm mono" style={{ width: 52 }} value={o.budget_usd} onChange={(e) => setO({ ...o, budget_usd: e.target.value })} aria-label="预算" /></label>
          <label className="row gap4">timeout<input className="input sm mono" style={{ width: 58 }} value={o.timeout_s} onChange={(e) => setO({ ...o, timeout_s: e.target.value.replace(/\D/g, "") })} aria-label="超时秒数" />s</label>
          {mode === "assign" && <label className="row gap4">steps<input className="input sm mono" style={{ width: 40 }} value={o.max_steps} onChange={(e) => setO({ ...o, max_steps: e.target.value.replace(/\D/g, "") })} aria-label="最大步数" /></label>}
          <div className="grow" />
          {mode === "run" ? (
            <button className="btn primary sm" disabled={!canRun || run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (t) => (setDone(t as Task), onDone?.()) })}>
              <Play size={12} /> Run
            </button>
          ) : (
            <button className="btn primary sm" disabled={!canAssign || assign.isPending} onClick={() => assign.mutate(undefined, { onSuccess: (t) => (setDone(t as Task), onDone?.()) })}>
              <Send size={12} /> Assign
            </button>
          )}
        </div>
      )}
    </div>
  );
}
