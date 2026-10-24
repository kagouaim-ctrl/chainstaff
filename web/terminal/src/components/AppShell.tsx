import {
  Activity, Bell, Blocks, Bot, ChevronsLeft, ChevronsRight, LayoutDashboard, ListChecks, MessagesSquare, MoreHorizontal, Puzzle, Search, Settings, Wallet,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useAgents, useApprovals, useChain, useOverview, useWorkspaces } from "../lib/queries";
import { useStreamState } from "../lib/stream";
import { go, num, short, usePref, type Route } from "../lib/util";
import { WalletButton } from "./WalletStatus";

export const NAV: [string, string, typeof LayoutDashboard][] = [
  ["overview", "Overview", LayoutDashboard],
  ["workforce", "Workforce", Bot],
  ["workspace", "Workspace", MessagesSquare],
  ["tasks", "Tasks", ListChecks],
  ["onchain", "On-Chain", Blocks],
  ["skills", "Skills", Puzzle],
  ["activity", "Activity", Activity],
  ["settings", "Settings", Settings],
];
const MOBILE: [string, string, typeof LayoutDashboard][] = [
  ["overview", "Home", LayoutDashboard],
  ["workforce", "Agents", Bot],
  ["workspace", "Workspace", MessagesSquare],
  ["tasks", "Tasks", ListChecks],
  ["more", "More", MoreHorizontal],
];

export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const [collapsed, setCollapsed] = usePref("sidebar-collapsed", false);
  const approvals = useApprovals("pending");
  const pending = approvals.data?.length || 0;
  const mobilePage = ["onchain", "skills", "activity", "settings"].includes(route.page) ? "more" : route.page;
  return (
    <div className="app">
      <TopBar route={route} pending={pending} />
      <div className={`body ${collapsed ? "collapsed" : ""}`}>
        <nav className="side" aria-label="主导航">
          {NAV.map(([id, label, Icon]) => (
            <a key={id} href={`#/${id}`} aria-current={route.page === id ? "page" : undefined} title={label}>
              <Icon size={16} />
              <span>{label}</span>
              {id === "tasks" && pending > 0 && <em className="count">{pending}</em>}
            </a>
          ))}
          <div className="grow" />
          <a href="/" title="Classic console">
            <Wallet size={16} />
            <span>Classic console</span>
          </a>
          <button className="btn ghost sm" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "展开侧栏" : "收起侧栏"} style={{ alignSelf: collapsed ? "center" : "flex-end" }}>
            {collapsed ? <ChevronsRight size={14} /> : <ChevronsLeft size={14} />}
          </button>
        </nav>
        <main className="main">{children}</main>
      </div>
      <nav className="bottom-nav" aria-label="底部导航">
        {MOBILE.map(([id, label, Icon]) => (
          <a key={id} href={`#/${id}`} aria-current={mobilePage === id ? "page" : undefined}>
            <Icon size={20} />
            {label}
            {id === "tasks" && pending > 0 && <em className="count acc" style={{ fontSize: 9 }}>{pending}</em>}
          </a>
        ))}
      </nav>
    </div>
  );
}

function TopBar({ route, pending }: { route: Route; pending: number }) {
  const ws = useWorkspaces();
  const chain = useChain();
  const ov = useOverview("24h");
  const live = useStreamState();
  const [wsId, setWsId] = usePref<string>("workspace", "");
  const current = ws.data?.find((w) => w.id === wsId) || ws.data?.[0];
  const rpc = chain.data?.rpc;
  return (
    <header className="top">
      <a className="brand" href="#/overview">
        <span className="brand-mark">CS</span>
        <span className="d-only">ChainStaff</span>
      </a>
      {ws.data && ws.data.length > 0 && (
        <select
          className="input d-only"
          style={{ width: 160 }}
          aria-label="工作区"
          value={current?.id || ""}
          onChange={(e) => {
            setWsId(e.target.value);
            if (route.page === "workspace") go("workspace", e.target.value);
          }}
        >
          {ws.data.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      )}
      <GlobalSearch />
      <div className="top-stat d-only" title={rpc?.error || "RPC 状态"}>
        <span className={`badge ${rpc?.ok ? "ok" : chain.data?.configured ? "bad" : ""}`}>
          <span className="dot" />
          {chain.data?.network || "no chain"}
        </span>
        {rpc?.ok && <span className="mono">#{num(rpc.block)}</span>}
        {rpc?.ok && <span className="muted">{rpc.latency_ms}ms</span>}
      </div>
      <div className="top-stat d-only" title="实时连接">
        <span className={`badge ${live === "live" ? "ok" : "warn"} ${live !== "live" ? "pulse" : ""}`}>
          <span className="dot" />
          {live === "live" ? "Live" : live === "retrying" ? "Reconnecting" : "Connecting"}
        </span>
      </div>
      <div className="top-stat d-only" title="员工状态">
        <Bot size={14} />
        <span className="mono acc">{ov.data?.agents.busy ?? "—"}</span>
        <span className="muted">busy</span>
        <span className="mono">{ov.data?.agents.idle ?? "—"}</span>
        <span className="muted">idle</span>
      </div>
      <div className="grow m-only" />
      <a className="btn ghost icon rel" href="#/tasks?view=approvals" aria-label={`${pending} 个待审批`} title="待审批">
        <Bell size={16} />
        {pending > 0 && <span style={{ position: "absolute", top: 2, right: 2, background: "var(--accent)", color: "var(--accent-fg)", borderRadius: 7, font: "600 9px var(--mono)", padding: "0 4px" }}>{pending}</span>}
      </a>
      <WalletButton />
    </header>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const agents = useAgents();
  const ws = useWorkspaces();
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    const out: { kind: string; label: string; sub: string; go: () => void }[] = [];
    agents.data?.forEach((a) => (a.name + a.role + a.id).toLowerCase().includes(s) && out.push({ kind: "Agent", label: a.name, sub: a.role || a.id, go: () => go("workforce", a.id) }));
    ws.data?.forEach((w) => {
      if (w.name.toLowerCase().includes(s)) out.push({ kind: "Workspace", label: w.name, sub: w.id, go: () => go("workspace", w.id) });
      w.goals?.forEach((g) => (g.title + g.id).toLowerCase().includes(s) && out.push({ kind: "Task", label: g.title, sub: g.id, go: () => go("tasks", g.id) }));
    });
    if (/^0x[0-9a-f]{64}$/i.test(s)) out.push({ kind: "Tx", label: short(s), sub: "查看链上记录", go: () => go("onchain", undefined, { q: s }) });
    if (/^tsk_/.test(s)) out.push({ kind: "Task", label: s, sub: "打开任务", go: () => go("tasks", s) });
    return out.slice(0, 12);
  }, [q, agents.data, ws.data]);
  return (
    <div className="top-search">
      <Search size={14} />
      <input
        className="input"
        placeholder="搜索员工、任务、工作区、交易哈希"
        value={q}
        onChange={(e) => (setQ(e.target.value), setOpen(true))}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results[0]) {
            results[0].go();
            setQ("");
          }
        }}
        aria-label="全局搜索"
      />
      {open && results.length > 0 && (
        <div className="search-pop" role="listbox">
          {results.map((r, i) => (
            <button key={i} role="option" aria-selected={i === 0} onMouseDown={() => (r.go(), setQ(""))}>
              <span className="tag">{r.kind}</span>
              <span className="ellipsis">{r.label}</span>
              <span className="muted mono ellipsis" style={{ marginLeft: "auto" }}>
                {r.sub}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
