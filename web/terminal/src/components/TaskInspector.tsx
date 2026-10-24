import { ArrowRight, Ban, X } from "lucide-react";
import { useEffect, useState } from "react";
import { post } from "../lib/api";
import { useAction, useAgentName, useMission, useTask } from "../lib/queries";
import type { Task, TaskDetail } from "../lib/types";
import { duration, fmtTime, go, usd } from "../lib/util";
import { ApprovalPanel } from "./ApprovalPanel";
import { ExecutionLogs, ExecutionSteps, MessagesTable, ResultView, TaskGraph } from "./Execution";
import { MissionStage } from "./Mission";
import { ChainPanel, TransactionStatus } from "./TransactionStatus";
import { Addr, ErrorState, Json, Loading, Pipe, Priority, Progress, Status, Tabs } from "./ui";

type Tab = "overview" | "execution" | "graph" | "messages" | "logs" | "result" | "onchain";

/** One task (or a whole mission) with its execution, opened beside the task table. The graph is a tab, not the default. */
export function TaskInspector({ id, onClose }: { id: string; onClose?: () => void }) {
  const q = useTask(id);
  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => setTab("overview"), [id]);
  const rootId = q.data?.root.id;
  const mission = useMission(rootId);
  if (q.isLoading) return <Loading rows={8} />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const t = d.task;
  const isMission = t.kind === "goal";
  const logs = d.messages.filter((m) => m.kind === "tool" && m.event !== "tool.start" && (isMission || m.task_id === t.id)).length;
  return (
    <>
      <div className="insp-h">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="col gap4 grow">
            <div className="row gap6">
              <span className="tag">{isMission ? "Mission" : "Task"}</span>
              {isMission && mission.data ? <MissionStage m={mission.data} /> : <Status status={t.status} />}
              <Priority p={t.priority} />
              <span className="mono muted xs">{t.id}</span>
            </div>
            <h2 className="clamp2" title={t.title}>{t.title}</h2>
            {!isMission && (
              <button className="row gap6 xs plain-btn" onClick={() => go("workspace", d.root.workspace_id, { m: d.root.id, t: t.id })}>
                <span className="muted nowrap">in mission</span><span className="dim ellipsis">{d.root.title}</span><ArrowRight size={11} className="muted" />
              </button>
            )}
          </div>
          {onClose && <button className="btn ghost sm icon" onClick={onClose} aria-label="关闭"><X size={14} /></button>}
        </div>
        <div className="row gap6">
          <button className="btn sm primary" onClick={() => go("workspace", d.root.workspace_id, { m: d.root.id, ...(isMission ? {} : { t: t.id }) })}>Open in workspace</button>
          <CancelButton root={d.root} />
        </div>
      </div>
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        options={[["overview", "Overview"], ["execution", "Execution", d.subtasks.length], ["graph", "Dependencies"], ["messages", "Messages"], ["logs", "Logs", logs], ["result", "Results"], ["onchain", "On-Chain", d.chain.length || undefined]]}
      />
      <div className="insp-b">
        {tab === "overview" && <Overview d={d} t={t} proof={mission.data?.proof} progress={mission.data?.progress} />}
        {tab === "execution" && <div className="insp-sec"><ExecutionSteps d={d} selected={isMission ? null : t.id} onSelect={(sid) => go("tasks", sid)} /></div>}
        {tab === "graph" && <div className="insp-sec"><TaskGraph d={d} /></div>}
        {tab === "messages" && <MessagesTable d={d} />}
        {tab === "logs" && <div className="insp-sec"><ExecutionLogs d={d} taskId={isMission ? null : t.id} /></div>}
        {tab === "result" && <div className="insp-sec"><ResultView d={d} taskId={isMission ? null : t.id} /></div>}
        {tab === "onchain" && <div className="insp-sec"><ChainPanel d={d} proof={mission.data?.proof} /></div>}
      </div>
    </>
  );
}

function CancelButton({ root }: { root: Task }) {
  const cancel = useAction(() => post(`/api/wf/tasks/${root.id}/cancel`, {}), ["task", "tasks", "missions"], "Mission 已取消");
  if (["completed", "failed", "cancelled"].includes(root.status)) return null;
  return <button className="btn ghost sm" disabled={cancel.isPending} onClick={() => confirm("取消整个 Mission 和它所有未完成的任务？") && cancel.mutate(undefined)}><Ban size={12} /> Cancel mission</button>;
}

function Overview({ d, t, proof, progress }: { d: TaskDetail; t: Task; proof?: import("../lib/types").Proof; progress?: import("../lib/types").Mission["progress"] }) {
  const name = useAgentName();
  const isMission = t.kind === "goal";
  const pending = d.approvals.filter((a) => a.status === "pending");
  return (
    <>
      {pending.length > 0 && <div className="insp-sec">{pending.map((a) => <ApprovalPanel key={a.id} approval={a} root={d.root} />)}</div>}
      <div className="metrics">
        <div><span className="label">Duration</span><span className="v">{duration(t.started_at || t.created_at, t.finished_at)}</span></div>
        <div><span className="label">Cost</span><span className="v">{usd(t.cost_usd)}</span></div>
        <div><span className="label">{isMission ? "Steps" : "Attempts"}</span><span className="v">{isMission ? `${t.steps}/${t.max_steps}` : `${t.attempts}/${t.max_attempts}`}</span></div>
        <div><span className="label">Tokens</span><span className="v">{t.tokens}</span></div>
      </div>
      <div className="insp-sec">
        {isMission && progress && <Progress done={progress.completed} failed={progress.failed} total={progress.total} />}
        {!isMission && <div className="row gap6"><Pipe status={t.status} /><span className="muted xs">queued → assigned → running → done</span></div>}
        <dl className="kv">
          <dt>{isMission ? "Owner" : "Agent"}</dt><dd>{name(t.assigned_agent)}</dd>
          {!isMission && <><dt>Skill</dt><dd className="mono">{t.skill}</dd></>}
          {isMission && <><dt>Planner</dt><dd>{t.planner === "model" ? "Model" : t.planner === "rules" ? "Rules (no model)" : t.planner === "direct" ? "Direct assignment" : "—"}</dd></>}
          <dt>Created</dt><dd>{fmtTime(t.created_at)} · {t.created_by === "owner" ? "you" : name(t.created_by)}</dd>
          <dt>Started</dt><dd>{fmtTime(t.started_at)}</dd>
          <dt>Finished</dt><dd>{fmtTime(t.finished_at)}</dd>
          {isMission && <><dt>Budget</dt><dd className="mono">{usd(t.cost_usd)} / {usd(t.budget_usd)}</dd><dt>Deadline</dt><dd>{fmtTime(t.deadline_at)}</dd></>}
          <dt>Result hash</dt><dd><Addr value={t.result_hash} n={8} /></dd>
        </dl>
        {t.error && <div className="err-callout"><span className="label">失败原因</span><span className="msg">{t.error}</span></div>}
      </div>
      {proof && <div className="insp-sec"><span className="label">Verification</span><TransactionStatus proof={proof} /></div>}
      {!isMission && Object.keys(t.input || {}).length > 0 && <div className="insp-sec"><span className="label">Input</span><Json value={t.input} /></div>}
      {isMission && t.description && <div className="insp-sec"><span className="label">Brief</span><div className="brief">{t.description}</div></div>}
    </>
  );
}
