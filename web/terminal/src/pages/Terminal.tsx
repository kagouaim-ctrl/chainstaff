import { useQuery } from "@tanstack/react-query";
import { Activity, Bot, ChevronRight, Layers, Radio, Search, ShieldCheck, Star, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityStream } from "../components/ActivityStream";
import { AgentForm } from "../components/AgentForm";
import { AgentInspector } from "../components/AgentInspector";
import { AgentRow, AgentTable } from "../components/AgentTable";
import { QuickActionBar, QuickActions } from "../components/QuickActionBar";
import type { RunMode } from "../components/RunPanel";
import { NewMissionDialog } from "../components/TaskComposer";
import { EmptyState, ErrorState, Loading, Progress, Seg, Sheet } from "../components/ui";
import { CATEGORIES, categoriesOf, inView, type Category, type View } from "../lib/agents";
import { api } from "../lib/api";
import { useAgentName, useAgents, useApprovals, useMissions, useOverview, useSkills, useSquads } from "../lib/queries";
import { useLiveMessages, useStreamState } from "../lib/stream";
import type { Agent, Message } from "../lib/types";
import { DEMO } from "../lib/demo";
import { ago, go, setParams, usd, useIsMobile, usePref, type Route } from "../lib/util";

const VIEWS: [View, string, typeof Bot][] = [
  ["all", "All agents", Layers],
  ["mine", "My agents", Bot],
  ["active", "Active", Radio],
  ["idle", "Idle", Radio],
  ["running", "Running", Zap],
  ["favorites", "Favorites", Star],
];

/** Default home: every AI employee, filterable on the left, actionable in the table, inspectable on the right. */
export default function Terminal({ route }: { route: Route }) {
  const agents = useAgents();
  const skills = useSkills();
  const mobile = useIsMobile();
  const [favs, setFavs] = usePref<string[]>("favorites", []);
  const [detailed, setDetailed] = usePref<boolean>("terminal-detailed", false);
  const view = (route.params.get("view") as View) || "mine";
  const cat = (route.params.get("cat") as Category) || null;
  const [q, setQ] = useState("");
  const [action, setAction] = useState<RunMode | null>(null);
  const [form, setForm] = useState<Agent | "new" | null>(null);
  const [newMission, setNewMission] = useState(false);
  const selected = route.id;
  useEffect(() => setAction(null), [selected]);

  const toggleFav = useCallback((id: string) => setFavs((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id])), [setFavs]);
  const select = useCallback((a: Agent) => go("terminal", a.id, Object.fromEntries(route.params)), [route.params]);
  const act = useCallback((a: Agent, m: RunMode) => (go("terminal", a.id, Object.fromEntries(route.params)), setTimeout(() => setAction(m), 0)), [route.params]);
  const configure = useCallback((a: Agent) => setForm(a), []);

  const all = agents.data || [];
  const cats = useMemo(() => new Map(all.map((a) => [a.id, categoriesOf(a, skills.data)])), [all, skills.data]);
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((a) => inView(a, view, favs) && (!cat || cats.get(a.id)?.has(cat)) && (!s || `${a.name} ${a.role} ${a.id} ${a.skills.join(" ")} ${a.current_task?.title || ""}`.toLowerCase().includes(s)));
  }, [all, view, cat, q, favs, cats]);
  const count = (v: View) => all.filter((a) => inView(a, v, favs)).length;
  const catCount = (c: Category) => all.filter((a) => a.status !== "archived" && cats.get(a.id)?.has(c)).length;
  const setView = (v: string) => setParams(route, { view: v === "mine" ? null : v, cat: null }, selected);
  const setCat = (c: Category | null) => setParams(route, { cat: c }, selected);
  const clearFilters = () => (setQ(""), setParams(route, { view: "all", cat: null }, selected));
  const live = all.filter((a) => a.status === "active");
  const allIdle = live.length > 0 && live.every((a) => a.live === "idle" || a.live === "error");

  if (agents.isLoading) return <div className="page"><Loading rows={10} /></div>;
  if (agents.error) return <div className="page"><ErrorState error={agents.error} retry={() => agents.refetch()} /></div>;

  const dialogs = (
    <>
      {form && <AgentForm agent={form === "new" ? undefined : form} onClose={() => setForm(null)} onSaved={(a) => form === "new" && go("terminal", a.id)} />}
      {newMission && <NewMissionDialog onClose={() => setNewMission(false)} />}
    </>
  );

  if (mobile)
    return (
      <MobileTerminal
        rows={rows} all={all} view={view} cat={cat} setView={setView} setCat={setCat} q={q} setQ={setQ} favs={favs} toggleFav={toggleFav}
        selected={selected} action={action} setAction={setAction} onConfigure={configure} onNewMission={() => setNewMission(true)} onNewAgent={() => setForm("new")} route={route} dialogs={dialogs}
      />
    );

  return (
    <div className="term">
      <aside className="pane rail" aria-label="Agent categories">
        <div className="rail-group">
          <span className="label">Workforce</span>
          {VIEWS.map(([v, label, Icon]) => (
            <button key={v} className="rail-item" aria-current={view === v && !cat} onClick={() => setView(v)}>
              <Icon size={13} /> {label} <span className="n">{count(v)}</span>
            </button>
          ))}
          {count("attention") > 0 && (
            <button className="rail-item" aria-current={view === "attention" && !cat} onClick={() => setView("attention")}>
              <Activity size={13} className="bad" /> <span className="bad">Needs attention</span> <span className="n bad">{count("attention")}</span>
            </button>
          )}
        </div>
        <div className="rail-group">
          <span className="label">By function</span>
          {CATEGORIES.map((c) => (
            <button key={c} className="rail-item" aria-current={cat === c} onClick={() => setCat(cat === c ? null : c)}>
              {c} <span className="n">{catCount(c)}</span>
            </button>
          ))}
        </div>
        <div className="rail-group">
          <span className="label">Squads</span>
          <SquadLinks />
        </div>
      </aside>

      <section className="pane center" aria-label="Agents">
        <div className="toolbar">
          <h1>{cat || VIEWS.find(([v]) => v === view)?.[1] || "Needs attention"}</h1>
          <span className="count">{rows.length}</span>
          <div className="grow" />
          <QuickActions onNewMission={() => setNewMission(true)} onNewAgent={() => setForm("new")} />
        </div>
        <QuickActionBar onView={setView} />
        <div className="filterbar">
          <div className="search" style={{ width: 300 }}>
            <Search size={14} />
            <input className="input" placeholder="搜索名称、角色、技能或当前任务" value={q} onChange={(e) => setQ(e.target.value)} aria-label="筛选员工" />
          </div>
          <div className="grow" />
          <LiveTag />
          <Seg value={detailed ? "detailed" : "compact"} onChange={(v) => setDetailed(v === "detailed")} options={[["compact", "Compact"], ["detailed", "Detailed"]]} />
        </div>
        {!all.length ? (
          <EmptyState title="Create your first agent" icon={<Bot size={18} />}>
            <span>先建一个 AI Manager，再建几个带技能的员工（钱包分析、合约分析、数据报告），然后把它们组成一个 Squad。</span>
            <button className="btn primary" onClick={() => setForm("new")}>Create agent</button>
          </EmptyState>
        ) : (
          <>
            {allIdle && <div className="banner"><span className="ind ok" /> All agents idle · 没有执行中的任务<div className="grow" /><button className="btn sm primary" onClick={() => setNewMission(true)}>Create mission</button></div>}
            <AgentTable rows={rows} selected={selected} favorites={favs} onFavorite={toggleFav} onSelect={select} onAction={act} onConfigure={configure} detailed={detailed}
              empty={<EmptyState title="No matching agents" icon={<Search size={18} />}><span>当前筛选没有结果，换个关键词或清除筛选。</span><button className="btn sm" onClick={clearFilters}>Clear filters</button></EmptyState>} />
          </>
        )}
      </section>

      <aside className="pane insp" aria-label="Agent inspector">
        {selected ? (
          <AgentInspector
            id={selected}
            onClose={() => go("terminal", undefined, Object.fromEntries(route.params))}
            onConfigure={configure}
            fav={favs.includes(selected)}
            onFav={() => toggleFav(selected)}
            action={action}
            onAction={setAction}
          />
        ) : (
          <LivePanel />
        )}
      </aside>
      {dialogs}
    </div>
  );
}

function LiveTag() {
  const s = useStreamState();
  if (DEMO) return <span className="row gap4 xs muted" title="静态演示，没有实时连接">Snapshot</span>;
  return (
    <span className="row gap4 xs muted" title="Agent 状态来自服务器实时事件流">
      <span className={`ind ${s === "live" ? "ok" : "warn live"}`} /> {s === "live" ? "Live" : "Reconnecting"}
    </span>
  );
}

function SquadLinks() {
  const squads = useSquads();
  return (
    <>
      {(squads.data || []).map((q) => (
        <button key={q.id} className="rail-item" onClick={() => go("board", q.workspace_id, { s: q.id })}>
          <span className={`ind ${q.status === "paused" ? "" : q.ready ? "ok" : "bad"}`} />
          <span className="ellipsis">{q.name}</span>
          {q.missions.active > 0 ? <span className="n acc">{q.missions.active} live</span> : <span className="n">{q.members.length + (q.manager_id ? 1 : 0)}</span>}
        </button>
      ))}
      {!squads.data?.length && <button className="rail-item" onClick={() => go("squads")}><span className="muted">+ Create squad</span></button>}
    </>
  );
}

/**
 * Live Operations, with no agent selected: what needs a human first, then what is running, then
 * plain activity. With nothing wrong it collapses to one line instead of empty boxes.
 */
function LivePanel() {
  const approvals = useApprovals("pending");
  const missions = useMissions({ status: "pending,running,waiting", limit: 20 });
  const squads = useSquads();
  const failed = useMissions({ status: "failed", limit: 5 });
  const name = useAgentName();
  const base = useQuery({ queryKey: ["activity", "terminal"], queryFn: () => api<Message[]>("/api/wf/activity?kinds=agent,task,chain,system,user&limit=40"), staleTime: Infinity });
  const [live, setLive] = useState<Message[]>([]);
  useLiveMessages(useCallback((m: Message) => m.kind !== "tool" && setLive((p) => [m, ...p].slice(0, 60)), []));
  const rows = useMemo(() => {
    const seen = new Set<number>();
    return [...live, ...(base.data || [])].filter((m) => !seen.has(m.id) && (seen.add(m.id), true)).slice(0, 60);
  }, [live, base.data]);
  const dayAgo = Date.now() - 86_400_000;
  const recentFails = (failed.data || []).filter((m) => +new Date(m.finished_at || m.updated_at) > dayAgo);
  const broken = (squads.data || []).filter((q) => q.status === "active" && !q.ready);
  const attention = (approvals.data?.length || 0) + recentFails.length + broken.length;
  return (
    <>
      <div className="pane-h title"><span className="label">Live operations</span><div className="grow" /><LiveTag /></div>
      <div className="pane-h sub-h"><span className="label">Needs attention</span>{attention > 0 && <span className="badge acc">{attention}</span>}</div>
      <div style={{ flex: "none", maxHeight: "38%", overflow: "auto" }}>
        {(approvals.data || []).map((a) => (
          <button key={a.id} className="m-row" onClick={() => a.task_id && go("board", a.workspace_id, { m: a.task_id })}>
            <ShieldCheck size={14} className="acc" style={{ flex: "none" }} />
            <span className="col gap2 grow"><span className="ellipsis sm">{a.title}</span><span className="muted xs">{a.kind === "over_budget" ? "预算用完" : "等你审批"} · {name(a.requested_by)} · {ago(a.at)}</span></span>
            <ChevronRight size={14} className="muted" />
          </button>
        ))}
        {recentFails.map((m) => (
          <button key={m.id} className="m-row" onClick={() => go("board", m.workspace_id, { m: m.id })}>
            <Activity size={14} className="bad" style={{ flex: "none" }} />
            <span className="col gap2 grow"><span className="ellipsis sm">{m.name}</span><span className="bad xs ellipsis">{m.error || "failed"}</span></span>
            <ChevronRight size={14} className="muted" />
          </button>
        ))}
        {broken.map((q) => (
          <button key={q.id} className="m-row" onClick={() => go("squads", q.id)}>
            <Activity size={14} className="warn" style={{ flex: "none" }} />
            <span className="col gap2 grow"><span className="ellipsis sm">Squad {q.name} 不能接 Mission</span><span className="muted xs ellipsis">{q.issues[0]?.text}</span></span>
            <ChevronRight size={14} className="muted" />
          </button>
        ))}
        {!attention && <div className="all-clear"><span className="ind ok" /> 没有需要你处理的事项</div>}
      </div>
      <div className="pane-h sub-h"><span className="label">Running missions</span><span className="mono muted xs">{missions.data?.length || 0}</span></div>
      <div style={{ flex: "none", maxHeight: "28%", overflow: "auto" }}>
        {(missions.data || []).map((m) => (
          <button key={m.id} className="m-row" onClick={() => go("board", m.workspace_id, { m: m.id })}>
            <span className="col gap4 grow">
              <span className="ellipsis sm strong">{m.name}</span>
              <span className="row gap6 xs muted"><span>{m.stage}</span><span>·</span><span>{name(m.owner)}</span></span>
            </span>
            <span style={{ width: 96 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></span>
          </button>
        ))}
        {!missions.data?.length && <div className="all-clear"><span className="ind" /> 没有执行中的 Mission</div>}
      </div>
      <div className="pane-h sub-h"><span className="label">Recent activity</span><div className="grow" /><a className="btn ghost xs" href="#/activity">View all</a></div>
      <div className="scroll" style={{ padding: "0 20px 16px" }}>
        <ActivityStream rows={rows} name={name} compact />
      </div>
    </>
  );
}

function MobileTerminal(p: {
  rows: Agent[]; all: Agent[]; view: View; cat: Category | null; setView: (v: string) => void; setCat: (c: Category | null) => void; q: string; setQ: (s: string) => void;
  favs: string[]; toggleFav: (id: string) => void; selected?: string; action: RunMode | null; setAction: (m: RunMode | null) => void; onConfigure: (a: Agent) => void;
  onNewMission: () => void; onNewAgent: () => void; route: Route; dialogs: React.ReactNode;
}) {
  const approvals = useApprovals("pending");
  const busy = p.all.filter((a) => a.live === "busy").length;
  const running = useMissions({ status: "pending,running,waiting", limit: 200 });
  const ov = useOverview("24h");
  const close = () => go("terminal", undefined, Object.fromEntries(p.route.params));
  const open = (a: Agent, m: RunMode | null = null) => (go("terminal", a.id, Object.fromEntries(p.route.params)), setTimeout(() => p.setAction(m), 0));
  return (
    <div className="page">
      <div className="strip">
        <button className="cell" onClick={() => p.setView("running")}><span className="label">Active</span><span className={`v ${busy ? "acc" : ""}`}>{busy}<span className="muted">/{p.all.filter((a) => a.status === "active").length}</span></span></button>
        <button className="cell" onClick={() => go("tasks", undefined, { f: "running", kind: "goal" })}><span className="label">Missions</span><span className="v">{running.data?.length ?? "—"}</span></button>
        <button className="cell" onClick={() => go("tasks", undefined, { f: "review", kind: "goal" })}><span className="label">Approvals</span><span className={`v ${approvals.data?.length ? "acc" : ""}`}>{approvals.data?.length || 0}</span></button>
        <button className="cell" onClick={() => go("tasks", undefined, { kind: "goal" })}><span className="label">Cost 24h</span><span className="v">{ov.data ? usd(ov.data.cost_usd) : "—"}</span></button>
      </div>
      <div className="row gap6" style={{ padding: "8px 16px", overflowX: "auto", flex: "none" }}>
        {VIEWS.filter(([v]) => v !== "all").map(([v, label]) => (
          <button key={v} className="chip" aria-pressed={p.view === v && !p.cat} onClick={() => p.setView(v)}>{label}</button>
        ))}
        {CATEGORIES.map((c) => <button key={c} className="chip" aria-pressed={p.cat === c} onClick={() => p.setCat(p.cat === c ? null : c)}>{c}</button>)}
      </div>
      <div style={{ padding: "4px 16px 12px", flex: "none" }} className="row gap8">
        <div className="search grow"><Search size={13} /><input className="input" placeholder="搜索员工" value={p.q} onChange={(e) => p.setQ(e.target.value)} /></div>
        <button className="btn primary" onClick={p.onNewMission}>Mission</button>
      </div>
      <div className="m-list">
        {p.rows.map((a) => <AgentRow key={a.id} agent={a} onOpen={open} />)}
        {!p.all.length && <EmptyState title="Create your first agent"><button className="btn primary" onClick={p.onNewAgent}>Create agent</button></EmptyState>}
        {!!p.all.length && !p.rows.length && <EmptyState title="No matching agents"><button className="btn" onClick={() => (p.setQ(""), p.setView("all"), p.setCat(null))}>Clear filters</button></EmptyState>}
      </div>
      {p.selected && (
        <Sheet onClose={close} tall>
          <AgentInspector id={p.selected} onClose={close} onConfigure={p.onConfigure} fav={p.favs.includes(p.selected)} onFav={() => p.toggleFav(p.selected!)} action={p.action} onAction={p.setAction} />
        </Sheet>
      )}
      {p.dialogs}
    </div>
  );
}
