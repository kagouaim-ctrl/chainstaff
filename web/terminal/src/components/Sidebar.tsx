import { Bot, ChevronDown, Link2, MessageSquare, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings, SquarePen, Target } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { isRunning, isWorking, STATE, useAtAgents, useAtMissions, type AtAgentRow } from "../lib/at";
import { useChats } from "../lib/chat";
import { DEMO } from "../lib/demo";
import { openCreateWorker, setSideOpen } from "../lib/shell";
import { go, usePref, type Route } from "../lib/util";
import { navCurrent } from "./TerminalHeader";
import { WorkerAvatar } from "./WorkerAvatar";

const SHOWN = 6; // chats / agents listed before "Show all"
const rank = (a: AtAgentRow) => (isWorking(a) || a.state === "waiting_approval" ? 0 : a.state === "watching" ? 1 : a.state === "error" || a.state === "offline" ? 2 : 3);

export const NAV: [string, string, typeof Bot][] = [
  ["chat", "Chat", MessageSquare],
  ["agents", "Agents", Bot],
  ["missions", "Missions", Target],
  ["onchain", "On-Chain", Link2],
  ["more", "More", MoreHorizontal],
];

/**
 * The left sidebar: start a chat, the four places you go, your recent chats and your agents. Clicking an agent
 * opens a conversation with it. Collapses to an icon rail on desktop; on phones it is a drawer.
 */
export function Sidebar({ route, collapsed, onCollapse }: { route: Route; collapsed: boolean; onCollapse: (v: boolean) => void }) {
  const missions = useAtMissions({ status: "active,paused" });
  const running = (missions.data || []).filter(isRunning).length;
  const current = navCurrent(route.page);
  const nav = (fn: () => void) => () => (fn(), setSideOpen(false));
  return (
    <aside className={`side ${collapsed ? "collapsed" : ""}`} aria-label="Sidebar">
      <div className="side-scroll">
        <button className="side-new" onClick={nav(() => go("chat"))} title="新对话">
          <SquarePen size={16} />
          {!collapsed && <span>新对话</span>}
        </button>
        <nav className="side-items" aria-label="主导航">
          {NAV.map(([id, label, Icon]) => (
            <Item key={id} icon={<Icon size={17} />} label={label} collapsed={collapsed} current={current === id && !(id === "chat" && route.id)}
              n={id === "missions" ? running : undefined} onClick={nav(() => go(id))} />
          ))}
        </nav>
        {!collapsed && <RecentChats route={route} />}
        <AgentList route={route} collapsed={collapsed} />
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

function RecentChats({ route }: { route: Route }) {
  const q = useChats();
  const [all, setAll] = useState(false);
  const [s, setS] = useState("");
  const [closed, setClosed] = usePref<boolean>("side-chats-closed", false);
  const rows = (q.data || []).filter((c) => !s.trim() || `${c.title} ${c.agent_name || ""}`.toLowerCase().includes(s.trim().toLowerCase()));
  const total = q.data?.length || 0;
  const shown = all || s ? rows : rows.slice(0, SHOWN);
  if (q.error || (!q.isLoading && !total)) return null; // nothing to list yet (or the server has no chats API): the "新对话" button is enough
  return (
    <Group label="Recent" count={total} closed={closed} onToggle={() => setClosed(!closed)}>
      {total > SHOWN && <SideSearch value={s} onChange={setS} placeholder="Search chats" />}
      {shown.map((c) => (
        <button key={c.id} className="side-item chat-item" aria-current={route.page === "chat" && route.id === c.id ? "true" : undefined}
          onClick={() => (go("chat", c.id), setSideOpen(false))} title={c.title}>
          <span className="side-label">{c.title}</span>
          {(c.last_status === "running" || c.last_status === "queued" || c.last_status === "waiting") && <i className="ind info live" aria-label="进行中" />}
          {c.last_status === "waiting_approval" && <i className="ind warn" aria-label="等待确认" />}
        </button>
      ))}
      {s && !rows.length && <div className="side-empty">没有匹配的会话</div>}
      {!s && total > SHOWN && <ShowAll all={all} total={total} onClick={() => setAll(!all)} />}
    </Group>
  );
}

function AgentList({ route, collapsed }: { route: Route; collapsed: boolean }) {
  const agents = useAtAgents();
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const [closed, setClosed] = usePref<boolean>("side-agents-closed", false);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return [...(agents.data || [])]
      .filter((a) => !t || `${a.name} ${a.responsibility}`.toLowerCase().includes(t))
      .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }, [agents.data, q]);
  const total = agents.data?.length || 0;
  const shown = all || q ? list : list.slice(0, SHOWN);
  return (
    <Group label="Agents" count={total} collapsed={collapsed} closed={closed} onToggle={() => setClosed(!closed)}>
      {!collapsed && total > SHOWN && <SideSearch value={q} onChange={setQ} placeholder="Search agents" />}
      {agents.isLoading && !collapsed && <div className="side-sk"><i className="skeleton" /><i className="skeleton" /><i className="skeleton" /></div>}
      {shown.map((a) => {
        const [label, tone, live] = STATE[a.state] || [a.state, ""];
        return (
          <button key={a.id} className="side-item worker" aria-current={route.page === "agents" && route.id === a.id ? "true" : undefined}
            onClick={() => (go("agents", a.id), setSideOpen(false))} title={collapsed ? `${a.name} · ${label}` : `${label}${a.state_detail ? ` · ${a.state_detail}` : ""}`}>
            <span className="side-av">
              <WorkerAvatar id={a.id} appearance={a.appearance} size={collapsed ? 30 : 22} dim={a.status === "paused"} />
              <i className={`side-dot ${tone} ${live ? "live" : ""}`} aria-label={label} />
            </span>
            {!collapsed && <span className="side-label ellipsis">{a.name}</span>}
          </button>
        );
      })}
      {!collapsed && q && !list.length && <div className="side-empty">没有匹配的 Agent</div>}
      {!collapsed && !q && total > SHOWN && <ShowAll all={all} total={total} onClick={() => setAll(!all)} />}
      <Item icon={<Plus size={16} />} label="New Agent" collapsed={collapsed} subtle disabled={DEMO} title={DEMO ? "演示版不能创建" : undefined} onClick={openCreateWorker} />
    </Group>
  );
}

function SideSearch({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="side-search">
      <Search size={13} />
      <input className="input sm" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
    </div>
  );
}

function ShowAll({ all, total, onClick }: { all: boolean; total: number; onClick: () => void }) {
  return (
    <button className="side-item subtle" onClick={onClick}>
      <span className="side-label">{all ? "Show less" : `Show all ${total}`}</span>
    </button>
  );
}

function Group({ label, count, collapsed, closed, onToggle, children }: { label: string; count?: number; collapsed?: boolean; closed?: boolean; onToggle?: () => void; children: ReactNode }) {
  return (
    <section className="side-group">
      {collapsed ? <div className="side-rule" /> : (
        <button className="side-h" onClick={onToggle} aria-expanded={!closed}>
          <span>{label}</span>
          {count !== undefined && <span className="side-n">{count}</span>}
          <ChevronDown size={13} className={`side-chev ${closed ? "closed" : ""}`} />
        </button>
      )}
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
