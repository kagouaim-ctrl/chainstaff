import { Archive, ArrowLeft, Bot, LayoutGrid, List, Pause, Play, Plus, Search, Send, Settings, Trash2, Zap } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { ApprovalMessage } from "../components/Approval";
import { ActivityRow, AgentState, TaskDialog, TaskState } from "../components/AtBits";
import { ChatView } from "../components/chat/ChatView";
import { DataTable, type Column } from "../components/DataTable";
import { WorkerCard } from "../components/WorkerCard";
import { COLOR_HEX, resolveAppearance, SHAPE_LABEL, ShapeMark, WorkerAvatar } from "../components/WorkerAvatar";
import { Dialog, EmptyState, ErrorState, Loading, Seg } from "../components/ui";
import { del, post, put } from "../lib/api";
import {
  AVATAR_COLORS, AVATAR_SHAPES, isWorking, MISSION_ST, MISSION_TYPE_LABEL, MODE_LABEL, refreshAt, useAtActivity, useAtAgent, useAtAgents, useAtMemory, useAtMeta, useAtWorkspaces,
  type AgentDetailAt, type Appearance, type AtAgentRow, type AtMeta, type AtTask, type WorkMode,
} from "../lib/at";
import { useChats } from "../lib/chat";
import { DEMO } from "../lib/demo";
import { openCreateWorker } from "../lib/shell";
import { ago, errText, fmtTime, go, setParams, toast, useIsMobile, usePref, type Route } from "../lib/util";
import { AgentWizard } from "./AgentWizard";

type Filter = "all" | "working" | "watching" | "attention" | "paused";
const IN: Record<Filter, (a: AtAgentRow) => boolean> = {
  all: () => true,
  working: isWorking,
  watching: (a) => a.state === "watching",
  attention: (a) => ["error", "offline", "waiting_approval"].includes(a.state) || !a.model_ready,
  paused: (a) => a.status === "paused",
};

export default function Agents({ route }: { route: Route }) {
  if (route.params.get("new")) return <AgentWizard onDone={(id) => go("agents", id)} onCancel={() => go("agents")} />;
  if (route.id) return <AgentDetailPage id={route.id} route={route} />;
  return <AgentList route={route} />;
}

function AgentList({ route }: { route: Route }) {
  const [archived, setArchived] = usePref<boolean>("at-agents-archived", false);
  const [viewPref, setView] = usePref<"card" | "table">("workers-view", "card");
  const mobile = useIsMobile();
  const view = mobile ? "card" : viewPref;
  const q = useAtAgents(archived);
  const [s, setS] = useState("");
  const filter = (route.params.get("f") as Filter) || "all";
  const [busy, setBusy] = useState<string | null>(null);
  const rows = useMemo(() => {
    const t = s.trim().toLowerCase();
    return (q.data || []).filter((a) => IN[filter](a) && (!t || `${a.name} ${a.responsibility} ${a.id} ${a.workspaces.map((w) => w.name).join(" ")}`.toLowerCase().includes(t)));
  }, [q.data, s, filter]);
  const act = async (a: AtAgentRow, action: string) => {
    if (action === "archive" && !confirm(`归档 ${a.name}？它的长期职责 Mission 会停止，排队的任务变成待分配。`)) return;
    setBusy(a.id);
    try {
      await post(`/api/at/agents/${a.id}/${action}`);
      toast({ pause: "已暂停", resume: "已恢复", archive: "已归档", activate: "已激活" }[action] || "已更新");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(null);
    }
  };
  const cols: Column<AtAgentRow>[] = [
    { id: "name", header: "Agent", sort: (a) => a.name, cell: (a) => <span className="row gap8"><WorkerAvatar id={a.id} appearance={a.appearance} size={26} dim={a.status === "paused"} /><b>{a.name}</b>{!a.activated && a.status !== "archived" && <span className="badge acc">Not activated</span>}</span> },
    { id: "resp", header: "Responsibility", cell: (a) => <span className="muted ellipsis" title={a.responsibility} style={{ maxWidth: 340, display: "inline-block" }}>{a.responsibility || "—"}</span> },
    { id: "ws", header: "Workspace", cell: (a) => (a.workspaces.length ? a.workspaces.map((w) => w.name).join(", ") : <span className="muted">—</span>) },
    { id: "mode", header: "Mode", sort: (a) => a.work_mode, cell: (a) => <span className="muted">{MODE_LABEL[a.work_mode]}</span>, defaultHidden: true },
    { id: "state", header: "Status", sort: (a) => a.state, cell: (a) => <AgentState state={a.state} detail={a.state_detail} /> },
    { id: "work", header: "Current Task", cell: (a) => <span className="ellipsis" title={a.current_task?.objective || a.state_detail} style={{ maxWidth: 300, display: "inline-block" }}>{a.current_task?.objective || <span className="muted">{a.state_detail}</span>}</span> },
    { id: "wake", header: "Last Active", sort: (a) => a.last_activity_at || a.last_wake_at || "", cell: (a) => { const t = a.last_activity_at || a.last_wake_at; return <span className="num muted" title={fmtTime(t)}>{t ? ago(t) : "never"}</span>; } },
    { id: "model", header: "Model", cell: (a) => <span className={a.model_ready ? "muted mono" : "bad mono"} title={a.model_ready ? "" : "服务器没有这个模型服务的 API Key"}>{a.model || a.provider}</span>, defaultHidden: true },
    { id: "act", header: "Actions", hideable: false, align: "r", cell: (a) => (
      <span className="row gap2" style={{ justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()}>
        <button className="btn xs" onClick={() => go("agents", a.id)}>Chat</button>
        {a.status !== "archived" && !a.activated && <button className="btn xs primary" disabled={busy === a.id || DEMO} onClick={() => act(a, "activate")}><Zap size={11} /> Activate</button>}
        {a.status === "active" && a.activated && <button className="btn xs" disabled={busy === a.id || DEMO} onClick={() => act(a, "pause")} title="Pause"><Pause size={11} /></button>}
        {a.status === "paused" && <button className="btn xs" disabled={busy === a.id || DEMO} onClick={() => act(a, "resume")} title="Resume"><Play size={11} /></button>}
        {a.status !== "archived" && <button className="btn xs" onClick={() => go("agents", a.id, { tab: "settings" })}>Settings</button>}
        {a.status !== "archived" && <button className="btn xs ghost" disabled={busy === a.id || DEMO} onClick={() => act(a, "archive")} title="Archive" aria-label="Archive"><Archive size={11} /></button>}
      </span>
    ) },
  ];
  const all = q.data || [];
  const empty = all.length ? <EmptyState small title="没有符合筛选条件的 Agent" /> : (
    <EmptyState title="建立第一个 Agent" icon={<Bot size={20} />}>
      <span className="muted">起个名字、选个形象就能保存；准备好后再写职责、绑定工作区。</span>
      {!DEMO && <button className="btn primary" onClick={openCreateWorker}><Plus size={14} /> New Agent</button>}
    </EmptyState>
  );
  return (
    <div className="page">
      <div className="toolbar">
        <div className="col gap4">
          <h1>Agents <span className="count">{all.length}</span></h1>
          <span className="sub">点一个 Agent 就能和它对话；它的工具、权限、触发条件和记忆在 Settings 里。</span>
        </div>
        <div className="grow" />
        <button className="btn primary" disabled={DEMO} onClick={openCreateWorker}><Plus size={14} /> New Agent</button>
      </div>
      <div className="filterbar wrap">
        <div className="search" style={{ width: 280 }}>
          <Search size={14} />
          <input className="input" placeholder="搜索名称、职责、工作区" value={s} onChange={(e) => setS(e.target.value)} aria-label="筛选 Agent" />
        </div>
        <Seg<Filter> value={filter} onChange={(v) => setParams(route, { f: v === "all" ? null : v })}
          options={[["all", `All ${all.length}`], ["working", `Working ${all.filter(IN.working).length}`], ["watching", `Watching ${all.filter(IN.watching).length}`],
            ["attention", `Needs attention ${all.filter(IN.attention).length}`], ["paused", `Paused ${all.filter(IN.paused).length}`]]} />
        <div className="grow" />
        <label className="check xs"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived</label>
        <div className="seg d-only" role="group" aria-label="View">
          <button aria-pressed={view === "card"} onClick={() => setView("card")} title="Card view"><LayoutGrid size={13} /> Cards</button>
          <button aria-pressed={view === "table"} onClick={() => setView("table")} title="Table view"><List size={13} /> Table</button>
        </div>
      </div>
      {q.isLoading ? <div className="wk-grid page-grid">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="wk-card sk"><i className="skeleton" /><i className="skeleton" /><i className="skeleton" /></div>)}</div>
        : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} />
        : view === "card" ? (rows.length ? <div className="wk-grid page-grid">{rows.map((a) => <WorkerCard key={a.id} a={a} />)}</div> : <div className="page-grid">{empty}</div>)
        : <DataTable id="at-agents" rows={rows} columns={cols} rowKey={(a) => a.id} onRowClick={(a) => go("agents", a.id)} empty={empty} />}
    </div>
  );
}

type Tab = "chat" | "overview" | "work" | "activity" | "tools" | "memory" | "settings";
const CONFIG: [Tab, string][] = [["overview", "概况"], ["settings", "设置"], ["tools", "工具与权限"], ["memory", "记忆"], ["work", "任务与执行记录"], ["activity", "活动日志"]];

/**
 * An agent's page opens on a conversation with it: who it is, what it is doing now, and an input. Configuration
 * (model, tools, permissions, triggers, memory, budget) and the full execution record sit behind Settings.
 */
function AgentDetailPage({ id, route }: { id: string; route: Route }) {
  const q = useAtAgent(id);
  const meta = useAtMeta();
  const tab = (route.params.get("tab") as Tab) || "chat";
  const task = route.params.get("task");
  const openTask = useCallback((t: string) => setParams(route, { task: t }), [route]);
  const [give, setGive] = useState(false);
  const [busy, setBusy] = useState(false);
  if (q.isLoading) return <div className="page"><Loading rows={8} /></div>;
  if (q.error) return <div className="page"><ErrorState error={q.error} retry={() => q.refetch()} /></div>;
  const a = q.data!;
  const act = async (action: string) => {
    if (action === "archive" && !confirm(`归档 ${a.name}？它的长期职责 Mission 会停止，排队的任务变成待分配。`)) return;
    setBusy(true);
    try {
      const r = await post<{ warnings?: string[] }>(`/api/at/agents/${id}/${action}`);
      toast(action === "activate" ? (r.warnings?.length ? `已激活。${r.warnings.join("；")}` : "已激活") : { pause: "已暂停", resume: "已恢复", archive: "已归档" }[action] || "已更新", !!r.warnings?.length);
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const config = tab !== "chat";
  return (
    <div className="page agent-page">
      <header className="agent-head">
        <button className="btn ghost icon" onClick={() => (config ? setParams(route, { tab: null }) : go("agents"))} aria-label={config ? "返回对话" : "返回 Agents"}><ArrowLeft size={16} /></button>
        <WorkerAvatar id={a.id} appearance={a.appearance} size={40} dim={a.status === "paused"} />
        <div className="col gap2 grow" style={{ minWidth: 0 }}>
          <div className="row gap8" style={{ minWidth: 0 }}>
            <h1 className="agent-name ellipsis">{a.name}</h1>
            {a.activated || a.status === "archived" ? <AgentState state={a.state} detail={a.state_detail} /> : <span className="badge acc">未激活</span>}
          </div>
          <span className="agent-resp ellipsis" title={a.responsibility}>{a.responsibility || "还没有写职责"}</span>
        </div>
        {a.status !== "archived" && !a.activated && <button className="btn primary sm" disabled={busy || DEMO} onClick={() => act("activate")}><Zap size={13} /> 激活</button>}
        {a.status === "paused" && <button className="btn primary sm" disabled={busy || DEMO} onClick={() => act("resume")}><Play size={13} /> 恢复</button>}
        {a.status === "active" && a.activated && <button className="btn ghost sm d-only" disabled={busy || DEMO} onClick={() => act("pause")} title="暂停：不再被唤醒，排队的任务保留"><Pause size={13} /> 暂停</button>}
        <button className={`btn sm ${config ? "secondary" : "ghost"}`} aria-pressed={config} onClick={() => setParams(route, { tab: config ? null : "overview" })}><Settings size={14} /> <span className="hide-s">Settings</span></button>
      </header>
      {!a.activated && a.status !== "archived" && (
        <div className="notice-bar">
          <Zap size={14} className="acc" />
          <span className="grow">{a.name} 还没有激活。{!a.responsibility.trim() ? "先写下它的职责" : "职责已写好"}{a.work_mode !== "on_demand" ? (a.workspaces.length ? "，已绑定工作区" : "，再绑定一个工作区") : ""}，然后激活。模型、技能和权限都有安全的默认值。</span>
          {tab !== "settings" && <button className="btn sm" onClick={() => setParams(route, { tab: "settings" })}>去设置</button>}
        </div>
      )}
      {!a.model_ready && a.status !== "archived" && (
        <div className="notice-bar warn"><span className="grow">服务器没有配置 {a.provider} 的 API Key：这个 Agent 被唤醒时会执行失败并通知你。</span><a className="link xs" href="#/settings?s=models">配置模型</a></div>
      )}
      {config ? (
        <div className="agent-config">
          <nav className="config-nav" aria-label="Agent settings">
            {CONFIG.map(([k, l]) => <button key={k} className="rail-item" aria-current={tab === k} onClick={() => setParams(route, { tab: k })}>{l}{k === "work" && a.open_tasks.length ? <span className="n">{a.open_tasks.length}</span> : null}</button>)}
            {a.status !== "archived" && <button className="rail-item danger-text" disabled={busy || DEMO} onClick={() => act("archive")}><Archive size={14} /> 归档 Agent</button>}
          </nav>
          <div className="config-body">
            {tab === "overview" && <AgentOverview a={a} onTask={openTask} />}
            {tab === "work" && <AgentWork a={a} onTask={openTask} onGive={() => setGive(true)} />}
            {tab === "activity" && <AgentActivity id={id} onTask={openTask} />}
            {tab === "tools" && meta.data && <AgentTools a={a} meta={meta.data} />}
            {tab === "memory" && <AgentMemory id={id} />}
            {tab === "settings" && meta.data && <AgentSettings a={a} meta={meta.data} />}
          </div>
        </div>
      ) : <AgentChat a={a} route={route} onTask={openTask} />}
      {give && <GiveTask a={a} onClose={() => setGive(false)} />}
      {task && <TaskDialog id={task} onClose={() => setParams(route, { task: null })} />}
    </div>
  );
}

/** The conversation with this agent (its latest chat, or a new one), and beside it what it is working on. */
function AgentChat({ a, route, onTask }: { a: AgentDetailAt; route: Route; onTask: (id: string) => void }) {
  const chats = useChats(a.id);
  const param = route.params.get("chat");
  const chatId = param === "new" ? null : param || chats.data?.[0]?.id || null;
  const cannot = a.status === "archived" ? "这个 Agent 已归档，不能再交任务。" : !a.autonomous ? "这个 Agent 还没有设置长期职责（旧版员工），不能接收任务。先在 Settings 里写下职责并激活。"
    : a.status === "paused" ? "这个 Agent 已暂停：消息会排队，恢复后才会执行。" : null;
  const pending = a.approvals.filter((x) => x.status === "pending");
  const recent = [...a.open_tasks, ...a.recent_tasks].slice(0, 8);
  if (chats.isLoading && !param) return <div className="page"><Loading rows={4} /></div>;
  return (
    <div className="agent-body">
      <ChatView key={chatId || "new"} chatId={chatId} fixedAgent={{ id: a.id, name: a.name }} onTask={onTask}
        onCreated={(c) => setParams(route, { chat: c })} placeholder={`给 ${a.name} 交代任务或提问…`}
        top={<>
          {cannot && <div className="notice warn"><span className="grow">{cannot}</span></div>}
          {pending.map((x) => <ApprovalMessage key={x.id} a={{ ...x, agent_name: x.agent_name || a.name }} />)}
        </>}
        empty={
          <div className="welcome agent-welcome">
            <WorkerAvatar id={a.id} appearance={a.appearance} size={56} />
            <h1>和 {a.name} 对话</h1>
            <p className="muted">{a.responsibility || "这个 Agent 还没有写职责。"}</p>
            {a.current_task && <button className="now-card" onClick={() => onTask(a.current_task!.id)}><span className="label">正在做</span><span className="ellipsis">{a.current_task.objective}</span><TaskState status={a.current_task.status} /></button>}
          </div>
        } />
      <aside className="agent-side d-only">
        <div className="agent-side-h">
          <span className="label grow">对话</span>
          <button className="btn ghost xs" onClick={() => setParams(route, { chat: "new" })} disabled={DEMO}><Plus size={12} /> 新对话</button>
        </div>
        {(chats.data || []).slice(0, 6).map((c) => (
          <button key={c.id} className="side-item chat-item" aria-current={c.id === chatId ? "true" : undefined} onClick={() => setParams(route, { chat: c.id })}><span className="side-label">{c.title}</span></button>
        ))}
        {!chats.data?.length && <span className="muted xs pad-x">还没有对话</span>}
        <div className="agent-side-h"><span className="label grow">最近工作</span><button className="btn ghost xs" onClick={() => setParams(route, { tab: "work" })}>全部</button></div>
        {recent.length ? recent.map((t) => (
          <button key={t.id} className="work-row" onClick={() => onTask(t.id)} title={t.objective}>
            <TaskState status={t.status} />
            <span className="ellipsis grow">{t.objective}</span>
            <span className="muted xs">{ago(t.created_at)}</span>
          </button>
        )) : <span className="muted xs pad-x">还没有任务</span>}
      </aside>
    </div>
  );
}

function AgentOverview({ a, onTask }: { a: AgentDetailAt; onTask: (id: string) => void }) {
  const c = a.task_counts;
  return (
    <div className="section col gap16" style={{ maxWidth: 1100 }}>
      <div className="grid2 stack-m">
        <div className="col gap8">
          <div className="label">Responsibility</div>
          <div className="note pre">{a.responsibility || "—"}</div>
          <div className="label">Right now</div>
          <div className="row gap6"><AgentState state={a.state} /> <span className="sm">{a.state_detail}</span></div>
          {a.current_task && <button className="plain-btn link sm" style={{ textAlign: "left" }} onClick={() => onTask(a.current_task!.id)}>{a.current_task.objective}</button>}
        </div>
        <dl className="kv">
          <dt>Workspaces</dt><dd>{a.workspaces.map((w) => w.name).join(", ") || "—"}</dd>
          <dt>Mode</dt><dd>{MODE_LABEL[a.work_mode]}{a.agent.schedule_cron ? ` · cron ${a.agent.schedule_cron} UTC` : ""}</dd>
          <dt>Woken by</dt><dd>{a.work_mode === "scheduled" ? "its schedule" : a.work_mode === "on_demand" ? "tasks only" : a.agent.wake_on === null ? "default problem events" : a.agent.wake_on.length ? a.agent.wake_on.join(", ") : "tasks from other agents only"}</dd>
          <dt>Model</dt><dd className="mono">{a.provider} · {a.model}</dd>
          <dt>Budget</dt><dd className="mono">${Number(a.spent_usd || 0).toFixed(4)} spent{Number(a.budget_usd) > 0 ? ` of $${a.budget_usd}` : " · no agent cap"}</dd>
          <dt>Last wake-up</dt><dd>{a.last_wake_at ? `${ago(a.last_wake_at)} · ${fmtTime(a.last_wake_at)}` : "never"}</dd>
          <dt>Tasks</dt><dd className="mono">{Object.entries(c).map(([k, v]) => `${k} ${v}`).join(" · ") || "none yet"}</dd>
          <dt>Activated</dt><dd>{a.agent.activated_at ? fmtTime(a.agent.activated_at) : "not yet"}</dd>
        </dl>
      </div>
      {!!a.approvals.length && (<div className="col gap8"><div className="label">审批</div>{a.approvals.map((x) => <ApprovalMessage key={x.id} a={{ ...x, agent_name: x.agent_name || a.name }} compact={x.status !== "pending"} />)}</div>)}
      <div className="col gap8">
        <div className="label">Missions</div>
        {a.missions.length ? (
          <div className="table-wrap boxed">
            <table className="dt">
              <thead><tr><th>Mission</th><th>Type</th><th>Workspace</th><th>State</th><th>Runs</th><th>Last run</th></tr></thead>
              <tbody>
                {a.missions.map((m) => (
                  <tr key={m.id} className="click" onClick={() => go("missions", m.id)}>
                    <td>{m.name}{m.responsibility_of === a.id && <span className="tag" style={{ marginLeft: 6 }}>standing</span>}</td>
                    <td className="muted">{MISSION_TYPE_LABEL[m.type] || m.type}</td><td className="muted">{a.workspaces.find((w) => w.id === m.workspace_id)?.name || m.workspace_id}</td>
                    <td><span className={`st ${MISSION_ST[m.status]?.[1] || ""}`}><i /><span className="t">{MISSION_ST[m.status]?.[0] || m.status}</span></span></td>
                    <td className="mono">{m.run_count}</td><td className="mono muted">{m.last_run_at ? ago(m.last_run_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <span className="muted sm">{a.activated ? "On-demand：没有长期职责 Mission，只在被交任务时工作。" : "激活后会为每个绑定的工作区建立一个长期职责 Mission。"}</span>}
      </div>
    </div>
  );
}

function TaskList({ rows, onTask, empty, show = "to" }: { rows: AtTask[]; onTask: (id: string) => void; empty: string; show?: "to" | "from" }) {
  const agents = useAtAgents(true);
  const name = (id?: string | null) => (id ? agents.data?.find((a) => a.id === id)?.name || id : "System");
  if (!rows.length) return <span className="muted sm">{empty}</span>;
  return (
    <div className="table-wrap boxed">
      <table className="dt">
        <thead><tr><th>Task</th><th>Kind</th><th>{show === "to" ? "From" : "To"}</th><th>Status</th><th>Created</th><th>Result</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className="click" onClick={() => onTask(t.id)}>
              <td className="ellipsis" style={{ maxWidth: 360 }} title={t.objective}>{t.objective}</td>
              <td><span className="tag">{t.kind}</span></td>
              <td className="muted">{show === "to" ? name(t.from_agent_id) : name(t.to_agent_id)}</td>
              <td><TaskState status={t.status} /></td>
              <td className="mono muted">{ago(t.created_at)}</td>
              <td className="muted ellipsis" style={{ maxWidth: 320 }} title={t.result?.summary || t.error || ""}>{t.result?.summary || t.error || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AgentWork({ a, onTask, onGive }: { a: AgentDetailAt; onTask: (id: string) => void; onGive: () => void }) {
  return (
    <div className="section col gap16">
      {a.status !== "archived" && a.autonomous && (
        <div className="row gap8"><span className="muted sm grow">需要指定优先级、截止时间或工作区时，用高级方式交任务；平时直接在对话里说就行。</span><button className="btn sm" disabled={DEMO} onClick={onGive}><Send size={13} /> 高级：交任务</button></div>
      )}
      <div className="col gap8"><div className="label">Open tasks</div><TaskList rows={a.open_tasks} onTask={onTask} empty="没有进行中的任务" /></div>
      <div className="col gap8"><div className="label">Handed to other agents</div><TaskList rows={a.delegated} onTask={onTask} empty="还没有交出任务" show="from" /></div>
      <div className="col gap8"><div className="label">Finished</div><TaskList rows={a.recent_tasks} onTask={onTask} empty="还没有完成的任务" /></div>
      <div className="col gap8">
        <div className="label">Runs</div>
        {a.runs.length ? (
          <div className="table-wrap boxed">
            <table className="dt">
              <thead><tr><th>Run</th><th>Task</th><th>Status</th><th>Model calls</th><th>Tool calls</th><th>Tokens</th><th>Cost</th><th>Started</th></tr></thead>
              <tbody>
                {a.runs.map((r) => (
                  <tr key={r.id} className="click" onClick={() => onTask(r.task_id)}>
                    <td className="mono">{r.id}{r.resumed ? <span className="tag" style={{ marginLeft: 6 }}>resumed</span> : null}</td>
                    <td className="mono muted">{r.task_id}</td>
                    <td><span className={`st ${r.status === "completed" ? "ok" : r.status === "failed" ? "bad" : r.status === "running" ? "info live" : "mute"}`}><i /><span className="t">{r.status}</span></span></td>
                    <td className="mono">{r.model_calls}</td><td className="mono">{r.tool_calls}</td><td className="mono">{r.tokens_in + r.tokens_out}</td>
                    <td className="mono">${Number(r.cost_usd).toFixed(4)}</td><td className="mono muted">{ago(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <span className="muted sm">还没有执行记录</span>}
      </div>
    </div>
  );
}

function AgentActivity({ id, onTask }: { id: string; onTask: (id: string) => void }) {
  const q = useAtActivity({ agent_id: id, limit: 200 });
  if (q.isLoading) return <div className="section"><Loading rows={6} /></div>;
  if (q.error) return <div className="section"><ErrorState error={q.error} /></div>;
  return <div className="section">{q.data!.length ? <div className="act-list">{q.data!.map((x) => <ActivityRow key={x.id} a={x} onTask={onTask} />)}</div> : <EmptyState small title="还没有活动" />}</div>;
}

function AgentTools({ a, meta }: { a: AgentDetailAt; meta: AtMeta }) {
  const [perms, setPerms] = useState<string[]>(a.agent.permissions);
  const [skills, setSkills] = useState<string[]>(a.agent.skills);
  const [busy, setBusy] = useState(false);
  const dirty = perms.join() !== a.agent.permissions.join() || skills.join() !== a.agent.skills.join();
  const save = async () => {
    setBusy(true);
    try {
      await put(`/api/at/agents/${a.id}`, { permissions: perms, skills });
      toast("权限和技能已保存");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const tools = meta.tools.filter((t) => !t.permission || perms.includes(t.permission));
  return (
    <div className="section col gap16" style={{ maxWidth: 1000 }}>
      <PermissionPicker meta={meta} value={perms} onToggle={(p) => toggle(perms, setPerms, p)} />
      <div className="col gap8">
        <div className="label">Web3 skills (read-only chain tools)</div>
        <div className="chips">
          {meta.skills.map((s) => (
            <button key={s.id} className="chip" aria-pressed={skills.includes(s.id)} onClick={() => toggle(skills, setSkills, s.id)} title={s.description + (s.missing.length ? ` · 缺少：${s.missing.join(", ")}` : "") + (s.needs_chain ? " · 服务器没有配置链" : "")}>
              {s.name}{(!s.enabled || s.missing.length || s.needs_chain) && <span className="bad">·</span>}
            </button>
          ))}
        </div>
        <span className="muted xs">技能需要它自己的权限（例如 chain:read）。标红点的技能在这台服务器上不能运行（未安装、未启用或缺少配置），见 Settings → Tool Library。</span>
      </div>
      <div className="row gap6">
        <button className="btn primary" disabled={!dirty || busy || DEMO} onClick={save}>Save</button>
        {dirty && <button className="btn ghost" onClick={() => (setPerms(a.agent.permissions), setSkills(a.agent.skills))}>Reset</button>}
      </div>
      <div className="col gap8">
        <div className="label">Tools this agent can call now ({tools.length})</div>
        <div className="table-wrap boxed">
          <table className="dt">
            <thead><tr><th>Tool</th><th>Permission</th><th>Risk</th><th>What it does</th></tr></thead>
            <tbody>{tools.map((t) => <tr key={t.name}><td className="mono">{t.name}</td><td className="mono muted">{t.permission || "always"}</td><td className={t.risk === "high" ? "bad" : t.risk === "medium" ? "acc" : "muted"}>{t.risk}</td><td className="muted ellipsis" style={{ maxWidth: 480 }} title={t.description}>{t.description}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function PermissionPicker({ meta, value, onToggle }: { meta: AtMeta; value: string[]; onToggle: (p: string) => void }) {
  const chain = ["chain:read"];
  return (
    <div className="col gap8">
      <div className="label">Permissions</div>
      <div className="perm-grid">
        {[...Object.entries(meta.permissions), ...chain.map((p) => [p, ["读取链上数据（RPC、浏览器 API）", "low"]] as [string, [string, string]])].map(([p, [desc, risk]]) => (
          <label key={p} className={`perm ${value.includes(p) ? "on" : ""}`}>
            <input type="checkbox" checked={value.includes(p)} onChange={() => onToggle(p)} />
            <span className="col gap2"><span className="mono sm">{p} <span className={`xs ${risk === "high" ? "bad" : risk === "medium" ? "acc" : "muted"}`}>{risk}</span></span><span className="muted xs">{desc}</span></span>
          </label>
        ))}
        {Object.entries(meta.human_only).map(([p, desc]) => (
          <label key={p} className="perm off" title="只有人可以做，任何 Agent 都不能获得">
            <input type="checkbox" checked={false} disabled />
            <span className="col gap2"><span className="mono sm">{p} <span className="xs bad">human only</span></span><span className="muted xs">{desc}</span></span>
          </label>
        ))}
      </div>
      <span className="muted xs">默认只读。补丁和 Draft PR 永远需要人工批准；合并、部署、合约升级、权限变更、签名转账和读取凭据只能由人完成。</span>
    </div>
  );
}

function AgentMemory({ id }: { id: string }) {
  const [q, setQ] = useState("");
  const m = useAtMemory(id, q);
  const forget = async (mid: number) => {
    try {
      await del(`/api/at/agents/${id}/memory/${mid}`);
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    }
  };
  return (
    <div className="section col gap8" style={{ maxWidth: 900 }}>
      <div className="search" style={{ width: 320 }}><Search size={14} /><input className="input" placeholder="搜索记忆" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {m.isLoading ? <Loading /> : m.error ? <ErrorState error={m.error} /> : !m.data!.length ? <EmptyState small title="还没有记忆">Agent 用 remember 工具保存的事实（自己的，或工作区共享的）会出现在这里，下次被唤醒时会读到。</EmptyState> : (
        <div className="col">
          {m.data!.map((x) => (
            <div key={x.id} className="list-row">
              <span className="tag">{x.scope}</span>
              <span className="grow">{x.text}</span>
              <span className="muted xs mono">{ago(x.created_at)}</span>
              <button className="btn ghost xs icon" disabled={DEMO} onClick={() => confirm("删除这条记忆？") && forget(x.id)} aria-label="删除"><Trash2 size={11} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentSettings({ a, meta }: { a: AgentDetailAt; meta: AtMeta }) {
  const ws = useAtWorkspaces();
  const g = a.agent;
  const [f, setF] = useState({
    name: a.name, responsibility: a.responsibility, work_mode: a.work_mode as WorkMode, schedule_cron: g.schedule_cron || "",
    workspace_ids: a.workspaces.map((w) => w.id), wake_on: g.wake_on, budget_usd: String(a.budget_usd || "0"), model: a.model || "", provider: a.provider || "anthropic",
    max_task_budget_usd: String(g.approval_rules?.max_task_budget_usd ?? ""), min_severity: g.notify_rules?.min_severity || "medium",
    on_failure: g.notify_rules?.on_failure ?? true, on_completion: g.notify_rules?.on_completion ?? false, system_prompt: a.system_prompt,
    appearance: resolveAppearance(a.id, a.appearance) as Appearance,
  });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await put(`/api/at/agents/${a.id}`, {
        name: f.name, responsibility: f.responsibility, work_mode: f.work_mode, schedule_cron: f.schedule_cron || null, workspace_ids: f.workspace_ids,
        wake_on: f.wake_on, budget_usd: f.budget_usd || "0", provider: f.provider, model: f.model, system_prompt: f.system_prompt,
        approval_rules: { max_task_budget_usd: f.max_task_budget_usd || null }, notify_rules: { min_severity: f.min_severity, on_failure: f.on_failure, on_completion: f.on_completion },
        appearance: f.appearance,
      });
      toast("已保存");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="section col gap12" style={{ maxWidth: 820 }}>
      <div className="row gap16 wrap">
        <WorkerAvatar id={a.id} appearance={f.appearance} size={64} />
        <div className="col gap8 grow">
          <label className="field"><span>Name</span><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <div className="row gap6 wrap" role="radiogroup" aria-label="Avatar">
            {AVATAR_SHAPES.map((x) => (
              <button key={x} type="button" role="radio" aria-checked={f.appearance.shape === x} className="cw-shape sm" title={SHAPE_LABEL[x]}
                style={{ color: f.appearance.shape === x ? COLOR_HEX[f.appearance.color || "gold"] : undefined }} onClick={() => setF({ ...f, appearance: { ...f.appearance, shape: x } })}>
                <ShapeMark shape={x} size={18} />
              </button>
            ))}
            <span className="sep-v" />
            {AVATAR_COLORS.map((c) => (
              <button key={c} type="button" role="radio" aria-checked={f.appearance.color === c} aria-label={c} className="cw-color sm" style={{ background: COLOR_HEX[c] }}
                onClick={() => setF({ ...f, appearance: { ...f.appearance, color: c } })} />
            ))}
          </div>
        </div>
      </div>
      <label className="field"><span>Responsibility</span><textarea className="input" rows={4} placeholder="What is this worker in charge of? e.g. 持续监控国库钱包的大额转出，发现异常时调查并通知我。" value={f.responsibility} onChange={(e) => setF({ ...f, responsibility: e.target.value })} /></label>
      <WorkspacePicker value={f.workspace_ids} onChange={(v) => setF({ ...f, workspace_ids: v })} options={(ws.data || []).map((w) => ({ id: w.id, name: w.name }))} />
      <ModePicker meta={meta} mode={f.work_mode} cron={f.schedule_cron} wake={f.wake_on} onChange={(p) => setF({ ...f, ...p })} />
      <div className="grid2 stack-m">
        <label className="field"><span>Model provider</span>
          <select className="input" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })}>
            <option value="anthropic">Anthropic (Claude)</option><option value="openai_compat">OpenAI-compatible (Qwen, Gemini, local…)</option>
          </select>
        </label>
        <label className="field"><span>Model</span><input className="input mono" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder={meta.models.default_model} /></label>
        <label className="field"><span>Agent budget (USD, 0 = no cap)</span><input className="input mono" value={f.budget_usd} onChange={(e) => setF({ ...f, budget_usd: e.target.value })} /></label>
        <label className="field"><span>Max budget per task (USD)</span><input className="input mono" value={f.max_task_budget_usd} onChange={(e) => setF({ ...f, max_task_budget_usd: e.target.value })} placeholder={String(meta.limits.default_task_budget)} /></label>
        <label className="field"><span>Notify me from severity</span>
          <select className="input" value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value })}>{meta.severities.map((s) => <option key={s}>{s}</option>)}</select>
        </label>
        <div className="col gap6" style={{ justifyContent: "flex-end" }}>
          <label className="check"><input type="checkbox" checked={f.on_failure} onChange={(e) => setF({ ...f, on_failure: e.target.checked })} /> Notify when a task fails</label>
          <label className="check"><input type="checkbox" checked={f.on_completion} onChange={(e) => setF({ ...f, on_completion: e.target.checked })} /> Notify when a task completes</label>
        </div>
      </div>
      <label className="field"><span>Extra instructions (optional)</span><textarea className="input" rows={3} value={f.system_prompt} onChange={(e) => setF({ ...f, system_prompt: e.target.value })} /></label>
      <div className="row gap6"><button className="btn primary" disabled={busy || DEMO} onClick={save}>Save</button></div>
    </div>
  );
}

export function WorkspacePicker({ value, onChange, options }: { value: string[]; onChange: (v: string[]) => void; options: { id: string; name: string }[] }) {
  return (
    <div className="field">
      <span>Workspaces</span>
      {options.length ? (
        <div className="chips">
          {options.map((w) => <button key={w.id} type="button" className="chip" aria-pressed={value.includes(w.id)} onClick={() => onChange(value.includes(w.id) ? value.filter((x) => x !== w.id) : [...value, w.id])}>{w.name}</button>)}
        </div>
      ) : <small>还没有工作区。先在 Workspace 页面建一个（仓库、合约、代币、钱包、API）。</small>}
    </div>
  );
}

const WAKE_TYPES: [string, string][] = [
  ["app.error", "Application errors"], ["ci.failed", "CI failures"], ["health.down", "API down"], ["health.recovered", "API recovered (verify)"], ["github.workflow_failed", "GitHub workflow failed"],
  ["patch.applied", "A fix was applied (verify)"], ["pr.merged", "A PR was merged (verify)"], ["bsc.*", "All BSC events"], ["bsc.large_transfer", "BSC large transfers"],
  ["bsc.contract_event", "BSC contract events"], ["github.push", "GitHub pushes"], ["github.issue.*", "GitHub issues"], ["manual.report", "Reports you file"],
];
const DEFAULT_WAKE = ["app.error", "ci.failed", "health.down", "github.workflow_failed", "bsc.*", "patch.applied", "pr.merged"];

export function ModePicker({ meta, mode, cron, wake, onChange }: {
  meta: AtMeta; mode: WorkMode; cron: string; wake: string[] | null; onChange: (p: { work_mode?: WorkMode; schedule_cron?: string; wake_on?: string[] | null }) => void;
}) {
  const w = wake ?? DEFAULT_WAKE;
  return (
    <div className="col gap8">
      <div className="mode-grid">
        {(Object.keys(meta.work_modes) as WorkMode[]).map((m) => (
          <label key={m} className={`perm ${mode === m ? "on" : ""}`}>
            <input type="radio" name="mode" checked={mode === m} onChange={() => onChange({ work_mode: m })} />
            <span className="col gap2"><span className="sm strong">{MODE_LABEL[m]}{m === "event" ? " · default" : ""}</span><span className="muted xs">{meta.work_modes[m].split("：")[1]}</span></span>
          </label>
        ))}
      </div>
      {(mode === "scheduled" || mode === "continuous") && (
        <label className="field"><span>Schedule (5-field cron, UTC){mode === "continuous" ? " · optional self-check" : ""}</span>
          <input className="input mono" value={cron} onChange={(e) => onChange({ schedule_cron: e.target.value })} placeholder="0 3 * * *" />
        </label>
      )}
      {(mode === "event" || mode === "continuous") && (
        <div className="field">
          <span>Woken by these events in its workspaces (and by tasks from other agents)</span>
          <div className="chips">
            {WAKE_TYPES.map(([t, l]) => <button key={t} type="button" className="chip" aria-pressed={w.includes(t)} onClick={() => onChange({ wake_on: w.includes(t) ? w.filter((x) => x !== t) : [...w, t] })} title={t}>{l}</button>)}
          </div>
          <small>不选任何事件：只在其他 Agent 或你交任务时被唤醒。一个事件匹配多个 Agent 时，只唤醒最相关的一个，由它再交接。</small>
        </div>
      )}
    </div>
  );
}

function GiveTask({ a, onClose }: { a: AgentDetailAt; onClose: () => void }) {
  const meta = useAtMeta();
  const [f, setF] = useState({ objective: "", kind: "general", workspace_id: a.workspaces[0]?.id || "", priority: 2, deadline_minutes: "" });
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const t = await post<AtTask & { deduplicated: boolean }>(`/api/at/agents/${a.id}/tasks`, { ...f, deadline_minutes: f.deadline_minutes ? Number(f.deadline_minutes) : undefined });
      toast(t.deduplicated ? "同样的任务已经在进行，没有重复创建" : `任务 ${t.id} 已排队`);
      refreshAt();
      onClose();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={`Give ${a.name} a task`} onClose={onClose} footer={<><div className="grow" /><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!f.objective.trim() || !f.workspace_id || busy} onClick={submit}>Queue task</button></>}>
      <label className="field"><span>Objective</span><textarea className="input" rows={4} autoFocus value={f.objective} onChange={(e) => setF({ ...f, objective: e.target.value })} placeholder="要做什么、做到什么程度" /></label>
      <div className="grid2">
        <label className="field"><span>Workspace</span><select className="input" value={f.workspace_id} onChange={(e) => setF({ ...f, workspace_id: e.target.value })}>{a.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        <label className="field"><span>Kind</span><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(meta.data?.task_kinds || {}).map(([k, v]) => <option key={k} value={k}>{k} · {v}</option>)}</select></label>
        <label className="field"><span>Priority</span><select className="input" value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })}>{[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}</select></label>
        <label className="field"><span>Deadline (minutes, optional)</span><input className="input mono" value={f.deadline_minutes} onChange={(e) => setF({ ...f, deadline_minutes: e.target.value.replace(/\D/g, "") })} /></label>
      </div>
      <small className="muted">任务进入持久队列，云端 Worker 会唤醒这个 Agent 执行；关掉浏览器也会继续。</small>
    </Dialog>
  );
}
