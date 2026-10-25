import { ArrowLeft, Pause, Play, Plus, Search, Square, Target, Zap } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { ActivityRow, TaskDialog, TaskState } from "../components/AtBits";
import { DataTable, type Column } from "../components/DataTable";
import { Dialog, EmptyState, ErrorState, Loading, Seg, Tabs } from "../components/ui";
import { post, put } from "../lib/api";
import {
  MISSION_ST, MISSION_TYPE_LABEL, refreshAt, useAtAgents, useAtMeta, useAtMission, useAtMissions, useAtTasks, useAtWorkspaces,
  type AtMission, type AtTask, type Severity,
} from "../lib/at";
import { DEMO } from "../lib/demo";
import { ago, errText, fmtTime, go, setParams, toast, type Route } from "../lib/util";

export default function Missions({ route }: { route: Route }) {
  const task = route.params.get("task");
  const close = () => setParams(route, { task: null });
  return (
    <>
      {route.id ? <MissionDetail id={route.id} route={route} /> : <MissionList route={route} />}
      {task && <TaskDialog id={task} onClose={close} />}
    </>
  );
}

function MissionState({ s }: { s: string }) {
  const [l, t] = MISSION_ST[s] || [s, ""];
  return <span className={`st ${t}`}><i /><span className="t">{l}</span></span>;
}

function MissionList({ route }: { route: Route }) {
  const tab = (route.params.get("tab") as "missions" | "tasks") || "missions";
  const [creating, setCreating] = useState(false);
  return (
    <div className="page">
      <div className="toolbar">
        <h1>Missions</h1>
        <div className="grow" />
        <button className="btn primary" disabled={DEMO} onClick={() => setCreating(true)}><Plus size={13} /> New mission</button>
      </div>
      <div className="tabs-pad">
        <Tabs value={tab} onChange={(v) => setParams(route, { tab: v === "missions" ? null : v, kind: null, status: null })} options={[["missions", "Missions"], ["tasks", "Tasks"]]} />
      </div>
      {tab === "missions" ? <MissionTable route={route} /> : <TaskTable route={route} />}
      {creating && <MissionForm onClose={() => setCreating(false)} />}
    </div>
  );
}

function MissionTable({ route }: { route: Route }) {
  const status = route.params.get("status") || "active,paused";
  const ws = route.params.get("ws") || "";
  const q = useAtMissions({ status, workspace_id: ws });
  const wss = useAtWorkspaces();
  const [s, setS] = useState("");
  const rows = useMemo(() => (q.data || []).filter((m) => !s || `${m.name} ${m.goal} ${m.id}`.toLowerCase().includes(s.toLowerCase())), [q.data, s]);
  const cols: Column<AtMission>[] = [
    { id: "name", header: "Mission", sort: (m) => m.name, cell: (m) => <span className="row gap6"><Target size={13} className="muted" /><b>{m.name}</b>{m.responsibility_of && <span className="tag">standing</span>}</span> },
    { id: "type", header: "Type", sort: (m) => m.type, cell: (m) => <span className="muted">{MISSION_TYPE_LABEL[m.type]}</span> },
    { id: "goal", header: "Goal", cell: (m) => <span className="muted ellipsis" title={m.goal} style={{ maxWidth: 320, display: "inline-block" }}>{m.goal}</span> },
    { id: "agents", header: "Agents", cell: (m) => (m.agents || []).map((a) => a.name).join(", ") || "—" },
    { id: "ws", header: "Workspace", cell: (m) => wss.data?.find((w) => w.id === m.workspace_id)?.name || m.workspace_id },
    { id: "state", header: "State", sort: (m) => m.status, cell: (m) => <MissionState s={m.status} /> },
    { id: "open", header: "Open tasks", align: "r", sort: (m) => openOf(m), cell: (m) => <span className="mono">{openOf(m)}</span> },
    { id: "runs", header: "Runs", align: "r", sort: (m) => m.run_count, cell: (m) => <span className="mono">{m.run_count}</span> },
    { id: "cost", header: "Cost", align: "r", sort: (m) => Number(m.cost_usd), cell: (m) => <span className="mono">${Number(m.cost_usd).toFixed(4)}{Number(m.budget_usd) > 0 ? <span className="muted"> / ${m.budget_usd}</span> : null}</span> },
    { id: "last", header: "Last run", sort: (m) => m.last_run_at || "", cell: (m) => <span className="mono muted">{m.last_run_at ? ago(m.last_run_at) : "—"}</span> },
  ];
  return (
    <>
      <div className="filterbar wrap">
        <div className="search" style={{ width: 260 }}><Search size={14} /><input className="input" placeholder="搜索 Mission" value={s} onChange={(e) => setS(e.target.value)} /></div>
        <Seg value={status} onChange={(v) => setParams(route, { status: v === "active,paused" ? null : v })}
          options={[["active,paused", "Active"], ["completed,failed,stopped", "Finished"], ["", "All"]]} />
        <select className="input sm" value={ws} onChange={(e) => setParams(route, { ws: e.target.value || null })} aria-label="工作区">
          <option value="">All workspaces</option>{(wss.data || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>
      {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
        <DataTable id="at-missions" rows={rows} columns={cols} rowKey={(m) => m.id} onRowClick={(m) => go("missions", m.id)}
          empty={<EmptyState title="No missions here" icon={<Target size={18} />}><span className="muted sm">激活一个 Agent 会自动建立它的长期职责 Mission；也可以手动新建一个 One-time、Continuous、Event-driven 或 Scheduled Mission。</span></EmptyState>} />
      )}
    </>
  );
}

const openOf = (m: AtMission) => ["queued", "running", "waiting", "waiting_approval", "unassigned"].reduce((n, k) => n + (m.task_counts?.[k] || 0), 0);

export function TaskTable({ route, mission }: { route: Route; mission?: string }) {
  const status = route.params.get("status") || "";
  const kind = route.params.get("kind") || "";
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const tasks = useAtTasks({ mission_id: mission, status, kind, q, limit: 100, offset: page * 100 });
  const agents = useAtAgents(true);
  const name = (id?: string | null) => (id ? agents.data?.find((a) => a.id === id)?.name || id : "System");
  const cols: Column<AtTask>[] = [
    { id: "id", header: "Task", cell: (t) => <span className="mono">{t.id}</span>, defaultHidden: true },
    { id: "obj", header: "Objective", cell: (t) => <span className="ellipsis" title={t.objective} style={{ maxWidth: 380, display: "inline-block" }}>{t.objective}</span> },
    { id: "kind", header: "Kind", cell: (t) => <span className="tag">{t.kind}</span> },
    { id: "from", header: "From", cell: (t) => <span className="muted">{name(t.from_agent_id)}</span> },
    { id: "to", header: "To", cell: (t) => (t.to_agent_id ? name(t.to_agent_id) : <span className="acc">unassigned</span>) },
    { id: "st", header: "Status", cell: (t) => <TaskState status={t.status} /> },
    { id: "depth", header: "Depth", align: "r", cell: (t) => <span className="mono muted">{t.depth}</span>, defaultHidden: true },
    { id: "cost", header: "Cost", align: "r", cell: (t) => <span className="mono">${Number(t.cost_usd).toFixed(4)}</span> },
    { id: "created", header: "Created", cell: (t) => <span className="mono muted" title={fmtTime(t.created_at)}>{ago(t.created_at)}</span> },
    { id: "result", header: "Result", cell: (t) => <span className="muted ellipsis" style={{ maxWidth: 300, display: "inline-block" }} title={t.result?.summary || t.error || ""}>{t.result?.summary || t.error || "—"}</span> },
  ];
  const total = tasks.data?.total || 0;
  return (
    <>
      <div className="filterbar wrap">
        <div className="search" style={{ width: 260 }}><Search size={14} /><input className="input" placeholder="搜索任务" value={q} onChange={(e) => (setQ(e.target.value), setPage(0))} /></div>
        <Seg value={status} onChange={(v) => (setParams(route, { status: v || null }), setPage(0))}
          options={[["", "All"], ["queued,running,waiting,waiting_approval", "Open"], ["unassigned", "Unassigned"], ["completed", "Completed"], ["failed,rejected,cancelled", "Failed"]]} />
        <select className="input sm" value={kind} onChange={(e) => setParams(route, { kind: e.target.value || null })} aria-label="类型">
          <option value="">All kinds</option>{["investigate", "analyze", "fix", "test", "verify", "review", "report", "general"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <div className="grow" /><span className="muted xs mono">{total} tasks</span>
      </div>
      {tasks.isLoading ? <Loading rows={6} /> : tasks.error ? <ErrorState error={tasks.error} /> : (
        <DataTable id={`at-tasks${mission ? "-m" : ""}`} rows={tasks.data!.items} columns={cols} rowKey={(t) => t.id} onRowClick={(t) => setParams(route, { task: t.id })}
          empty={<EmptyState small title="没有任务">事件、定时或交接创建的每个任务都会出现在这里。</EmptyState>} />
      )}
      {total > 100 && (
        <div className="pager">
          <button className="btn xs" disabled={!page} onClick={() => setPage(page - 1)}>Prev</button>
          <span className="mono">{page * 100 + 1}–{Math.min(total, (page + 1) * 100)} / {total}</span>
          <button className="btn xs" disabled={(page + 1) * 100 >= total} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </>
  );
}

function MissionDetail({ id, route }: { id: string; route: Route }) {
  const q = useAtMission(id);
  const wss = useAtWorkspaces();
  const tab = (route.params.get("mt") as "tasks" | "activity" | "settings") || "tasks";
  const [busy, setBusy] = useState(false);
  const openTask = useCallback((t: string) => setParams(route, { task: t }), [route]);
  if (q.isLoading) return <div className="page"><Loading rows={8} /></div>;
  if (q.error) return <div className="page"><ErrorState error={q.error} /></div>;
  const m = q.data!;
  const act = async (a: string) => {
    if (a === "stop" && !confirm("停止这个 Mission？排队和待分配的任务会被取消，正在执行的任务会做完。")) return;
    setBusy(true);
    try {
      await post(`/api/at/missions/${id}/${a}`);
      toast({ run: "已创建一次执行", pause: "已暂停", resume: "已恢复", stop: "已停止" }[a] || "已更新");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <div className="toolbar">
        <button className="btn ghost icon" onClick={() => go("missions")} aria-label="返回"><ArrowLeft size={15} /></button>
        <h1>{m.name}</h1>
        <MissionState s={m.status} />
        <span className="muted sm">{MISSION_TYPE_LABEL[m.type]}</span>
        <div className="grow" />
        {m.status === "active" && <button className="btn" disabled={busy || DEMO} onClick={() => act("run")}><Zap size={13} /> Run now</button>}
        {m.status === "active" && <button className="btn" disabled={busy || DEMO} onClick={() => act("pause")}><Pause size={13} /> Pause</button>}
        {m.status === "paused" && <button className="btn primary" disabled={busy || DEMO} onClick={() => act("resume")}><Play size={13} /> Resume</button>}
        {(m.status === "active" || m.status === "paused") && <button className="btn ghost" disabled={busy || DEMO} onClick={() => act("stop")}><Square size={13} /> Stop</button>}
      </div>
      <div className="section grid2 stack-m">
        <div className="col gap8">
          <div className="label">Goal</div>
          <div className="note pre">{m.goal}</div>
          {m.result != null && (<><div className="label">Result</div><div className="note pre">{typeof m.result === "string" ? m.result : (m.result as { summary?: string }).summary || JSON.stringify(m.result)}</div></>)}
        </div>
        <dl className="kv">
          <dt>Workspace</dt><dd>{wss.data?.find((w) => w.id === m.workspace_id)?.name || m.workspace_id}</dd>
          <dt>Lead agent</dt><dd>{m.lead_agent_id ? <a className="link" href={`#/agents/${m.lead_agent_id}`}>{m.lead_name}</a> : "matched per task"}</dd>
          <dt>Agents</dt><dd>{(m.agents || []).map((a) => a.name).join(", ") || "—"}</dd>
          <dt>Triggers</dt><dd>{m.triggers.length ? m.triggers.map((t, i) => <div key={i} className="mono xs">{(t.types || ["any"]).join(", ")} ≥ {t.min_severity || "medium"}{t.watcher_id ? ` · ${t.watcher_id}` : ""}</div>) : <span className="muted">{m.type === "scheduled" ? "schedule only" : "none (woken by tasks only)"}</span>}</dd>
          {m.schedule_cron && (<><dt>Schedule</dt><dd className="mono">{m.schedule_cron} UTC · next {fmtTime(m.next_run_at)}</dd></>)}
          <dt>Permissions</dt><dd className="mono">{m.permissions.length ? m.permissions.join(", ") : "agents' own"}</dd>
          <dt>Budget</dt><dd className="mono">${Number(m.cost_usd).toFixed(4)} spent{Number(m.budget_usd) > 0 ? ` of $${m.budget_usd}` : " · no mission cap"}</dd>
          <dt>History</dt><dd className="mono">{m.run_count} runs · last {m.last_run_at ? ago(m.last_run_at) : "never"} · {Object.entries(m.task_counts || {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
          <dt>Created</dt><dd>{fmtTime(m.created_at)} by {m.created_by}</dd>
        </dl>
      </div>
      <div className="tabs-pad">
        <Tabs value={tab} onChange={(v) => setParams(route, { mt: v === "tasks" ? null : v })} options={[["tasks", "Tasks", m.tasks.length], ["activity", "Activity"], ...(m.responsibility_of ? [] : [["settings", "Settings"]]) as [ "settings", string][]]} />
      </div>
      {tab === "tasks" && <TaskTable route={route} mission={id} />}
      {tab === "activity" && <div className="section">{m.activity.length ? <div className="act-list">{m.activity.map((a) => <ActivityRow key={a.id} a={a} onTask={openTask} />)}</div> : <EmptyState small title="还没有活动" />}</div>}
      {tab === "settings" && <MissionSettings m={m} />}
    </div>
  );
}

function MissionSettings({ m }: { m: AtMission }) {
  const [f, setF] = useState({ name: m.name, goal: m.goal, budget_usd: m.budget_usd, schedule_cron: m.schedule_cron || "", types: (m.triggers[0]?.types || []).join(", "), min_severity: m.triggers[0]?.min_severity || "medium" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const types = f.types.split(",").map((x) => x.trim()).filter(Boolean);
      await put(`/api/at/missions/${m.id}`, { name: f.name, goal: f.goal, budget_usd: f.budget_usd, schedule_cron: f.schedule_cron || null,
        triggers: types.length ? [{ types, min_severity: f.min_severity }] : [] });
      toast("已保存");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="section col gap12" style={{ maxWidth: 720 }}>
      <label className="field"><span>Name</span><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field"><span>Goal</span><textarea className="input" rows={4} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} /></label>
      <div className="grid2">
        <label className="field"><span>Trigger event types (comma separated, * allowed)</span><input className="input mono" value={f.types} onChange={(e) => setF({ ...f, types: e.target.value })} placeholder="app.error, ci.failed, bsc.*" /></label>
        <label className="field"><span>From severity</span><select className="input" value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value as Severity })}>{["info", "low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="field"><span>Schedule (cron, UTC)</span><input className="input mono" value={f.schedule_cron} onChange={(e) => setF({ ...f, schedule_cron: e.target.value })} /></label>
        <label className="field"><span>Mission budget (USD, 0 = no cap)</span><input className="input mono" value={f.budget_usd} onChange={(e) => setF({ ...f, budget_usd: e.target.value })} /></label>
      </div>
      <div><button className="btn primary" disabled={busy || DEMO} onClick={save}>Save</button></div>
    </div>
  );
}

function MissionForm({ onClose }: { onClose: () => void }) {
  const meta = useAtMeta();
  const wss = useAtWorkspaces();
  const agents = useAtAgents();
  const [f, setF] = useState({ name: "", goal: "", type: "one_time", workspace_id: "", lead_agent_id: "", agent_ids: [] as string[], types: "app.error, ci.failed", min_severity: "medium", schedule_cron: "", budget_usd: "0" });
  const [busy, setBusy] = useState(false);
  const wid = f.workspace_id || wss.data?.[0]?.id || "";
  const pool = (agents.data || []).filter((a) => a.autonomous && a.status === "active" && a.workspaces.some((w) => w.id === wid));
  const submit = async () => {
    setBusy(true);
    try {
      const types = f.types.split(",").map((x) => x.trim()).filter(Boolean);
      const m = await post<AtMission>("/api/at/missions", {
        name: f.name, goal: f.goal, type: f.type, workspace_id: wid, lead_agent_id: f.lead_agent_id || pool[0]?.id || null, agent_ids: f.agent_ids,
        triggers: f.type === "event_driven" || f.type === "continuous" ? [{ types, min_severity: f.min_severity }] : [],
        schedule_cron: f.type === "scheduled" || f.type === "continuous" ? f.schedule_cron || null : null, budget_usd: f.budget_usd,
      });
      toast("Mission 已创建");
      refreshAt();
      onClose();
      go("missions", m.id);
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog wide title="New mission" onClose={onClose} footer={<><div className="grow" /><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || !f.name.trim() || !f.goal.trim() || !wid || !pool.length} onClick={submit}>Create</button></>}>
      <div className="grid2 stack-m">
        <label className="field"><span>Name</span><input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field"><span>Type</span>
          <select className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>{Object.entries(meta.data?.mission_types || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </label>
      </div>
      <label className="field"><span>Goal</span><textarea className="input" rows={4} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} /></label>
      <div className="grid2 stack-m">
        <label className="field"><span>Workspace</span><select className="input" value={wid} onChange={(e) => setF({ ...f, workspace_id: e.target.value, lead_agent_id: "", agent_ids: [] })}>{(wss.data || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        <label className="field"><span>Lead agent (woken first; hands work on to others)</span>
          <select className="input" value={f.lead_agent_id || pool[0]?.id || ""} onChange={(e) => setF({ ...f, lead_agent_id: e.target.value })}>{pool.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
        </label>
      </div>
      <div className="field"><span>Agents on this mission</span>
        <div className="chips">{pool.map((a) => <button type="button" key={a.id} className="chip" aria-pressed={f.agent_ids.includes(a.id)} onClick={() => setF({ ...f, agent_ids: f.agent_ids.includes(a.id) ? f.agent_ids.filter((x) => x !== a.id) : [...f.agent_ids, a.id] })}>{a.name}</button>)}</div>
        {!pool.length && <small>这个工作区还没有激活的 Agent。</small>}
      </div>
      {(f.type === "event_driven" || f.type === "continuous") && (
        <div className="grid2 stack-m">
          <label className="field"><span>Trigger event types</span><input className="input mono" value={f.types} onChange={(e) => setF({ ...f, types: e.target.value })} /></label>
          <label className="field"><span>From severity</span><select className="input" value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value })}>{["info", "low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}</select></label>
        </div>
      )}
      <div className="grid2 stack-m">
        {(f.type === "scheduled" || f.type === "continuous") && <label className="field"><span>Schedule (cron, UTC)</span><input className="input mono" value={f.schedule_cron} onChange={(e) => setF({ ...f, schedule_cron: e.target.value })} placeholder="0 * * * *" /></label>}
        <label className="field"><span>Budget (USD, 0 = no cap)</span><input className="input mono" value={f.budget_usd} onChange={(e) => setF({ ...f, budget_usd: e.target.value })} /></label>
      </div>
    </Dialog>
  );
}
