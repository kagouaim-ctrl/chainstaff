import { useQuery } from "@tanstack/react-query";
import { Activity, Bot, ChevronRight, Layers, Radio, Search, ShieldCheck, Star, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityStream } from "../components/ActivityStream";
import { AgentForm } from "../components/AgentForm";
import { AgentInspector } from "../components/AgentInspector";
import { AgentRow, AgentTable } from "../components/AgentTable";
import { QuickActionBar } from "../components/QuickActionBar";
import type { RunMode } from "../components/RunPanel";
import { NewMissionDialog } from "../components/TaskComposer";
import { EmptyState, ErrorState, Loading, Progress, Seg, Sheet } from "../components/ui";
import { CATEGORIES, categoriesOf, inView, type Category, type View } from "../lib/agents";
import { api } from "../lib/api";
import { useAgentName, useAgents, useApprovals, useMissions, useSkills } from "../lib/queries";
import { useLiveMessages, useStreamState } from "../lib/stream";
import type { Agent, Message } from "../lib/types";
import { ago, go, setParams, useIsMobile, usePref, type Route } from "../lib/util";

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
        selected={selected} action={action} setAction={setAction} onConfigure={configure} onNewMission={() => setNewMission(true)} route={route} dialogs={dialogs}
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
        <QuickActionBar onNewMission={() => setNewMission(true)} onNewAgent={() => setForm("new")} onView={setView} />
        <div className="toolbar">
          <h1>{cat || VIEWS.find(([v]) => v === view)?.[1] || "Needs attention"}</h1>
          <span className="mono muted xs">{rows.length}</span>
          <div className="search" style={{ width: 280, marginLeft: 8 }}>
            <Search size={13} />
            <input className="input" placeholder="名称、角色、技能、当前任务" value={q} onChange={(e) => setQ(e.target.value)} aria-label="筛选员工" />
          </div>
          <div className="grow" />
          <LiveTag />
          <Seg value={detailed ? "detailed" : "compact"} onChange={(v) => setDetailed(v === "detailed")} options={[["compact", "Compact"], ["detailed", "Detailed"]]} />
        </div>
        {!all.length ? (
          <EmptyState title="还没有 AI 员工">
            <span>先建一个 AI Manager，再建几个带技能的员工（钱包分析、合约分析、数据报告）。</span>
            <button className="btn primary" onClick={() => setForm("new")}>创建第一个员工</button>
          </EmptyState>
        ) : (
          <AgentTable rows={rows} selected={selected} favorites={favs} onFavorite={toggleFav} onSelect={select} onAction={act} onConfigure={configure} detailed={detailed} />
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
  return (
    <span className="row gap4 xs muted" title="Agent 状态来自服务器实时事件流">
      <span className={`ind ${s === "live" ? "ok" : "warn live"}`} /> {s === "live" ? "Live" : "Reconnecting"}
    </span>
  );
}

function SquadLinks() {
  const ws = useMissions({ status: "pending,running,waiting,review", limit: 200 });
  const spaces = useQuery({ queryKey: ["workspaces"], queryFn: () => api<{ id: string; name: string; archived: boolean }[]>("/api/wf/workspaces") });
  return (
    <>
      {(spaces.data || []).filter((w) => !w.archived).map((w) => {
        const live = ws.data?.filter((m) => m.workspace_id === w.id).length || 0;
        return (
          <button key={w.id} className="rail-item" onClick={() => go("workspace", w.id)}>
            <span className="ellipsis">{w.name}</span>
            {live > 0 && <span className="n acc">{live} live</span>}
          </button>
        );
      })}
      {!spaces.data?.length && <span className="muted xs" style={{ padding: "0 14px" }}>还没有工作区</span>}
    </>
  );
}

/** With no agent selected, the right column shows what needs a human and what is moving. */
function LivePanel() {
  const approvals = useApprovals("pending");
  const missions = useMissions({ status: "pending,running,waiting", limit: 20 });
  const name = useAgentName();
  const base = useQuery({ queryKey: ["activity", "terminal"], queryFn: () => api<Message[]>("/api/wf/activity?kinds=agent,task,chain,system,user&limit=40"), staleTime: Infinity });
  const [live, setLive] = useState<Message[]>([]);
  useLiveMessages(useCallback((m: Message) => m.kind !== "tool" && setLive((p) => [m, ...p].slice(0, 60)), []));
  const rows = useMemo(() => {
    const seen = new Set<number>();
    return [...live, ...(base.data || [])].filter((m) => !seen.has(m.id) && (seen.add(m.id), true)).slice(0, 60);
  }, [live, base.data]);
  return (
    <>
      <div className="pane-h"><span className="label">Needs you</span><span className="mono acc xs">{approvals.data?.length || 0}</span></div>
      <div style={{ flex: "none", maxHeight: "34%", overflow: "auto" }}>
        {(approvals.data || []).map((a) => (
          <button key={a.id} className="m-row" onClick={() => a.task_id && go("workspace", a.workspace_id, { m: a.task_id })}>
            <ShieldCheck size={14} className="acc" style={{ flex: "none" }} />
            <span className="col gap2 grow">
              <span className="ellipsis sm">{a.title}</span>
              <span className="muted xs">{name(a.requested_by)} · {ago(a.at)}</span>
            </span>
            <ChevronRight size={14} className="muted" />
          </button>
        ))}
        {!approvals.data?.length && <div className="muted sm" style={{ padding: "10px 14px" }}>没有等你审批的结果。</div>}
      </div>
      <div className="pane-h" style={{ borderTop: "1px solid var(--line)" }}><span className="label">Live missions</span><span className="mono muted xs">{missions.data?.length || 0}</span></div>
      <div style={{ flex: "none", maxHeight: "28%", overflow: "auto" }}>
        {(missions.data || []).map((m) => (
          <button key={m.id} className="m-row" onClick={() => go("workspace", m.workspace_id, { m: m.id })}>
            <span className="col gap4 grow">
              <span className="ellipsis sm strong">{m.name}</span>
              <span className="row gap6 xs muted"><span>{m.stage}</span><span>·</span><span>{name(m.owner)}</span></span>
            </span>
            <span style={{ width: 90 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></span>
          </button>
        ))}
        {!missions.data?.length && <div className="muted sm" style={{ padding: "10px 14px" }}>现在没有执行中的 Mission。</div>}
      </div>
      <div className="pane-h" style={{ borderTop: "1px solid var(--line)" }}><span className="label">Activity</span><div className="grow" /><a className="btn ghost xs" href="#/activity">All</a></div>
      <div className="scroll" style={{ padding: "0 14px" }}>
        <ActivityStream rows={rows} name={name} compact />
      </div>
    </>
  );
}

function MobileTerminal(p: {
  rows: Agent[]; all: Agent[]; view: View; cat: Category | null; setView: (v: string) => void; setCat: (c: Category | null) => void; q: string; setQ: (s: string) => void;
  favs: string[]; toggleFav: (id: string) => void; selected?: string; action: RunMode | null; setAction: (m: RunMode | null) => void; onConfigure: (a: Agent) => void;
  onNewMission: () => void; route: Route; dialogs: React.ReactNode;
}) {
  const approvals = useApprovals("pending");
  const busy = p.all.filter((a) => a.live === "busy").length;
  const idle = p.all.filter((a) => a.live === "idle").length;
  const err = p.all.filter((a) => a.live === "error").length;
  const close = () => go("terminal", undefined, Object.fromEntries(p.route.params));
  const open = (a: Agent, m: RunMode | null = null) => (go("terminal", a.id, Object.fromEntries(p.route.params)), setTimeout(() => p.setAction(m), 0));
  return (
    <div className="page">
      <div className="strip">
        <button className="cell" onClick={() => p.setView("running")}><span className="label">Running</span><span className={`v ${busy ? "acc" : ""}`}>{busy}</span></button>
        <button className="cell" onClick={() => p.setView("idle")}><span className="label">Idle</span><span className="v">{idle}</span></button>
        <button className="cell" onClick={() => p.setView("attention")}><span className="label">Error</span><span className={`v ${err ? "bad" : ""}`}>{err}</span></button>
        <button className="cell" onClick={() => go("tasks", undefined, { f: "review", kind: "goal" })}><span className="label">Review</span><span className={`v ${approvals.data?.length ? "acc" : ""}`}>{approvals.data?.length || 0}</span></button>
      </div>
      <div className="row gap6" style={{ padding: "8px 12px", overflowX: "auto", flex: "none" }}>
        {VIEWS.filter(([v]) => v !== "all").map(([v, label]) => (
          <button key={v} className="chip" aria-pressed={p.view === v && !p.cat} onClick={() => p.setView(v)}>{label}</button>
        ))}
        {CATEGORIES.map((c) => <button key={c} className="chip" aria-pressed={p.cat === c} onClick={() => p.setCat(p.cat === c ? null : c)}>{c}</button>)}
      </div>
      <div style={{ padding: "0 12px 8px", flex: "none" }} className="row gap6">
        <div className="search grow"><Search size={13} /><input className="input" placeholder="搜索员工" value={p.q} onChange={(e) => p.setQ(e.target.value)} /></div>
        <button className="btn primary" onClick={p.onNewMission}>Mission</button>
      </div>
      <div className="m-list" style={{ borderTop: "1px solid var(--line)" }}>
        {p.rows.map((a) => <AgentRow key={a.id} agent={a} onOpen={open} />)}
        {!p.rows.length && <EmptyState title="没有符合条件的员工" />}
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
