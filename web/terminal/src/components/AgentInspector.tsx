import { ArrowRight, Pause, Play, Settings2, Star, X, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { post } from "../lib/api";
import { useAction, useAgent, useAgentName, useMeta, useMissions, useSkills, useSquads } from "../lib/queries";
import type { Agent, AgentDetail } from "../lib/types";
import { ago, duration, fmtTime, go, pct, secs, usd, useNow } from "../lib/util";
import { ActivityStream } from "./ActivityStream";
import { AgentStatus } from "./AgentStatus";
import { RunPanel, type RunMode } from "./RunPanel";
import { Addr, Avatar, EmptyState, ErrorState, Loading, Progress, Status, Tabs } from "./ui";

type Tab = "overview" | "execution" | "skills" | "tasks" | "memory" | "permissions" | "activity";

/**
 * Everything about one agent, and the two things you do with it (run / assign), in one panel.
 * Header and primary action stay fixed; the tabs below scroll.
 */
export function AgentInspector({ id, onClose, onConfigure, fav, onFav, action, onAction }: {
  id: string;
  onClose?: () => void;
  onConfigure: (a: Agent) => void;
  fav?: boolean;
  onFav?: () => void;
  /** Open with the run panel showing, e.g. after the row's Run button. */
  action?: RunMode | null;
  onAction?: (m: RunMode | null) => void;
}) {
  const q = useAgent(id);
  const [tab, setTab] = useState<Tab>("overview");
  const [preset, setPreset] = useState<string | undefined>();
  const status = useAction((s: string) => post(`/api/wf/agents/${id}/status`, { status: s }), ["agents"]);
  useEffect(() => setTab("overview"), [id]);
  if (q.isLoading) return <Loading rows={8} />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  const primary: RunMode = a.is_manager ? "assign" : "run";
  const open = (m: RunMode, skill?: string) => (setPreset(skill), onAction?.(m));
  return (
    <>
      <div className="insp-h">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Avatar name={a.name} mgr={a.is_manager} lg />
          <div className="col gap2 grow">
            <div className="row gap6">
              <h2 className="ellipsis">{a.name}</h2>
              {a.is_manager && <span className="badge acc">Manager</span>}
            </div>
            <div className="row gap6 xs muted">
              <AgentStatus agent={a} />
              <span>·</span>
              <span className="ellipsis">{a.role || "—"}</span>
              <span className="mono">{a.id}</span>
            </div>
          </div>
          {onFav && (
            <button className="star" aria-pressed={!!fav} onClick={onFav} aria-label={fav ? "取消收藏" : "收藏"} title={fav ? "取消收藏" : "收藏"}>
              <Star size={14} fill={fav ? "currentColor" : "none"} />
            </button>
          )}
          {onClose && <button className="btn ghost sm icon" onClick={onClose} aria-label="关闭"><X size={14} /></button>}
        </div>
        <div className="row gap6">
          <button className="btn primary sm" disabled={a.status !== "active"} onClick={() => open(primary)}>
            {primary === "run" ? <><Zap size={12} /> Run task</> : <><ArrowRight size={12} /> Assign mission</>}
          </button>
          {primary === "run" && <button className="btn sm" disabled={a.status !== "active"} onClick={() => open("assign")}>Assign mission</button>}
          <div className="grow" />
          {a.status === "active" ? (
            <button className="btn ghost sm" onClick={() => status.mutate("paused")} title="暂停：收件箱保留，不再接新任务"><Pause size={12} /> Pause</button>
          ) : (
            <button className="btn ghost sm" onClick={() => status.mutate("active")}><Play size={12} /> Resume</button>
          )}
          <button className="btn ghost sm icon" onClick={() => onConfigure(a)} title="Configure" aria-label="配置"><Settings2 size={13} /></button>
        </div>
        {action && (
          <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: 12 }}>
            <RunPanel agent={a} mode={action} onMode={(m) => onAction?.(m)} presetSkill={preset} />
          </div>
        )}
      </div>
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        options={[["overview", "Overview"], ["execution", "Execution"], ["skills", "Skills", a.skills.length], ["tasks", "Tasks", a.tasks.length], ["memory", "Memory", a.memory.length], ["permissions", "Permissions"], ["activity", "Logs"]]}
      />
      <div className="insp-b">
        {tab === "overview" && <OverviewTab a={a} />}
        {tab === "execution" && <ExecutionTab a={a} />}
        {tab === "skills" && <SkillsTab a={a} onRun={(s) => open("run", s)} />}
        {tab === "tasks" && <TasksTab a={a} />}
        {tab === "memory" && <MemoryTab a={a} />}
        {tab === "permissions" && <PermissionsTab a={a} />}
        {tab === "activity" && <ActivityTab a={a} />}
      </div>
    </>
  );
}

function Metrics({ a }: { a: AgentDetail }) {
  return (
    <div className="metrics">
      <div><span className="label">Done</span><span className="v">{a.tasks_completed ?? 0}</span></div>
      <div><span className="label">Success</span><span className={`v ${a.success_rate != null && a.success_rate < 0.8 ? "warn" : ""}`}>{pct(a.success_rate)}</span></div>
      <div><span className="label">Avg</span><span className="v">{secs(a.avg_secs)}</span></div>
      <div><span className="label">Cost</span><span className="v">{usd(a.cost_usd)}</span></div>
    </div>
  );
}

/** What the agent is doing right now, from its current task and that task's mission. */
function CurrentExecution({ a, full }: { a: AgentDetail; full?: boolean }) {
  const cur = a.current_task;
  const missions = useMissions({ agent: a.id, status: "pending,running,waiting,review", limit: 20 });
  const m = cur && missions.data?.find((x) => x.id === cur.id || x.tasks.some((t) => t.id === cur.id));
  const task = m?.tasks.find((t) => t.id === cur?.id);
  const now = useNow(1000, !!cur);
  void now;
  const recent = useMemo(() => a.logs.filter((l) => l.kind === "tool" && (!cur || l.task_id === cur.id)).slice(0, full ? 8 : 2), [a.logs, cur, full]);
  if (!cur)
    return (
      <div className="exec-card idle">
        <div className="row sp"><span className="label">Current execution</span><span className="muted xs">last active {ago(a.last_activity_at)}</span></div>
        <div className="dim sm">{a.status === "active" ? "空闲，可以派活。" : a.status === "paused" ? "已暂停，不接新任务。" : "已归档。"}</div>
      </div>
    );
  return (
    <div className="exec-card">
      <div className="row sp">
        <span className="label acc">{cur.status === "review" ? "Awaiting your review" : "Executing"}</span>
        <span className="mono xs dim">{duration(task?.started_at || m?.started_at || m?.created_at)}</span>
      </div>
      <div className="strong ellipsis" title={cur.title}>{cur.title}</div>
      <div className="row gap6 xs">
        <Status status={cur.status} />
        {task?.skill && <span className="tag">{task.skill}</span>}
        {task && task.attempts > 1 && <span className="muted">attempt {task.attempts}</span>}
      </div>
      {m && m.id !== cur.id && (
        <button className="row gap6 xs plain-btn" onClick={() => go("board", m.workspace_id, { m: m.id, t: cur.id })}>
          <span className="muted">Mission</span>
          <span className="ellipsis dim grow">{m.name}</span>
          <Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} />
        </button>
      )}
      {recent.map((l) => (
        <div key={l.id} className="row gap6 xs">
          <span className={l.event === "tool.error" ? "bad" : l.event === "tool.done" ? "ok" : "muted"}>●</span>
          <span className="mono dim">{l.data?.skill}</span>
          <span className="muted ellipsis">{l.text}</span>
          <span className="muted mono" style={{ marginLeft: "auto" }}>{ago(l.at)}</span>
        </div>
      ))}
      <div className="row gap6">
        <button className="btn sm" onClick={() => go("board", m?.workspace_id, { m: m?.id || cur.id, t: m && m.id !== cur.id ? cur.id : "" })}>Open in workspace</button>
        <button className="btn ghost sm" onClick={() => go("tasks", cur.id)}>Task</button>
      </div>
    </div>
  );
}

function OverviewTab({ a }: { a: AgentDetail }) {
  const skills = useSkills();
  return (
    <>
      <Metrics a={a} />
      <div className="insp-sec">
        <CurrentExecution a={a} />
      </div>
      <div className="insp-sec">
        {a.description && <div className="dim sm">{a.description}</div>}
        <dl className="kv">
          <dt>Skills</dt>
          <dd className="row wrap gap4">{a.skills.length ? a.skills.map((s) => <span key={s} className="tag">{skills.data?.find((x) => x.id === s)?.name || s}</span>) : <span className="muted">—</span>}</dd>
          <dt>Model</dt><dd className="mono">{a.provider === "none" ? "No model · skills only" : `${a.provider} · ${a.model}`}</dd>
          <dt>Budget cap</dt><dd className="mono">{Number(a.budget_usd) ? usd(a.budget_usd) : "none"} · {a.tokens_in + a.tokens_out} tok used</dd>
          <dt>Squads</dt><dd>{a.squads?.length ? a.squads.map((q) => <a key={q.id} className="link" href={`#/squads/${q.id}`} style={{ marginRight: 8 }}>{q.name}{q.role === "manager" ? " · MGR" : ""}</a>) : <span className="muted">不在任何 Squad</span>} <AddToSquad a={a} /></dd>
          <dt>Concurrency</dt><dd className="mono">{a.running ?? 0} running / max {a.max_concurrency ?? 2}</dd>
          <dt>Completed</dt><dd className="mono">{a.tasks_completed} ok · {a.tasks_failed} failed</dd>
          <dt>ERC-8004</dt><dd className="mono">{a.onchain.agent_id || <span className="muted">未登记</span>}</dd>
          <dt>Created</dt><dd>{fmtTime(a.created_at)}</dd>
        </dl>
        {a.system_prompt && (
          <details className="log">
            <summary className="muted">System prompt</summary>
            <pre>{a.system_prompt}</pre>
          </details>
        )}
      </div>
    </>
  );
}

function ExecutionTab({ a }: { a: AgentDetail }) {
  const runs = a.logs.filter((l) => l.kind === "tool" && l.event !== "tool.start");
  return (
    <>
      <div className="insp-sec"><CurrentExecution a={a} full /></div>
      <div className="insp-sec">
        <span className="label">Recent executions · {runs.length}</span>
        {!runs.length && <div className="muted sm">还没有执行记录</div>}
        {runs.slice(0, 30).map((l) => (
          <details key={l.id} className="log">
            <summary>
              <span className={l.event === "tool.error" ? "bad" : "ok"}>●</span>
              <span className="mono">{l.data?.skill}</span>
              <span className="muted ellipsis grow">{l.text}</span>
              <span className="muted mono xs">{fmtTime(l.at)}</span>
            </summary>
            <pre>{[...(l.data?.logs || []), l.data?.error ? `✕ ${l.data.error}` : ""].filter(Boolean).join("\n")}</pre>
          </details>
        ))}
      </div>
    </>
  );
}

function SkillsTab({ a, onRun }: { a: AgentDetail; onRun: (s: string) => void }) {
  const skills = useSkills();
  if (!a.skills.length) return <EmptyState title="没有技能">在 Configure 里给它添加技能。</EmptyState>;
  return (
    <div className="insp-sec">
      {a.skills.map((s) => {
        const d = skills.data?.find((x) => x.id === s);
        const reason = a.can_run[s];
        return (
          <div key={s} className="list-row">
            <div className="col gap2 grow">
              <span className="row gap6"><span className="strong">{d?.name || s}</span><span className="mono muted xs">{s}{d ? ` v${d.installed_version || d.version}` : ""}</span></span>
              <span className={`xs ${reason ? "bad" : "muted"} ellipsis`}>{reason || d?.description}</span>
            </div>
            {s !== "data_report" && <button className="btn sm" disabled={!!reason || a.status !== "active"} onClick={() => onRun(s)}><Zap size={11} /> Run</button>}
          </div>
        );
      })}
    </div>
  );
}

function TasksTab({ a }: { a: AgentDetail }) {
  if (!a.tasks.length) return <EmptyState title="还没有任务" />;
  return (
    <table className="dt inner">
      <thead><tr><th>Task</th><th>Status</th><th className="r">Duration</th><th className="r">Created</th></tr></thead>
      <tbody>
        {a.tasks.map((t) => (
          <tr key={t.id} className="click" onClick={() => go("tasks", t.id)}>
            <td className="ellipsis" style={{ maxWidth: 170 }} title={t.title}>{t.kind === "goal" && <span className="tag" style={{ marginRight: 6 }}>M</span>}{t.title}</td>
            <td><Status status={t.status} /></td>
            <td className="r mono">{duration(t.started_at || t.created_at, t.finished_at)}</td>
            <td className="r mono muted">{ago(t.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MemoryTab({ a }: { a: AgentDetail }) {
  if (!a.memory.length) return <EmptyState title="还没有私有记忆">完成任务后会自动记下结论，90 天后过期。</EmptyState>;
  return (
    <div className="insp-sec">
      {a.memory.map((m) => (
        <div key={m.id} className="list-row" style={{ alignItems: "flex-start" }}>
          <div className="col gap2 grow">
            <span className="sm">{m.text}</span>
            <span className="muted mono xs">#{m.id} · {fmtTime(m.created_at)} · {m.expires_at ? `expires ${fmtTime(m.expires_at)}` : "no expiry"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function PermissionsTab({ a }: { a: AgentDetail }) {
  const meta = useMeta();
  return (
    <>
      <div className="insp-sec">
        {Object.entries(meta.data?.permissions || {}).map(([p, d]) => (
          <div key={p} className="list-row">
            <span className="mono">{p}</span>
            <span className="muted xs grow ellipsis">{d}</span>
            {a.permissions.includes(p) ? <span className="ok xs">granted</span> : <span className="muted xs">—</span>}
          </div>
        ))}
        <div className="note">员工没有任何签名或转账权限。链上写入只发生在人工审批之后，由服务器的测试网钱包执行。</div>
      </div>
      <div className="insp-sec">
        <span className="label">On-chain identity</span>
        <dl className="kv">
          <dt>ERC-8004 ID</dt><dd className="mono">{a.onchain.agent_id || "—"}</dd>
          <dt>Address</dt><dd><Addr value={a.onchain.address} url={a.onchain.address && meta.data?.explorer ? meta.data.explorer.replace("/tx/", "/address/") + a.onchain.address : null} /></dd>
          <dt>Verified</dt><dd className="muted">未在链上核验（记录的是你填写的身份）</dd>
        </dl>
      </div>
      <div className="insp-sec">
        <span className="label">Audit</span>
        {!a.audit.length && <span className="muted sm">没有记录</span>}
        {a.audit.map((e) => <div key={e.id} className="list-row xs"><span className="mono muted">{fmtTime(e.at)}</span><span className="dim ellipsis">{e.text}</span></div>)}
      </div>
    </>
  );
}

function ActivityTab({ a }: { a: AgentDetail }) {
  const name = useAgentName();
  if (!a.logs.length) return <EmptyState title="没有活动记录" />;
  return (
    <div className="insp-sec" style={{ gap: 0 }}>
      <ActivityStream rows={a.logs} name={name} compact />
    </div>
  );
}

/** Join one more squad without leaving the inspector. */
function AddToSquad({ a }: { a: AgentDetail }) {
  const squads = useSquads();
  const add = useAction((sid: string) => post(`/api/wf/squads/${sid}/members`, { agent_id: a.id }), ["squads", "agents", "workspaces", "preflight"], "已加入 Squad");
  const options = (squads.data || []).filter((q) => q.manager_id !== a.id && !q.members.includes(a.id));
  if (!options.length || a.status === "archived") return null;
  return (
    <select className="input sm" style={{ width: 130, display: "inline-block" }} value="" onChange={(e) => e.target.value && add.mutate(e.target.value)} aria-label="Add to squad">
      <option value="">+ Add to squad</option>
      {options.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
    </select>
  );
}
