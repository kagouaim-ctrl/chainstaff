import { RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import { post } from "../lib/api";
import { useAction, useAgentName } from "../lib/queries";
import type { Message, Task, TaskDetail } from "../lib/types";
import { duration, fmtTime, short } from "../lib/util";
import { Addr, Avatar, EmptyState, Json, Pipe, Status } from "./ui";

const FINAL = ["completed", "failed", "cancelled"];

/** Dependency depth per task: tasks on one level have no unfinished dependency between them and run in parallel. */
export function levels(subs: Task[]): Task[][] {
  const byId = Object.fromEntries(subs.map((s) => [s.id, s]));
  const depth: Record<string, number> = {};
  const f = (id: string, seen = new Set<string>()): number => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const deps = byId[id]?.depends_on.filter((x) => byId[x]) || [];
    return (depth[id] = deps.length ? 1 + Math.max(...deps.map((x) => f(x, seen))) : 0);
  };
  subs.forEach((s) => f(s.id));
  const out: Task[][] = [];
  subs.forEach((s) => (out[depth[s.id]] ||= []).push(s));
  return out.filter(Boolean);
}

/** The plan as executed: one row per task, grouped by dependency level. */
export function ExecutionSteps({ d, selected, onSelect }: { d: TaskDetail; selected?: string | null; onSelect?: (id: string) => void }) {
  const name = useAgentName();
  const retry = useAction((tid: string) => post(`/api/wf/tasks/${tid}/retry`), ["task", "tasks", "missions"], "已重新排队");
  const lanes = useMemo(() => levels(d.subtasks), [d.subtasks]);
  const open = !FINAL.includes(d.root.status);
  if (!d.subtasks.length) return <EmptyState title={d.root.status === "pending" || d.root.status === "running" ? "主管正在拆分任务…" : "没有子任务"} />;
  return (
    <div className="col gap12">
      {lanes.map((lane, i) => (
        <div key={i} className="col gap2">
          <span className="label">{i === 0 ? "Stage 1" : `Stage ${i + 1} · after stage ${i}`}{lane.length > 1 ? ` · ${lane.length} in parallel` : ""}</span>
          {lane.map((s) => {
            const waiting = s.depends_on.filter((x) => d.subtasks.find((y) => y.id === x && !FINAL.includes(y.status))).length;
            return (
              <div key={s.id} className={`list-row ${onSelect ? "click" : ""}`} onClick={() => onSelect?.(s.id)} aria-selected={selected === s.id} style={selected === s.id ? { background: "var(--accent-bg)" } : undefined}>
                <Pipe status={s.status} />
                <div className="col gap2 grow">
                  <span className="ellipsis" title={s.title}>{s.title}</span>
                  <span className="muted xs ellipsis">
                    {name(s.assigned_agent)} · <span className="mono">{s.skill}</span>
                    {s.attempts > 1 && ` · attempt ${s.attempts}`}
                    {waiting > 0 && s.status === "pending" && ` · waiting on ${waiting}`}
                  </span>
                  {s.error && <span className="bad xs clamp2">{s.error}</span>}
                </div>
                <span className="mono xs muted">{s.started_at ? duration(s.started_at, s.finished_at) : ""}</span>
                <Status status={s.status} />
                {s.status === "failed" && open && (
                  <button className="btn ghost xs icon" title="重试这个任务" aria-label="重试" onClick={(e) => (e.stopPropagation(), retry.mutate(s.id))}><RotateCcw size={11} /></button>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** Dependency graph. Drawn only when asked for, never by default. */
export function TaskGraph({ d }: { d: TaskDetail }) {
  const name = useAgentName();
  const lanes = useMemo(() => levels(d.subtasks), [d.subtasks]);
  if (!d.subtasks.length) return <EmptyState title="还没有子任务" />;
  return (
    <div className="graph">
      {lanes.map((lane, i) => (
        <div key={i} className="col gap4">
          {i > 0 && <div className="arrow">↓</div>}
          <span className="label">Level {i}{lane.length > 1 ? ` · ${lane.length} parallel` : ""}</span>
          <div className="lane">
            {lane.map((s) => (
              <div key={s.id} className={`node ${s.status}`} title={s.depends_on.length ? `depends on ${s.depends_on.join(", ")}` : "no dependencies"}>
                <span className="ellipsis strong">{s.title}</span>
                <span className="row sp gap4"><span className="muted ellipsis">{name(s.assigned_agent)}</span><Status status={s.status} /></span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="muted xs">{d.graph.edges.length} dependency edges · {d.subtasks.length} tasks</div>
    </div>
  );
}

/** Tool runs, collapsed by default. Full technical logs live only here. */
export function ExecutionLogs({ d, taskId }: { d: TaskDetail; taskId?: string | null }) {
  const name = useAgentName();
  const [all, setAll] = useState(false);
  const runs = useMemo(() => d.messages.filter((m) => m.kind === "tool" && m.event !== "tool.start" && (!taskId || m.task_id === taskId)), [d.messages, taskId]);
  const starts = useMemo(() => new Map(d.messages.filter((m) => m.event === "tool.start").map((m) => [m.task_id, m])), [d.messages]);
  if (!runs.length) return <EmptyState title={taskId ? "这个任务还没有工具执行" : "还没有工具执行"} />;
  return (
    <div className="col gap6">
      <div className="row sp">
        <span className="label">{runs.length} tool runs</span>
        <button className="btn ghost xs" onClick={() => setAll(!all)}>{all ? "Collapse all" : "Expand all"}</button>
      </div>
      <div>
        {runs.map((m) => (
          <details key={`${m.id}-${all}`} className="log" open={all}>
            <summary>
              <span className={m.event === "tool.error" ? "bad" : "ok"}>●</span>
              <span className="mono">{m.data?.skill}</span>
              <span className="muted xs">{name(m.sender)}</span>
              <span className="muted ellipsis grow xs">{m.text}</span>
              <span className="muted mono xs">{fmtTime(m.at, false)}</span>
            </summary>
            <pre>{logText(m, starts.get(m.task_id))}</pre>
          </details>
        ))}
      </div>
    </div>
  );
}

function logText(m: Message, start?: Message): string {
  const d = m.data || {};
  return [
    start?.data?.input ? `input ${JSON.stringify(start.data.input)}` : "",
    ...(d.logs || []),
    d.output_preview ? `→ ${d.output_preview}` : "",
    d.error ? `✕ ${d.error}` : "",
    d.secs !== undefined ? `(${d.secs}s)` : "",
  ].filter(Boolean).join("\n");
}

/** Agent ↔ agent messages with delivery state; the bus, not the story. */
export function MessagesTable({ d }: { d: TaskDetail }) {
  const name = useAgentName();
  const rows = d.messages.filter((m) => m.recipient);
  if (!rows.length) return <EmptyState title="还没有员工之间的消息" />;
  const tone: Record<string, string> = { processed: "ok", expired: "bad", failed: "bad", queued: "acc", delivered: "info" };
  return (
    <table className="dt inner">
      <thead><tr><th>#</th><th>Type</th><th>From → To</th><th>Delivery</th><th className="r">Tries</th></tr></thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.id} title={m.text}>
            <td className="mono muted">{m.id}</td>
            <td><span className="tag">{m.event}</span></td>
            <td className="ellipsis" style={{ maxWidth: 170 }}>{name(m.sender)} → {name(m.recipient)}</td>
            <td><span className={`st ${tone[m.status || ""] || ""}`}><i /><span className="t">{m.status}</span></span></td>
            <td className="r mono">{m.attempts}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Deliverables: the mission report, findings, and every task's own result hash; or one task's output. */
export function ResultView({ d, taskId }: { d: TaskDetail; taskId?: string | null }) {
  const name = useAgentName();
  const t = taskId ? d.subtasks.find((s) => s.id === taskId) : null;
  if (t)
    return (
      <div className="col">
        <dl className="kv">
          <dt>Task</dt><dd>{t.title}</dd>
          <dt>Agent</dt><dd>{name(t.assigned_agent)}</dd>
          <dt>Result hash</dt><dd><Addr value={t.result_hash} n={10} /></dd>
        </dl>
        {t.error && <div className="note bad">{t.error}</div>}
        {t.result ? (t.result.report_md ? <div className="report">{t.result.report_md}</div> : <Json value={t.result} />) : <div className="muted sm">还没有结果</div>}
      </div>
    );
  const r = d.root.result;
  if (!r) return <EmptyState title="还没有汇总结果">所有任务结束后，主管把结果汇总成报告交给你审批。</EmptyState>;
  return (
    <div className="col">
      <dl className="kv">
        <dt>Result hash</dt><dd><Addr value={d.root.result_hash} n={10} /></dd>
        <dt>State</dt><dd>{d.root.status === "completed" ? "已审批（链下完成）" : d.root.status === "review" ? "等待你审批" : d.root.status}</dd>
      </dl>
      {r.findings?.length > 0 && (
        <div className="note acc col gap4">
          <span className="label">Findings · {r.findings.length}</span>
          {r.findings.map((f: string, i: number) => <span key={i} className="sm">• {f}</span>)}
        </div>
      )}
      {r.report_md && <div className="report">{r.report_md}</div>}
      {r.output && <Json value={r.output} />}
      {r.subtasks?.length > 0 && (
        <>
          <span className="label">Per-task results</span>
          <table className="dt inner">
            <tbody>
              {r.subtasks.map((s: any) => (
                <tr key={s.id}>
                  <td><Avatar name={name(s.agent)} /></td>
                  <td className="ellipsis" style={{ maxWidth: 150 }} title={s.title}>{s.title}</td>
                  <td><Status status={s.status} /></td>
                  <td className="mono muted">{s.result_hash ? short(s.result_hash, 6) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
