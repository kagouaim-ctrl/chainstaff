import { AlertTriangle, Bot, Pause, Play, Plus } from "lucide-react";
import { useCallback, useState } from "react";
import { ActivityRow, AgentState, ApprovalCard, TaskDialog } from "../components/AtBits";
import { DataTable, type Column } from "../components/DataTable";
import { Cell, EmptyState, ErrorState, Loading } from "../components/ui";
import { post } from "../lib/api";
import { MODE_LABEL, refreshAt, useAtActivity, useAtApprovals, useAtLive, useAtOverview, type AtActivity, type AtAgentRow } from "../lib/at";
import { DEMO } from "../lib/demo";
import { ago, errText, go, setParams, toast, type Route } from "../lib/util";

const HEALTH: Record<string, [string, string]> = { ok: ["Healthy", "ok"], degraded: ["Degraded", "acc"], down: ["Down", "bad"] };

export default function Overview({ route }: { route: Route }) {
  const o = useAtOverview();
  const approvals = useAtApprovals("pending");
  const task = route.params.get("task");
  const openTask = useCallback((id: string) => setParams(route, { task: id }), [route]);
  if (o.isLoading) return <div className="page"><Loading rows={8} /></div>;
  if (o.error) return <div className="page"><ErrorState error={o.error} retry={() => o.refetch()} /></div>;
  const d = o.data!;
  const k = d.kpis;
  const [hl, ht] = HEALTH[k.runtime_health];
  return (
    <div className="page">
      <div className="toolbar">
        <h1>Overview</h1>
        <div className="grow" />
        <button className="btn primary" disabled={DEMO} onClick={() => go("agents", undefined, { new: "1" })}><Plus size={13} /> New agent</button>
      </div>
      <div className="strip kpis">
        <Cell label="Total Agents" value={k.total_agents} sub={`${d.agents.filter((a) => a.activated).length} activated`} onClick={() => go("agents")} />
        <Cell label="Active Watchers" value={<>{k.active_watchers}<span className="muted v-sm"> / {k.watchers_total}</span></>} sub="healthy right now" onClick={() => go("workspace")} />
        <Cell label="Running Missions" value={k.running_missions} sub={`${k.active_missions} active`} onClick={() => go("missions")} />
        <Cell label="Active Investigations" value={k.active_investigations} sub="queued, running or waiting" onClick={() => go("missions", undefined, { tab: "tasks", kind: "investigate" })} />
        <Cell label="Pending Approvals" value={k.pending_approvals} tone={k.pending_approvals ? "acc" : ""} sub="patches and PRs" onClick={() => go("activity", undefined, { human: "1" })} />
        <Cell label="Runtime Health" value={<span className={ht}>{hl}</span>} sub={d.health.worker_alive ? "worker online" : "no worker online"} onClick={() => go("settings", undefined, { s: "runtime" })} />
      </div>
      {d.health.problems.map((p) => (
        <div key={p} className="banner warn"><AlertTriangle size={14} className="acc" /> <span className="grow">{p}</span>
          {p.includes("Worker") && <a className="btn ghost xs" href="#/settings?s=runtime">Runtime</a>}
          {p.includes("API Key") && <a className="btn ghost xs" href="#/settings?s=models">AI Models</a>}
        </div>
      ))}
      <div className="ov-grid">
        <section className="col" aria-label="Agents">
          {!!approvals.data?.length && (
            <div className="section col gap8">
              <div className="section-h"><h2>Waiting for you</h2><span className="count mono muted">{approvals.data.length}</span></div>
              {approvals.data.slice(0, 5).map((a) => <ApprovalCard key={a.id} a={a} />)}
            </div>
          )}
          <div className="section-h pad"><h2>Agents</h2><span className="count mono muted">{d.agents.length}</span></div>
          <AgentTable rows={d.agents} onTask={openTask} compact />
        </section>
        <aside className="ov-side" aria-label="Live activity"><LiveActivity onTask={openTask} /></aside>
      </div>
      {task && <TaskDialog id={task} onClose={() => setParams(route, { task: null })} />}
    </div>
  );
}

export function AgentTable({ rows, onTask, id = "at-overview", compact = false }: { rows: AtAgentRow[]; onTask: (id: string) => void; id?: string; compact?: boolean }) {
  const [busy, setBusy] = useState<string | null>(null);
  const toggle = async (a: AtAgentRow) => {
    setBusy(a.id);
    try {
      await post(`/api/at/agents/${a.id}/${a.status === "paused" ? "resume" : "pause"}`);
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(null);
    }
  };
  const cols: Column<AtAgentRow>[] = [
    { id: "agent", header: "Agent", sort: (a) => a.name, cell: (a) => <span className="row gap6"><Bot size={13} className="muted" /><b>{a.name}</b><span className="muted xs">{MODE_LABEL[a.work_mode]}</span></span> },
    { id: "resp", header: "Responsibility", cell: (a) => <span className="muted ellipsis" title={a.responsibility} style={{ maxWidth: 240, display: "inline-block" }}>{a.responsibility || "—"}</span> },
    { id: "ws", header: "Workspace", cell: (a) => (a.workspaces.length ? a.workspaces.map((w) => w.name).join(", ") : <span className="muted">—</span>) },
    { id: "state", header: "Status", sort: (a) => a.state, cell: (a) => <AgentState state={a.state} detail={a.state_detail} /> },
    { id: "task", header: "Current Work", cell: (a) => a.current_task
        ? <button className="plain-btn link ellipsis" style={{ maxWidth: 220 }} title={a.current_task.objective} onClick={(e) => (e.stopPropagation(), onTask(a.current_task!.id))}>{a.current_task.objective}</button>
        : <span className="muted ellipsis" title={a.state_detail} style={{ maxWidth: 220, display: "inline-block" }}>{a.state_detail}</span> },
    { id: "last", header: "Last Wake-up", sort: (a) => a.last_wake_at || "", cell: (a) => <span className="mono muted">{a.last_wake_at ? ago(a.last_wake_at) : "—"}</span> },
    { id: "act", header: "Actions", hideable: false, align: "r", cell: (a) => (
      <span className="row gap2" style={{ justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()}>
        {a.status !== "archived" && (
          <button className="btn xs" disabled={busy === a.id || DEMO} onClick={() => toggle(a)} title={a.status === "paused" ? "Resume" : "Pause"}>
            {a.status === "paused" ? <Play size={11} /> : <Pause size={11} />}
          </button>
        )}
        <button className="btn xs" onClick={() => go("agents", a.id)}>Open</button>
      </span>
    ) },
  ];
  return (
    <DataTable id={id} rows={rows} columns={compact ? cols.filter((c) => c.id !== "resp") : cols} rowKey={(a) => a.id} onRowClick={(a) => go("agents", a.id)}
      empty={<EmptyState title="Create your first agent" icon={<Bot size={18} />}>
        <span className="muted sm">给它一个名字、一份长期职责和一个工作区，激活后它就会在云端被事件和其他 Agent 唤醒。</span>
        {!DEMO && <button className="btn primary sm" onClick={() => go("agents", undefined, { new: "1" })}><Plus size={12} /> New agent</button>}
      </EmptyState>} />
  );
}

function LiveActivity({ onTask }: { onTask: (id: string) => void }) {
  const q = useAtActivity({ limit: 60 });
  const [live, setLive] = useState<AtActivity[]>([]);
  useAtLive(useCallback((a: AtActivity) => setLive((l) => [a, ...l.filter((x) => x.id !== a.id)].slice(0, 60)), []));
  const seen = new Set(live.map((a) => a.id));
  const rows = [...live, ...(q.data || []).filter((a) => !seen.has(a.id))].sort((a, b) => b.id - a.id).slice(0, 60);
  return (
    <>
      <div className="section-h pad"><h2>Live Activity</h2><div className="grow" /><a className="btn ghost xs" href="#/activity">All</a></div>
      {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorState error={q.error} /> : !rows.length ? (
        <EmptyState small title="还没有活动">创建 Agent、连接 Watcher 之后，这里会实时显示每一次唤醒、交接和结果。</EmptyState>
      ) : (
        <div className="act-list">{rows.map((a) => <ActivityRow key={a.id} a={a} onTask={onTask} compact={false} />)}</div>
      )}
    </>
  );
}
