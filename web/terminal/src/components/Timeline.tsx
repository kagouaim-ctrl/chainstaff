import { ArrowRight, Blocks, CircleAlert, Hammer, ShieldCheck, Terminal } from "lucide-react";
import { memo } from "react";
import type { Message, TaskStatus } from "../lib/types";
import { fmtTime, go, short } from "../lib/util";
import { Addr, StatusBadge } from "./ui";

const TASK_EVENT: Record<string, TaskStatus> = {
  "task.created": "pending",
  "task.assigned": "assigned",
  "task.completed": "completed",
  "task.failed": "failed",
  "task.cancelled": "cancelled",
  "task.retry": "pending",
};

const DELIVERY: Record<string, string> = { queued: "warn", delivered: "info", processed: "ok", failed: "bad", expired: "bad" };

/** One timeline entry. Each kind has its own shape; only human instructions look like chat. */
export const TimelineItem = memo(function TimelineItem({ m, name, onApprove, onTask }: {
  m: Message;
  name: (id?: string | null) => string;
  onApprove?: (approvalId: number) => void;
  onTask?: (taskId: string) => void;
}) {
  const t = <div className="t">{fmtTime(m.at, false)}</div>;
  const taskLink = m.task_id && (
    <button className="btn ghost sm mono" onClick={() => (onTask ? onTask(m.task_id!) : go("tasks", m.task_id!))} title="打开任务">
      {m.task_id}
    </button>
  );
  const delivery = m.recipient && m.status && <span className={`badge ${DELIVERY[m.status] || ""}`} title={`投递状态，尝试 ${m.attempts} 次`}>{m.status}</span>;

  if (m.kind === "user")
    return (
      <div className="ev user">
        {t}
        <div className="col" style={{ gap: 2 }}>
          <div className="line">
            <span className="who acc">You</span>
            <span className="muted">{m.event === "user.note" ? "note" : "instruction"}</span>
            {taskLink}
          </div>
          <div className="bubble">{m.text}</div>
        </div>
      </div>
    );

  if (m.kind === "agent") {
    if (m.event === "task.assign" || m.event === "handoff" || m.event === "task.result" || m.event === "goal.submitted") {
      const label = { "task.assign": "assign", handoff: "handoff", "task.result": "result", "goal.submitted": "goal" }[m.event];
      return (
        <div className="ev">
          {t}
          <div className="line">
            <span className="tag">{label}</span>
            <span className="who">{name(m.sender)}</span>
            <ArrowRight size={12} className="muted" />
            <span className="who">{name(m.recipient)}</span>
            <span className="dim ellipsis grow">{m.text}</span>
            {delivery}
          </div>
        </div>
      );
    }
    return (
      <div className="ev msg">
        {t}
        <div className="col" style={{ gap: 2 }}>
          <div className="line">
            <span className="who">{name(m.sender)}</span>
            {m.recipient && (
              <>
                <ArrowRight size={12} className="muted" />
                <span className="who">{name(m.recipient)}</span>
              </>
            )}
            {m.event === "plan.created" && <span className="badge info">plan · {m.data?.planner === "model" ? "model" : "rules"}</span>}
            {m.event === "plan.expanded" && <span className="badge info">delegation</span>}
            {m.event === "goal.review" && <span className="badge warn">review</span>}
            {delivery}
          </div>
          <div className="body-text">{m.text}</div>
        </div>
      </div>
    );
  }

  if (m.kind === "task") {
    const st = TASK_EVENT[m.event];
    return (
      <div className="ev">
        {t}
        <div className="line">
          <Hammer size={12} className="muted" />
          {st ? <StatusBadge status={st} /> : <span className="tag">{m.event}</span>}
          <span className="ellipsis grow">{m.text}</span>
          {m.data?.secs !== undefined && <span className="mono muted">{m.data.secs}s</span>}
          {taskLink}
        </div>
      </div>
    );
  }

  if (m.kind === "tool") {
    const d = m.data || {};
    const err = m.event === "tool.error";
    if (m.event === "tool.start")
      return (
        <div className="ev">
          {t}
          <div className="line">
            <Terminal size={12} className="muted" />
            <span className="who">{name(m.sender)}</span>
            <span className="mono dim">{m.text}</span>
            <span className="mono muted ellipsis grow">{JSON.stringify(d.input)}</span>
          </div>
        </div>
      );
    return (
      <div className="ev">
        {t}
        <details>
          <summary>
            <Terminal size={12} className={err ? "bad" : "ok"} />
            <span className="mono">{d.skill}</span>
            <span className={err ? "bad ellipsis" : "dim ellipsis"}>{m.text}</span>
            <span className="muted" style={{ marginLeft: "auto" }}>{(d.logs || []).length} log lines</span>
          </summary>
          <pre>{[...(d.logs || []), d.output_preview ? `\n→ ${d.output_preview}` : "", d.error ? `\n✕ ${d.error}` : ""].join("\n")}</pre>
        </details>
      </div>
    );
  }

  if (m.kind === "chain") {
    const d = m.data || {};
    return (
      <div className="ev chain">
        {t}
        <div className="card">
          <div className="line">
            <Blocks size={13} className="acc" />
            <span className="who">On-chain</span>
            <StatusBadge kind="chain" status={d.status || "pending"} />
            <span className="dim ellipsis">{m.text}</span>
          </div>
          <div className="line mono muted" style={{ fontSize: 11 }}>
            <span>{d.network}</span>
            {d.tx_hash && <Addr value={d.tx_hash} n={8} url={d.tx_url} />}
            {d.block && <span>block {d.block}</span>}
            <span>hash {short(d.payload_hash, 8)}</span>
          </div>
        </div>
      </div>
    );
  }

  // system
  const approval = m.data?.approval && m.event === "approval.requested";
  return (
    <div className="ev sys">
      {t}
      <div className="line">
        {approval ? <ShieldCheck size={12} className="warn" /> : m.event.includes("error") || m.event.includes("expired") ? <CircleAlert size={12} className="bad" /> : null}
        <span className="ellipsis grow">{m.text}</span>
        {approval && onApprove && (
          <button className="btn sm primary" onClick={() => onApprove(m.data.approval)}>
            审批
          </button>
        )}
        {taskLink}
      </div>
    </div>
  );
});
