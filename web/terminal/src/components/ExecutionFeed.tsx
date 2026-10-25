import { ArrowRight, Blocks, Bot, CheckCircle2, ChevronRight, CircleAlert, ExternalLink, FileText, MessageSquare, Repeat, ShieldCheck, Wrench } from "lucide-react";
import { memo, useMemo } from "react";
import { useAgentLookup, useAgentName } from "../lib/queries";
import type { Message, Task, TaskDetail } from "../lib/types";
import { fmtTime, short } from "../lib/util";
import { ApprovalPanel } from "./ApprovalPanel";
import { Avatar, Pipe } from "./ui";

// Bus bookkeeping and duplicates of other rows: kept for Logs and Messages, not for the feed.
// Streamed model text and call bookkeeping live on the Live board; the feed keeps each reply and each tool result.
const HIDDEN = new Set(["task.created", "task.assigned", "task.result", "tool.start", "user.instruction", "goal.submitted", "tool.manual",
  "agent.delta", "model.start", "tool.call"]);

/**
 * Mission Execution Feed: what the squad did, in order, each kind of event in its own shape.
 * Planning is a plan card, assignments are compact task rows, handoffs read as one agent passing
 * work to another, tool runs fold open, approvals are an action panel, results are a preview.
 * Raw JSON stays in the inspector's Logs.
 */
export const ExecutionFeed = memo(function ExecutionFeed({ d, onTask }: { d: TaskDetail; onTask: (id: string) => void }) {
  const tasks = useMemo(() => new Map(d.subtasks.map((s, i) => [s.id, { ...s, n: i + 1 }])), [d.subtasks]);
  const rows = useMemo(() => d.messages.filter((m) => !HIDDEN.has(m.event)), [d.messages]);
  const pending = d.approvals.filter((a) => a.status === "pending");
  if (!rows.length) return <div className="feed-empty muted sm">{d.root.status === "pending" ? "等待主管接手…" : "还没有进展"}</div>;
  return (
    <div className="feed">
      {rows.map((m) => {
        if (m.event === "approval.requested") {
          const a = pending.find((x) => x.id === m.data?.approval);
          if (a) return <div key={m.id} className="feed-item"><ApprovalPanel approval={a} root={d.root} /></div>;
        }
        return <FeedRow key={m.id} m={m} task={m.task_id ? tasks.get(m.task_id) : undefined} tasks={tasks} root={d.root} onTask={onTask} />;
      })}
    </div>
  );
});

type T = Task & { n: number };

function FeedRow({ m, task, tasks, root, onTask }: { m: Message; task?: T; tasks: Map<string, T>; root: Task; onTask: (id: string) => void }) {
  const name = useAgentName();
  const agent = useAgentLookup();
  const who = (id?: string | null) => (
    <span className="who"><Avatar name={name(id)} mgr={agent(id)?.is_manager} />{name(id)}</span>
  );
  const time = <span className="when">{fmtTime(m.at, false)}</span>;
  const open = task ? () => onTask(task.id) : undefined;
  const ref = (t?: T) => t && <button className="task-ref" onClick={(e) => (e.stopPropagation(), onTask(t.id))}><span className="mono">#{t.n}</span> {t.title}</button>;

  switch (m.event) {
    case "agent.started":
      return <div className="feed-line info">{time}<Bot size={13} />{who(m.sender)}<span className="dim">{m.text}</span></div>;
    case "plan.created": {
      const steps: any[] = m.data?.steps || [];
      return (
        <div className="feed-card plan">
          <div className="feed-card-h">{time}<Bot size={13} className="info" />{who(m.sender)}<span className="dim">{m.data?.planner === "direct" ? "直接派活" : `拆成 ${steps.length} 个任务`}</span>
            <span className="tag">{m.data?.planner === "model" ? "model plan" : m.data?.planner === "direct" ? "direct" : "rule plan"}</span></div>
          <div className="plan-steps">
            {steps.map((s) => {
              const t = tasks.get(s.task);
              return (
                <button key={s.key} className="plan-step" onClick={() => t && onTask(t.id)}>
                  <span className="mono muted">{t?.n ?? "·"}</span>
                  <span className="ellipsis grow">{s.title}</span>
                  <ArrowRight size={11} className="muted" />
                  <span className="ellipsis" style={{ maxWidth: 140 }}>{name(s.agent)}</span>
                  {s.depends_on?.length > 0 && <span className="muted xs mono">after {s.depends_on.map((x: string) => `#${tasks.get(x)?.n ?? "?"}`).join(" ")}</span>}
                  {t && <Pipe status={t.status} />}
                </button>
              );
            })}
          </div>
        </div>
      );
    }
    case "plan.expanded":
    case "plan.note":
      return <div className="feed-line">{time}<Bot size={13} className="muted" />{who(m.sender)}<span className="dim">{m.text}</span></div>;
    case "task.assign":
      return (
        <div className="feed-task" onClick={open}>
          {time}{who(m.sender)}<ArrowRight size={11} className="muted" />{who(m.recipient)}
          {ref(task)}{task?.skill && <span className="tag">{task.skill}</span>}
          <span className="grow" />{task && <Pipe status={task.status} />}
        </div>
      );
    case "agent.working":
      return <div className="feed-line acc" onClick={open}>{time}<ChevronRight size={13} />{who(m.sender)}<span className="dim">started</span>{ref(task)}{/交接/.test(m.text) && <span className="muted xs">{m.text.replace(/^[^（]*（|）$/g, "")}</span>}</div>;
    case "handoff": {
      const from = tasks.get(m.data?.task);
      return (
        <div className={`feed-msg ${m.data?.error ? "bad" : ""}`} onClick={open}>
          {time}
          <MessageSquare size={13} className={m.data?.error ? "bad" : "info"} />
          {who(m.sender)}<ArrowRight size={11} className="muted" />{who(m.recipient)}
          <span className="msg-body">
            {m.data?.error ? <>{from?.title || m.data?.skill} 没有完成：{m.data.error}</> : <>交接 <b>{from?.title || m.data?.skill}</b> 的结果{task ? <>，给 {ref(task)}</> : null}</>}
            {!m.data?.error && m.data?.result && <span className="muted xs"> · {preview(m.data.result)}</span>}
          </span>
        </div>
      );
    }
    case "agent.message":
    case "user.note":
      return <div className="feed-msg">{time}<MessageSquare size={13} className="muted" />{who(m.sender)}{m.recipient && <><ArrowRight size={11} className="muted" />{who(m.recipient)}</>}<span className="msg-body">{m.text}</span></div>;
    case "tool.done":
    case "tool.error":
      return (
        <details className="feed-tool">
          <summary>{time}<Wrench size={12} className={m.event === "tool.error" ? "bad" : "muted"} />{who(m.sender)}<span className="mono xs">{m.data?.skill}</span><span className={`xs ${m.event === "tool.error" ? "bad" : "muted"}`}>{m.text}</span></summary>
          <pre>{[...(m.data?.logs || []), m.data?.error ? `✕ ${m.data.error}` : ""].filter(Boolean).join("\n") || "—"}</pre>
        </details>
      );
    case "model.done":
      return (
        <details className="feed-tool">
          <summary>{time}<Bot size={12} className="info" />{who(m.sender)}<span className="xs muted">AI 推理 #{m.data?.call}</span>
            <span className="xs dim ellipsis">{(m.text || "").slice(0, 90)}</span>
            <span className="muted xs mono">{m.data?.secs}s · {(m.data?.input_tokens || 0) + (m.data?.output_tokens || 0)} tok · ${m.data?.cost_usd}</span></summary>
          <pre>{m.text}</pre>
        </details>
      );
    case "tool.result":
      return (
        <details className="feed-tool">
          <summary>{time}<Wrench size={12} className={m.data?.ok === false ? "bad" : "muted"} />{who(m.sender)}<span className="mono xs">{m.data?.tool}</span>
            <span className={`xs ${m.data?.ok === false ? "bad" : "muted"}`}>{m.data?.ok === false ? m.data?.error : `${m.data?.secs}s · ${m.data?.rpc_calls ?? 0} RPC`}</span></summary>
          <pre>{m.data?.output_preview || m.data?.error || "—"}</pre>
        </details>
      );
    case "tool.cancelled":
      return <div className="feed-line muted" onClick={open}>{time}<CircleAlert size={13} /><span>{m.text}</span></div>;
    case "task.completed":
      if (task)
        return <div className="feed-line ok" onClick={open}>{time}<CheckCircle2 size={13} />{who(m.sender || task.assigned_agent)}<span className="dim">completed</span>{ref(task)}{m.data?.secs !== undefined && <span className="muted xs mono">{m.data.secs}s · {short(m.data.result_hash, 6)}</span>}</div>;
      return <div className="feed-line ok strong">{time}<CheckCircle2 size={13} />Mission completed off-chain<span className="muted xs mono">{short(m.data?.result_hash, 8)}</span></div>;
    case "task.failed":
      return (
        <div className="feed-line bad" onClick={open}>
          {time}<CircleAlert size={14} /><span className="verb bad">Failed</span>
          {task ? <>{ref(task)}{task.assigned_agent && who(task.assigned_agent)}<span className="reason"><b>原因</b>{m.text.replace(/^.*?失败：/, "")}</span></> : <span>{m.text}</span>}
        </div>
      );
    case "task.reassigned":
      return <div className="feed-line acc" onClick={open}>{time}<Repeat size={13} />{who(m.sender)}<span className="dim">reassigned</span>{ref(task)}<ArrowRight size={11} className="muted" />{who(m.data?.to)}<span className="muted xs">{m.text.split("；")[0]}</span></div>;
    case "task.retry":
    case "task.queued":
    case "squad.holding":
    case "task.cancelled":
    case "message.expired":
    case "engine.error":
      return <div className={`feed-line ${m.event === "task.cancelled" || m.event === "task.queued" ? "muted" : m.event === "message.expired" || m.event === "engine.error" ? "bad" : "acc"}`} onClick={open}>{time}<CircleAlert size={13} /><span>{m.text}</span></div>;
    case "goal.review":
      return (
        <div className="feed-card result">
          <div className="feed-card-h">{time}<FileText size={13} className="acc" />{who(m.sender)}<span className="dim">汇总了所有成员的结果，提交审核</span></div>
          <div className="sm dim">{m.text}</div>
          {root.result?.findings?.length > 0 && <div className="xs">{root.result.findings.slice(0, 3).map((f: string, i: number) => <div key={i}>• {f}</div>)}</div>}
        </div>
      );
    case "approval.requested":
    case "approval.approved":
    case "approval.rejected":
      return <div className={`feed-line ${m.event === "approval.approved" ? "ok" : m.event === "approval.rejected" ? "bad" : "acc"}`}>{time}<ShieldCheck size={13} /><span>{m.text}</span></div>;
    case "chain.submitted":
    case "chain.confirmed":
    case "chain.failed":
      return (
        <div className={`feed-line ${m.event === "chain.confirmed" ? "ok" : m.event === "chain.failed" ? "bad" : "acc"}`}>
          {time}<Blocks size={13} /><span>{m.text}</span>
          {m.data?.tx_url && <a className="btn ghost xs" href={m.data.tx_url} target="_blank" rel="noreferrer"><ExternalLink size={10} /> tx</a>}
        </div>
      );
    default:
      return <div className="feed-line muted">{time}<span>{m.text}</span></div>;
  }
}

function preview(r: any): string {
  if (!r || typeof r !== "object") return "";
  const keys = ["symbol", "is_contract", "bnb", "risks", "findings", "count", "title"].filter((k) => k in r);
  return keys.map((k) => `${k}=${Array.isArray(r[k]) ? r[k].length : String(r[k]).slice(0, 24)}`).join(" · ");
}
