import { ArrowRight, Check, ExternalLink, GitPullRequest, ShieldCheck, User, X } from "lucide-react";
import { useState } from "react";
import { post } from "../lib/api";
import {
  OPEN_TASK, SEV_TONE, STATE, TASK_ST, refreshAt, useAtAgents, useAtArtifact, useAtTask,
  type AgentState as AgentStateT, type AtActivity, type AtApproval, type AtRun, type AtStep, type Severity, type TaskStatusAt,
} from "../lib/at";
import { DEMO } from "../lib/demo";
import { ago, duration, errText, fmtTime, go, toast } from "../lib/util";
import { Dialog, EmptyState, ErrorState, Json, Loading, Tabs } from "./ui";

export function AgentState({ state, detail }: { state: AgentStateT; detail?: string }) {
  const [l, tone, live] = STATE[state] || [state, ""];
  return (
    <span className={`st ${tone} ${live ? "live" : ""}`} title={detail}>
      <i />
      <span className="t">{l}</span>
    </span>
  );
}

export function TaskState({ status }: { status: TaskStatusAt }) {
  const [l, tone, live] = TASK_ST[status] || [status, ""];
  return (
    <span className={`st ${tone} ${live ? "live" : ""}`}>
      <i />
      <span className="t">{l}</span>
    </span>
  );
}

export function Sev({ s }: { s: Severity }) {
  return <span className={`sev ${SEV_TONE[s] || ""}`}>{s}</span>;
}

/** One line of the activity record: who triggered it, why, who it went to, what happened, the result, and whether a human is needed. */
export function ActivityRow({ a, onTask, compact }: { a: AtActivity; onTask?: (id: string) => void; compact?: boolean }) {
  const who = a.agent_name || a.actor_name || a.actor;
  return (
    <div className={`act-row ${a.needs_human ? "human" : ""}`}>
      <span className="act-when mono" title={fmtTime(a.at)}>{ago(a.at)}</span>
      <div className="act-body">
        <div className="act-title">
          <Sev s={a.severity} />
          <span className="act-type mono">{a.type}</span>
          <span className="act-text">{a.title}</span>
        </div>
        {!compact && (
          <div className="act-meta">
            <span><span className="muted">by</span> {who}</span>
            {a.target_agent_name && <span><ArrowRight size={11} className="muted" /> {a.target_agent_name}</span>}
            {a.reason && <span className="ellipsis" title={a.reason}><span className="muted">why</span> {a.reason}</span>}
            {a.result && <span className="ellipsis" title={a.result}><span className="muted">result</span> {a.result}</span>}
            {!!a.needs_human && <span className="acc"><User size={11} /> needs you</span>}
            {a.task_id && onTask && <button className="plain-btn link mono" onClick={() => onTask(a.task_id!)}>{a.task_id}</button>}
          </div>
        )}
      </div>
    </div>
  );
}

export function ApprovalCard({ a }: { a: AtApproval }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState(false);
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      const r = await post<{ applied_commit?: string; note?: string }>(`/api/at/approvals/${a.id}`, { approve, note });
      toast(approve ? (r.applied_commit ? `补丁已应用：${r.applied_commit.slice(0, 10)}` : r.note || "已通过") : "已驳回");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="approval">
      <div className="approval-h"><ShieldCheck size={14} className="acc" /> <span className="grow">{a.title}</span></div>
      <div className="approval-b sm">
        <div className="row wrap gap6">
          <span className="muted">{a.agent_name || a.agent_id}</span>
          {a.detail.repo && <span className="tag">{a.detail.repo}</span>}
          {a.detail.stat && <span className="mono xs muted">{a.detail.stat}</span>}
          <span className={a.detail.tests_passed ? "ok" : a.detail.tests_passed === false ? "bad" : "muted"}>
            {a.detail.tests_passed ? "tests passed" : a.detail.tests_passed === false ? "tests failed" : "tests not run"}
          </span>
          {a.detail.pr_url && <a className="link" href={a.detail.pr_url} target="_blank" rel="noreferrer"><GitPullRequest size={12} /> Draft PR <ExternalLink size={10} /></a>}
          <span className="muted xs">{ago(a.at)}</span>
        </div>
        {a.artifact_id && <button className="plain-btn link xs" onClick={() => setDiff(!diff)}>{diff ? "Hide diff" : "Show diff"}</button>}
        {diff && a.artifact_id && <Diff id={a.artifact_id} />}
      </div>
      {a.status === "pending" && (
        <div className="approval-f">
          <input className="input sm" placeholder="备注（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
          <span className="muted xs grow">
            {a.detail.provider === "local" ? "通过后 KAGOU 把补丁提交到本地仓库，然后由发现问题的 Agent 验证。" : a.detail.pr_url ? "通过只记录你的审核；合并请在 GitHub 上操作。" : "通过只记录你的审核。"}
          </span>
          <button className="btn sm" disabled={busy || DEMO} onClick={() => decide(false)}><X size={12} /> Reject</button>
          <button className="btn sm primary" disabled={busy || DEMO} onClick={() => decide(true)}><Check size={12} /> Approve</button>
        </div>
      )}
    </div>
  );
}

function Diff({ id }: { id: string }) {
  const q = useAtArtifact(id);
  if (q.isLoading) return <Loading rows={3} />;
  if (q.error) return <ErrorState error={q.error} />;
  return (
    <pre className="code diff">
      {q.data!.content.split("\n").map((l, i) => (
        <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "ok" : l.startsWith("-") && !l.startsWith("---") ? "bad" : l.startsWith("@@") ? "info" : ""}>{l}{"\n"}</span>
      ))}
    </pre>
  );
}

/** Everything about one task: the hand-off chain it belongs to, its runs step by step, its evidence, result, patch and approval. */
export function TaskDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useAtTask(id);
  const [tab, setTab] = useState<"chain" | "run" | "evidence" | "activity">("chain");
  const [busy, setBusy] = useState(false);
  const act = async (path: "cancel" | "retry", body?: unknown) => {
    setBusy(true);
    try {
      await (path === "cancel" ? post(`/api/at/tasks/${id}/cancel`, body) : post(`/api/at/tasks/${id}/retry`, body));
      toast("已更新");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const d = q.data;
  const name = (x?: string | null) => (x ? d?.names[x] || x : "System");
  return (
    <Dialog wide title={<span className="row gap6"><span className="mono">{id}</span>{d && <TaskState status={d.task.status} />}</span>} onClose={onClose}
      footer={d && (
        <>
          <span className="muted xs grow">attempt {d.task.attempts}/{d.task.max_attempts} · ${Number(d.task.cost_usd).toFixed(4)} / ${d.task.budget_usd} · {d.task.tokens} tokens</span>
          {OPEN_TASK.includes(d.task.status) && <button className="btn sm" disabled={busy || DEMO} onClick={() => confirm("取消这个任务和它交出去的子任务？") && act("cancel", { reason: "用户取消" })}>Cancel</button>}
          {(d.task.status === "failed" || d.task.status === "cancelled") && d.task.to_agent_id && <button className="btn sm primary" disabled={busy || DEMO} onClick={() => act("retry")}>Retry</button>}
        </>
      )}>
      {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorState error={q.error} /> : (
        <div className="col gap12">
          <div className="col gap6">
            <div className="row gap6 wrap sm">
              <span className="tag">{d!.task.kind}</span>
              <span>{name(d!.task.from_agent_id)}</span>
              <ArrowRight size={12} className="muted" />
              <b>{d!.task.to_agent_id ? name(d!.task.to_agent_id) : <span className="acc">unassigned</span>}</b>
              <span className="muted xs">created {fmtTime(d!.task.created_at)}{d!.task.finished_at ? ` · took ${duration(d!.task.started_at || d!.task.created_at, d!.task.finished_at)}` : ""}</span>
              {d!.task.mission_id && <a className="link xs" href={`#/missions/${d!.task.mission_id}`}>mission</a>}
            </div>
            <div className="strong">{d!.task.objective}</div>
            {d!.task.status === "unassigned" && <AssignBox id={id} />}
            {d!.task.result?.summary && <div className="note">{d!.task.result.summary}</div>}
            {d!.task.error && <div className="note bad">{d!.task.error}</div>}
          </div>
          {d!.approvals.filter((a) => a.status === "pending").map((a) => <ApprovalCard key={a.id} a={a} />)}
          <Tabs value={tab} onChange={setTab} options={[["chain", "Hand-off chain", d!.tree.length], ["run", "Runs", d!.runs.length], ["evidence", "Evidence & result"], ["activity", "Activity", d!.activity.length]]} />
          {tab === "chain" && (
            <div className="tree">
              {d!.tree.map((t) => (
                <div key={t.id} className={`tree-row ${t.id === id ? "on" : ""}`} style={{ paddingLeft: 8 + t.depth * 18 }}>
                  <TaskState status={t.status} />
                  <span className="tag">{t.kind}</span>
                  <span className="muted xs">{name(t.from_agent_id)} → {name(t.to_agent_id)}</span>
                  <span className="ellipsis grow">{t.objective}</span>
                  {t.id !== id && <button className="plain-btn link mono xs" onClick={() => go("missions", undefined, { task: t.id })}>{t.id}</button>}
                </div>
              ))}
            </div>
          )}
          {tab === "run" && <Steps steps={d!.steps} runs={d!.runs} />}
          {tab === "evidence" && (
            <div className="col gap6">
              <div className="label">Evidence</div>
              <Json value={d!.task.evidence} />
              {d!.event && (<><div className="label">Triggering event · {d!.event.type} · {d!.event.processing_status}{d!.event.chain_status ? ` · chain ${d!.event.chain_status}` : ""}</div><Json value={d!.event.payload} /></>)}
              <div className="label">Result</div>
              {d!.task.result ? <Json value={d!.task.result} /> : <span className="muted sm">还没有结果</span>}
              {d!.artifacts.map((x) => (
                <div key={x.id} className="col gap6">
                  <div className="label">Patch {x.id} · {x.status}{x.meta.pr_url ? "" : x.meta.pr_error ? ` · ${x.meta.pr_error}` : ""}</div>
                  {x.meta.pr_url && <a className="link sm" href={x.meta.pr_url} target="_blank" rel="noreferrer">{x.meta.pr_url}</a>}
                  <Diff id={x.id} />
                </div>
              ))}
            </div>
          )}
          {tab === "activity" && (d!.activity.length ? <div className="act-list">{d!.activity.map((a) => <ActivityRow key={a.id} a={a} />)}</div> : <EmptyState small title="没有记录" />)}
        </div>
      )}
    </Dialog>
  );
}

function Steps({ steps, runs }: { steps: AtStep[]; runs: AtRun[] }) {
  if (!runs.length) return <EmptyState small title="还没有执行">任务在队列里，等 Worker 和 Agent 有空位时开始。</EmptyState>;
  const r = runs[0];
  return (
    <div className="col gap6">
      <div className="row gap6 wrap xs muted">
        <span>latest run <span className="mono">{r.id}</span> · {r.status}{r.resumed ? " · resumed from checkpoint" : ""}</span>
        <span>{r.provider}/{r.model}</span>
        <span>{r.model_calls} model calls · {r.tool_calls} tool calls · {r.tokens_in + r.tokens_out} tokens · ${Number(r.cost_usd).toFixed(4)}</span>
        {r.worker_id && <span>worker {r.worker_id}</span>}
      </div>
      {r.error && <div className="note bad">{r.error}</div>}
      <div className="steps">
        {steps.map((s) => (
          <details key={s.id} className={`step ${s.ok ? "" : "bad"}`}>
            <summary>
              <span className="tag">{s.kind}</span>
              <span className="mono">{s.name}</span>
              {s.secs != null && <span className="muted mono xs">{s.secs}s</span>}
              {s.kind === "model" && <span className="muted mono xs">{s.tokens_in}+{s.tokens_out} tok</span>}
              {!s.ok && <span className="bad xs">failed</span>}
            </summary>
            {s.input != null && <Json value={s.input} />}
            {s.output != null && <Json value={s.output} />}
          </details>
        ))}
      </div>
      {runs.length > 1 && <div className="muted xs">earlier runs: {runs.slice(1).map((x) => `${x.id} (${x.status})`).join(", ")}</div>}
    </div>
  );
}

function AssignBox({ id }: { id: string }) {
  const [agent, setAgent] = useState("");
  const [busy, setBusy] = useState(false);
  const agents = useAtTaskAgents();
  return (
    <div className="note acc row gap6 wrap">
      <span className="grow">没有 Agent 有这个任务需要的权限和工作区。指派一个 Agent，或者先修改某个 Agent 的权限。</span>
      <select className="input sm" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="指派给">
        <option value="">选择 Agent</option>
        {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
      <button className="btn sm primary" disabled={!agent || busy || DEMO} onClick={async () => {
        setBusy(true);
        try {
          await post(`/api/at/tasks/${id}/assign`, { agent_id: agent });
          toast("已指派");
          refreshAt();
        } catch (e) {
          toast(errText(e), true);
        } finally {
          setBusy(false);
        }
      }}>Assign</button>
    </div>
  );
}

function useAtTaskAgents() {
  const q = useAtAgents();
  return (q.data || []).filter((a) => a.status === "active" && a.autonomous);
}
