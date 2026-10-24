import { ArrowRight, Ban, Blocks, ExternalLink } from "lucide-react";
import { memo, useMemo } from "react";
import { post } from "../lib/api";
import { describe, nodeClass } from "../lib/events";
import { useAction, useAgentLookup, useAgentName, useMeta } from "../lib/queries";
import type { Message, Mission, TaskDetail } from "../lib/types";
import { duration, fmtTime, usd, useNow } from "../lib/util";
import { Avatar, Pipe, Priority, Progress, PROOF, Status } from "./ui";

const STAGE_LABEL: Record<Mission["stage"], [string, string]> = {
  planning: ["Planning", "info"], executing: ["Executing", "info"], review: ["Awaiting review", "acc"],
  completed: ["Completed", "ok"], failed: ["Failed", "bad"], cancelled: ["Cancelled", "mute"],
};

export function MissionStage({ m, badge }: { m: Pick<Mission, "stage">; badge?: boolean }) {
  const [l, tone] = STAGE_LABEL[m.stage];
  if (badge) return <span className={`badge ${tone === "mute" ? "" : tone}`}><span className="dot" />{l}</span>;
  return <span className={`st ${tone} ${m.stage === "planning" || m.stage === "executing" ? "live" : ""}`}><i /><span className="t">{l}</span></span>;
}

/** Name, state and the numbers that decide whether to step in: progress, who, how long, how much. */
export function MissionHeader({ m, d, compact, squadName }: { m: Mission; d?: TaskDetail; compact?: boolean; squadName?: string }) {
  const name = useAgentName();
  const agent = useAgentLookup();
  const meta = useMeta();
  const open = !["completed", "failed", "cancelled"].includes(m.status);
  useNow(1000, open);
  const cancel = useAction(() => post(`/api/wf/tasks/${m.id}/cancel`, {}), ["task", "tasks", "missions"], "Mission 已取消");
  const anchor = useAction(() => post(`/api/wf/tasks/${m.id}/anchor`), ["task", "records", "missions", "ledger"], "已广播到 BSC 测试网");
  const p = m.progress;
  const over = Number(m.budget_usd) > 0 && Number(m.cost_usd) >= Number(m.budget_usd);
  const [proofLabel, proofTone] = PROOF[m.proof.state];
  return (
    <div className="mh">
      <div className="mh-top">
        <div className="col gap8 grow">
          <div className="row gap8 wrap">
            <MissionStage m={m} badge />
            <span className="badge line"><Priority p={m.priority} /></span>
            {m.planner && <span className="tag">{m.planner === "direct" ? "direct" : m.planner === "model" ? "model plan" : "rule plan"}</span>}
            {squadName && <a className="tag" href={`#/squads/${m.squad_id}`} title="Assigned squad">{squadName}</a>}
          </div>
          <h1 className="mh-title ellipsis" title={m.name}>{m.name}</h1>
          {!compact && (
            <div className="mh-meta">
              <span className="mh-id">{m.id}</span>
              <i className="dot" />
              <span>Created <b className="tnum">{fmtTime(m.created_at)}</b> by <b>{m.created_by === "owner" ? "you" : m.created_by}</b></span>
              <i className="dot" />
              <span>Owner <b>{name(m.owner)}</b></span>
              {m.deadline_at && open && <><i className="dot" /><span>Deadline <b className="tnum">{fmtTime(m.deadline_at)}</b></span></>}
            </div>
          )}
        </div>
        <div className="row gap6" style={{ flex: "none" }}>
          {m.status === "completed" && m.proof.state === "offchain_completed" && meta.data?.engine.anchor && (
            <button className="btn sm" disabled={anchor.isPending} onClick={() => anchor.mutate(undefined)}><Blocks size={12} /> Anchor</button>
          )}
          {m.proof.tx_url && <a className="btn ghost sm" href={m.proof.tx_url} target="_blank" rel="noreferrer"><ExternalLink size={12} /> Tx</a>}
          {open && (
            <button className="btn ghost sm" disabled={cancel.isPending} onClick={() => confirm("取消这个 Mission 和它所有未完成的任务？") && cancel.mutate(undefined)}>
              <Ban size={12} /> Cancel
            </button>
          )}
        </div>
      </div>
      <div className="mh-grid">
        <div className="wide">
          <span className="label">Progress</span>
          <Progress done={p.completed} failed={p.failed} total={p.total} />
          <span className="col gap6"><span className="muted xs ellipsis">{p.total ? <>{p.completed} done · {p.running} running · {p.pending} queued{p.failed ? <> · <span className="bad">{p.failed} failed</span></> : null}</> : m.stage === "planning" ? "manager is planning" : "no tasks"}</span>{d && !compact && <AssignmentStrip d={d} />}</span>
        </div>
        <div>
          <span className="label">{squadName ? "Squad" : "Agents"}</span>
          <span className="avatars">{m.agents.slice(0, 6).map((id) => <Avatar key={id} name={name(id)} mgr={agent(id)?.is_manager} title={name(id)} />)}</span>
        </div>
        <div>
          <span className="label">Elapsed</span>
          <span className="v">{duration(m.started_at || m.created_at, m.finished_at)}</span>
        </div>
        <div>
          <span className="label">Budget</span>
          <span className={`v ${over ? "bad" : ""}`}>{usd(m.cost_usd)}<span className="muted"> / {Number(m.budget_usd) ? usd(m.budget_usd) : "∞"}</span></span>
        </div>
        <div className="m-hide">
          <span className="label">Proof</span>
          <span className={`st ${proofTone}`}><i /><span className="t ellipsis">{proofLabel}</span></span>
        </div>
      </div>
    </div>
  );
}

/** One chip per task, in plan order: the mission's work at a glance, clickable. */
function AssignmentStrip({ d }: { d: TaskDetail }) {
  if (!d.subtasks.length) return null;
  return (
    <div className="mh-strip">
      {d.subtasks.map((s) => <span key={s.id} title={`${s.title} · ${s.status}`}><Pipe status={s.status} /></span>)}
    </div>
  );
}

/** The task assignment table: who got what, waiting on whom, and where it stands. */
export function AssignmentTable({ d, selected, onSelect }: { d: TaskDetail; selected?: string | null; onSelect: (id: string) => void }) {
  const name = useAgentName();
  const agent = useAgentLookup();
  const index = useMemo(() => new Map(d.subtasks.map((s, i) => [s.id, i + 1])), [d.subtasks]);
  if (!d.subtasks.length)
    return <div className="muted sm">{d.root.status === "pending" ? "等待主管接手…" : d.root.status === "running" ? "主管正在拆分任务…" : "没有任务"}</div>;
  return (
    <div className="table-wrap boxed">
      <table className="dt inner">
        <thead><tr><th style={{ width: 28 }}>#</th><th>Task</th><th>Agent</th><th>Skill</th><th>After</th><th>State</th><th>Status</th><th className="r">Time</th></tr></thead>
        <tbody>
          {d.subtasks.map((s) => (
            <tr key={s.id} className="click" aria-selected={selected === s.id} onClick={() => onSelect(s.id)}>
              <td className="mono muted">{index.get(s.id)}</td>
              <td className="ellipsis" style={{ maxWidth: 260 }} title={s.title}>{s.title}</td>
              <td><span className="row gap6"><Avatar name={name(s.assigned_agent)} mgr={agent(s.assigned_agent)?.is_manager} /><span className="ellipsis">{name(s.assigned_agent)}</span></span></td>
              <td><span className="tag">{s.skill}</span></td>
              <td className="mono muted">{s.depends_on.length ? s.depends_on.map((x) => `#${index.get(x) ?? "?"}`).join(" ") : "—"}</td>
              <td><Pipe status={s.status} /></td>
              <td><Status status={s.status} /></td>
              <td className="r mono">{s.started_at ? duration(s.started_at, s.finished_at) : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The mission as a sequence of structured events on a rail: planning, assignment, starts,
 * handoffs, results, review, approval, chain. Tool output and bus bookkeeping are left to Logs.
 */
export const MissionTimeline = memo(function MissionTimeline({ d, onTask, limit }: { d: TaskDetail; onTask?: (id: string) => void; limit?: number }) {
  const name = useAgentName();
  const titles = useMemo(() => new Map(d.subtasks.map((s) => [s.id, s.title])), [d.subtasks]);
  const rows = useMemo(() => {
    const out = d.messages.filter((m) => describe(m).major && m.event !== "user.instruction");
    return limit ? out.slice(-limit) : out;
  }, [d.messages, limit]);
  if (!rows.length) return <div className="muted sm">还没有进展</div>;
  return (
    <div className="tl">
      {rows.map((m) => <TimelineRow key={m.id} m={m} name={name} titleOf={(id) => (id ? titles.get(id) : undefined)} onTask={onTask} rootId={d.root.id} />)}
    </div>
  );
});

function TimelineRow({ m, name, titleOf, onTask, rootId }: { m: Message; name: (id?: string | null) => string; titleOf: (id?: string | null) => string | undefined; onTask?: (id: string) => void; rootId: string }) {
  const v = describe(m);
  const title = titleOf(m.task_id);
  const isTask = !!m.task_id && m.task_id !== rootId;
  const click = isTask && onTask ? () => onTask(m.task_id!) : undefined;
  const actor = m.sender && m.sender !== "system" ? name(m.sender) : null;
  let what: React.ReactNode = m.text.split("\n")[0];
  let detail: string | null = null;
  switch (m.event) {
    case "task.assign":
    case "handoff":
    case "goal.submitted":
      what = m.event === "handoff" ? `${m.data?.error ? "没有完成：" : "交接 "}${titleOf(m.data?.task) || m.data?.skill || ""}${m.data?.error ? "" : " 的结果"}` : title || m.text;
      break;
    case "agent.working":
      what = title || m.text;
      if (/交接/.test(m.text)) detail = m.text.replace(/^开始：[^（]*/, "").replace(/[（）]/g, "");
      break;
    case "task.completed":
      what = isTask ? title || m.text : "Mission completed off-chain";
      if (m.data?.secs !== undefined) detail = `${m.data.secs}s · result ${String(m.data.result_hash || "").slice(0, 12)}…`;
      break;
    case "task.failed":
      what = isTask ? title || m.text : m.text;
      detail = isTask ? m.text.replace(/^.*?失败：/, "") : null;
      break;
    case "plan.created":
    case "plan.expanded":
      detail = m.text.split("\n").slice(1).join("\n") || null;
      break;
  }
  const target = v.showTarget && m.recipient ? name(m.recipient) : null;
  return (
    <div className={`tl-row ${v.major ? "major" : ""} ${click ? "clickable" : ""}`} onClick={click}>
      <span className="when">{fmtTime(m.at, false)}</span>
      <span className="node"><b className={m.event === "task.completed" || m.event === "chain.confirmed" || m.event === "approval.approved" ? "fill-ok" : nodeClass(v.tone)} /></span>
      <span className="body">
        <span className={`verb ${v.tone === "bad" ? "bad" : v.tone === "ok" ? "ok" : v.tone === "acc" ? "acc" : v.tone === "info" ? "info" : "muted"}`}>{v.verb}</span>
        {actor && <span className="who">{actor}</span>}
        {target && <><ArrowRight size={11} className="arrow" /><span className="who">{target}</span></>}
        <span className="what ellipsis">{what}</span>
        {m.kind === "chain" && m.data?.tx_url && <a className="btn ghost xs" href={m.data.tx_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>tx</a>}
        {detail && <span className="detail">{detail}</span>}
      </span>
    </div>
  );
}
