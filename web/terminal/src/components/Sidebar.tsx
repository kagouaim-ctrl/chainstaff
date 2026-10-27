import { CalendarClock, ChevronDown, History, LayoutGrid, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings, Zap } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { isRunning, isWorking, STATE, useAtAgents, useAtMissions, type AtAgentRow } from "../lib/at";
import { DEMO } from "../lib/demo";
import { openCreateWorker, setSideOpen } from "../lib/shell";
import { go, usePref, type Route } from "../lib/util";
import { WorkerAvatar } from "./WorkerAvatar";

const SHOWN = 6; // workers listed before "Show all"
const rank = (a: AtAgentRow) => (isWorking(a) || a.state === "waiting_approval" ? 0 : a.state === "watching" ? 1 : a.state === "error" || a.state === "offline" ? 2 : 3);

/**
 * The left sidebar: where you are, your workers, your missions, settings. Filters (working, watching, paused…)
 * live on the Workers page, not here. Collapses to an icon rail on desktop; on phones it is a drawer.
 */
export function Sidebar({ route, collapsed, onCollapse }: { route: Route; collapsed: boolean; onCollapse: (v: boolean) => void }) {
  const agents = useAtAgents();
  const missions = useAtMissions({ status: "active,paused" });
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const [closed, setClosed] = usePref<Record<string, boolean>>("side-closed", {});
  const toggle = (k: string) => setClosed((c) => ({ ...c, [k]: !c[k] }));
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return [...(agents.data || [])]
      .filter((a) => !t || `${a.name} ${a.responsibility}`.toLowerCase().includes(t))
      .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }, [agents.data, q]);
  const total = agents.data?.length || 0;
  const shown = all || q ? list : list.slice(0, SHOWN);
  const ms = missions.data || [];
  const running = ms.filter(isRunning).length;
  const scheduled = ms.filter((m) => m.type === "scheduled").length;
  const mstatus = route.page === "missions" && !route.id ? route.params.get("status") : null;
  const mtype = route.page === "missions" && !route.id ? route.params.get("type") : null;
  const nav = (fn: () => void) => () => (fn(), setSideOpen(false));
  return (
    <aside className={`side ${collapsed ? "collapsed" : ""}`} aria-label="Sidebar">
      <div className="side-scroll">
        <Group label="Workspace" collapsed={collapsed}>
          <Item icon={<LayoutGrid size={17} />} label="Overview" current={route.page === "overview"} collapsed={collapsed} onClick={nav(() => go("overview"))} />
        </Group>

        <Group label="My Workers" count={total} collapsed={collapsed} closed={!!closed.workers} onToggle={() => toggle("workers")}>
          {!collapsed && total > SHOWN && (
            <div className="side-search">
              <Search size={13} />
              <input className="input sm" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search workers" aria-label="Search workers" />
            </div>
          )}
          {agents.isLoading && !collapsed && <div className="side-sk"><i className="skeleton" /><i className="skeleton" /><i className="skeleton" /></div>}
          {shown.map((a) => {
            const [label, tone, live] = STATE[a.state] || [a.state, ""];
            return (
              <button key={a.id} className="side-item worker" aria-current={route.page === "agents" && route.id === a.id ? "true" : undefined}
                onClick={nav(() => go("agents", a.id))} title={collapsed ? `${a.name} · ${label}` : a.state_detail}>
                <span className="side-av">
                  <WorkerAvatar id={a.id} appearance={a.appearance} size={collapsed ? 30 : 24} dim={a.status === "paused"} />
                  <i className={`side-dot ${tone} ${live ? "live" : ""}`} aria-label={label} />
                </span>
                {!collapsed && <span className="side-label ellipsis">{a.name}</span>}
              </button>
            );
          })}
          {!collapsed && q && !list.length && <div className="side-empty">No worker matches “{q}”</div>}
          {!collapsed && !q && total > SHOWN && (
            <button className="side-item subtle" onClick={() => (all ? setAll(false) : go("agents"))}>
              <span className="side-ic" />
              <span className="side-label">{all ? "Show less" : `View all ${total}`}</span>
            </button>
          )}
          <Item icon={<Plus size={17} />} label="New Worker" collapsed={collapsed} subtle disabled={DEMO} title={DEMO ? "演示版不能创建" : undefined} onClick={openCreateWorker} />
        </Group>

        <Group label="Missions" collapsed={collapsed} closed={!!closed.missions} onToggle={() => toggle("missions")}>
          <Item icon={<Zap size={17} />} label="Running" n={running} collapsed={collapsed} current={mstatus === "running" && !mtype}
            onClick={nav(() => go("missions", undefined, { status: "running" }))} />
          <Item icon={<CalendarClock size={17} />} label="Scheduled" n={scheduled} collapsed={collapsed} current={mtype === "scheduled"}
            onClick={nav(() => go("missions", undefined, { type: "scheduled", status: "active,paused" }))} />
          <Item icon={<History size={17} />} label="History" collapsed={collapsed} current={mstatus === "completed,failed,stopped"}
            onClick={nav(() => go("missions", undefined, { status: "completed,failed,stopped" }))} />
        </Group>
      </div>
      <div className="side-foot">
        <Item icon={<Settings size={17} />} label="Settings" current={route.page === "settings"} collapsed={collapsed} onClick={nav(() => go("settings"))} />
        <button className="side-collapse d-only" onClick={() => onCollapse(!collapsed)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>
    </aside>
  );
}

function Group({ label, count, collapsed, closed, onToggle, children }: { label: string; count?: number; collapsed: boolean; closed?: boolean; onToggle?: () => void; children: ReactNode }) {
  return (
    <section className="side-group">
      {collapsed ? <div className="side-rule" /> : onToggle ? (
        <button className="side-h" onClick={onToggle} aria-expanded={!closed}>
          <span>{label}</span>
          {count !== undefined && <span className="side-n">{count}</span>}
          <ChevronDown size={13} className={`side-chev ${closed ? "closed" : ""}`} />
        </button>
      ) : <div className="side-h static"><span>{label}</span></div>}
      {(!closed || collapsed) && <div className="side-items">{children}</div>}
    </section>
  );
}

function Item({ icon, label, n, current, collapsed, subtle, disabled, title, onClick }: {
  icon: ReactNode; label: string; n?: number; current?: boolean; collapsed: boolean; subtle?: boolean; disabled?: boolean; title?: string; onClick: () => void;
}) {
  return (
    <button className={`side-item ${subtle ? "subtle" : ""}`} aria-current={current ? "true" : undefined} onClick={onClick} disabled={disabled} title={title || (collapsed ? label : undefined)}>
      <span className="side-ic">{icon}</span>
      {!collapsed && <span className="side-label">{label}</span>}
      {!collapsed && !!n && <span className="side-n">{n}</span>}
    </button>
  );
}
