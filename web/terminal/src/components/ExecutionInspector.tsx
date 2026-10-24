import { PanelRight, X } from "lucide-react";
import { useAgentName } from "../lib/queries";
import type { Mission, TaskDetail } from "../lib/types";
import { duration, fmtTime, usd } from "../lib/util";
import { ExecutionLogs, ExecutionSteps, ResultView } from "./Execution";
import { ChainPanel, TransactionStatus } from "./TransactionStatus";
import { Addr, EmptyState, Json, Loading, Pipe, Status, Tabs } from "./ui";

export type ExecTab = "overview" | "steps" | "logs" | "results" | "onchain";

/** Right side of the Mission Workspace: the mission by default, one task when one is picked. */
export function ExecutionInspector({ m, d, taskId, onTask, tab, onTab }: {
  m?: Mission | null;
  d?: TaskDetail;
  taskId?: string | null;
  onTask: (id: string | null) => void;
  tab: ExecTab;
  onTab: (t: ExecTab) => void;
}) {
  const name = useAgentName();
  if (!m) return <EmptyState title="选一个 Mission" icon={<PanelRight size={18} />}>左侧选一个 Mission，或在中间交给主管一个新的目标。</EmptyState>;
  if (!d) return <Loading rows={8} />;
  const t = taskId ? d.subtasks.find((s) => s.id === taskId) : null;
  const logs = d.messages.filter((x) => x.kind === "tool" && x.event !== "tool.start" && (!t || x.task_id === t.id)).length;
  return (
    <>
      <div className="pane-h title">
        <span className="label">Execution inspector</span>
        <div className="grow" />
        {t && (
          <button className="btn ghost xs" onClick={() => onTask(null)} title="回到整个 Mission">
            <X size={11} /> {t.title.slice(0, 24)}
          </button>
        )}
      </div>
      <Tabs<ExecTab> value={tab} onChange={onTab} options={[["overview", "Overview"], ["steps", "Steps", d.subtasks.length], ["logs", "Logs", logs], ["results", "Results"], ["onchain", "On-Chain"]]} />
      <div className="insp-b">
        {tab === "overview" && (t ? (
          <div className="insp-sec">
            <div className="row gap8"><Pipe status={t.status} /><Status status={t.status} /><span className="mono muted xs">{t.id}</span></div>
            <h3>{t.title}</h3>
            <dl className="kv">
              <dt>Agent</dt><dd>{name(t.assigned_agent)}</dd>
              <dt>Skill</dt><dd className="mono">{t.skill}</dd>
              <dt>Depends on</dt><dd className="mono">{t.depends_on.length ? t.depends_on.map((x) => d.subtasks.find((s) => s.id === x)?.title || x).join(", ") : "—"}</dd>
              <dt>Attempts</dt><dd className="mono">{t.attempts} / {t.max_attempts}</dd>
              <dt>Started</dt><dd>{fmtTime(t.started_at)}</dd>
              <dt>Duration</dt><dd className="mono">{t.started_at ? duration(t.started_at, t.finished_at) : "—"}</dd>
              <dt>Cost</dt><dd className="mono">{usd(t.cost_usd)} · {t.tokens} tok</dd>
              <dt>Result hash</dt><dd><Addr value={t.result_hash} n={8} /></dd>
            </dl>
            {t.error && <div className="err-callout"><span className="label">失败原因</span><span className="msg">{t.error}</span></div>}
            <span className="label">Input</span>
            <Json value={t.input} />
          </div>
        ) : (
          <>
            <div className="metrics">
              <div><span className="label">Tasks</span><span className="v">{m.progress.settled}/{m.progress.total}</span></div>
              <div><span className="label">Steps</span><span className="v">{m.steps}/{m.max_steps}</span></div>
              <div><span className="label">Cost</span><span className="v">{usd(m.cost_usd)}</span></div>
              <div><span className="label">Tokens</span><span className="v">{m.tokens}</span></div>
            </div>
            <div className="insp-sec">
              <span className="label">Verification</span>
              <TransactionStatus proof={m.proof} />
            </div>
            <div className="insp-sec">
              <span className="label">Plan</span>
              <ExecutionSteps d={d} selected={taskId} onSelect={(id) => onTask(id)} />
            </div>
            <div className="insp-sec">
              <dl className="kv">
                <dt>Owner</dt><dd>{name(m.owner)}</dd>
                <dt>Planner</dt><dd>{m.planner === "model" ? "Model" : m.planner === "rules" ? "Rules (no model)" : m.planner === "direct" ? "Direct assignment" : "—"}</dd>
                <dt>Created</dt><dd>{fmtTime(m.created_at)}</dd>
                <dt>Deadline</dt><dd>{fmtTime(m.deadline_at)}</dd>
              </dl>
              {m.error && <div className="err-callout"><span className="label">失败原因</span><span className="msg">{m.error}</span></div>}
            </div>
          </>
        ))}
        {tab === "steps" && <div className="insp-sec"><ExecutionSteps d={d} selected={taskId} onSelect={(id) => onTask(id)} /></div>}
        {tab === "logs" && <div className="insp-sec"><ExecutionLogs d={d} taskId={taskId} /></div>}
        {tab === "results" && <div className="insp-sec"><ResultView d={d} taskId={taskId} /></div>}
        {tab === "onchain" && <div className="insp-sec"><ChainPanel d={d} proof={m.proof} /></div>}
      </div>
    </>
  );
}
