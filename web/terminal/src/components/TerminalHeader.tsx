import { useQuery } from "@tanstack/react-query";
import {
  Activity, BarChart3, Blocks, Boxes, ChevronDown, Code2, History, LogOut, Menu, Search, Settings, SlidersHorizontal, SquareArrowOutUpRight, Users,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, setToken } from "../lib/api";
import { useAtAgents, useAtMissions } from "../lib/at";
import { DEMO } from "../lib/demo";
import { useChain, useMeta } from "../lib/queries";
import { setSideOpen, toggleOps, useShell } from "../lib/shell";
import { useStreamState } from "../lib/stream";
import { go, num, short, type Route } from "../lib/util";
import { useOpsBadge } from "./LiveOps";

// Five places you go every day. Everything used less often sits under More; nothing there is a placeholder.
export const NAV: [string, string][] = [
  ["overview", "Overview"],
  ["agents", "Workers"],
  ["missions", "Missions"],
  ["onchain", "On-Chain"],
  ["workspace", "Workspace"],
];

/** Every entry under More opens a page that exists and works today. */
export const MORE: { group: string; items: [string, string, string, typeof Boxes, Record<string, string>?][] }[] = [
  { group: "Build", items: [
    ["skills", "Skills", "Web3 skills workers can use", Blocks],
    ["squads", "Squads", "Teams of workers that run missions together", Users],
  ] },
  { group: "Insights", items: [
    ["settings", "Analytics", "Model usage and cost by worker and mission", BarChart3, { s: "billing" }],
    ["activity", "Activity History", "Every wake-up, hand-off and result", History],
  ] },
  { group: "System", items: [
    ["settings", "Settings", "Models, API keys, notifications", Settings],
    ["settings", "Developer Tools", "Tool library and tool permissions", Code2, { s: "tools" }],
    ["settings", "Advanced Configuration", "Runtime, workers, sandbox and limits", SlidersHorizontal, { s: "runtime" }],
  ] },
];
const UNDER_MORE = new Set(["skills", "squads", "activity", "settings", "more", "terminal", "team", "board", "tasks", "log"]);
export const navCurrent = (page: string) => (UNDER_MORE.has(page) ? "more" : page);

export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <rect width="20" height="20" rx="6" fill="#F0B90B" />
      <path d="M5.5 4.5h2.4v4.3l4-4.3h3l-4.6 4.9 4.9 6.1h-3l-3.6-4.6-0.7 0.7v3.9H5.5z" fill="#0B0E11" />
    </svg>
  );
}

export function TerminalHeader({ route }: { route: Route }) {
  const current = navCurrent(route.page);
  return (
    <header className="head">
      <button className="btn ghost icon m-only-flex" onClick={() => setSideOpen(true)} aria-label="Open sidebar"><Menu size={18} /></button>
      <a className="brand" href="#/overview" aria-label="KAGOU">
        <BrandMark size={26} />
        <span>KAGOU</span>
      </a>
      <NavMenu current={current} />
      <nav className="nav" aria-label="主导航">
        {NAV.map(([id, label]) => (
          <a key={id} href={`#/${id}`} aria-current={current === id ? "page" : undefined}>
            {label}
          </a>
        ))}
        <MoreMenu current={current === "more"} />
      </nav>
      <div className="head-right">
        <GlobalSearch />
        <DemoPill />
        <NetworkPill />
        <OpsButton />
        <AccountMenu />
      </div>
    </header>
  );
}

function MoreMenu({ current }: { current: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  return (
    <div className="rel nav-more" ref={ref}>
      <button className="nav-btn" aria-current={current ? "page" : undefined} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}>
        More <ChevronDown size={13} className={`chev ${open ? "up" : ""}`} />
      </button>
      {open && (
        <div className="menu more-menu" role="menu" onClick={() => setOpen(false)}>
          {MORE.map((g) => (
            <div key={g.group} className="more-group">
              <span className="label">{g.group}</span>
              {g.items.map(([page, label, sub, Icon, params]) => (
                <button key={label} role="menuitem" onClick={() => go(page, undefined, params)}>
                  <span className="more-ic"><Icon size={16} /></span>
                  <span className="col gap2"><span className="more-l">{label}</span><span className="more-s">{sub}</span></span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Below 1180px the five links fold into one menu so the header never crowds. */
function NavMenu({ current }: { current: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  const label = NAV.find(([id]) => id === current)?.[1] || "More";
  return (
    <div className="rel nav-fold" ref={ref}>
      <button className="nav-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        {label} <ChevronDown size={13} className={`chev ${open ? "up" : ""}`} />
      </button>
      {open && (
        <div className="menu" style={{ left: 0, right: "auto", top: 44, minWidth: 220 }} onClick={() => setOpen(false)}>
          {NAV.map(([id, l]) => <a key={id} href={`#/${id}`} aria-current={current === id ? "page" : undefined}>{l}</a>)}
          <div className="pop-sep" />
          {MORE.flatMap((g) => g.items).map(([page, l, , Icon, params]) => (
            <button key={l} onClick={() => go(page, undefined, params)}><Icon size={14} /> {l}</button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Opens Live Operations. The badge counts approvals waiting on you and unread alerts. */
function OpsButton() {
  const { total, pending } = useOpsBadge();
  const { ops } = useShell();
  return (
    <button className={`btn ghost ops-btn rel ${ops ? "on" : ""}`} onClick={toggleOps} aria-expanded={!!ops}
      aria-label={`Live operations${total ? `, ${total} new` : ""}`} title={pending ? `${pending} 个审批等你处理` : "Live operations"}>
      <Activity size={16} />
      <span className="hide-l">Activity</span>
      {total > 0 && <span className="count-dot">{total > 99 ? "99+" : total}</span>}
    </button>
  );
}

// Signed in with the read-only demo token: every page works, every change is refused by the server.
function DemoPill() {
  const s = useQuery({ queryKey: ["session"], queryFn: () => api<{ role: string }>("/api/session"), staleTime: Infinity, enabled: !DEMO });
  if (DEMO)
    return (
      <span className="pill demo" title="静态演示：示例数据（脚本化模型、模拟链），不连接服务器，不能修改">
        演示 · 示例数据
      </span>
    );
  if (s.data?.role !== "viewer") return <ModelPill />;
  return (
    <span className="pill demo" title="演示账号：可以查看所有页面，不能修改任何内容">
      演示 · 只读
    </span>
  );
}

// Live mode without a model key: agents cannot reason, and the console says so rather than letting it pass unnoticed.
function ModelPill() {
  const meta = useMeta();
  if (!meta.data || meta.data.engine.llm || meta.data.engine.compat) return null;
  return (
    <button className="pill warn-pill d-only" onClick={() => go("settings")} title="服务器没有配置模型（ANTHROPIC_API_KEY 或 KAGOU_OPENAI_COMPAT_*）：Agent 不调用模型，主管只用规则拆分">
      Model off
    </button>
  );
}

function useOutside(ref: React.RefObject<HTMLElement>, on: boolean, close: () => void) {
  useEffect(() => {
    if (!on) return;
    const f = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", f);
    document.addEventListener("keydown", k);
    return () => (document.removeEventListener("mousedown", f), document.removeEventListener("keydown", k));
  }, [on, close, ref]);
}

function AccountMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  if (DEMO) return null;
  return (
    <div className="rel" ref={ref}>
      <button className="btn ghost icon" aria-label="账户" aria-expanded={open} onClick={() => setOpen(!open)}><ChevronDown size={14} /></button>
      {open && (
        <div className="menu" onClick={() => setOpen(false)}>
          <a href="/"><SquareArrowOutUpRight size={13} /> Classic console</a>
          <button onClick={() => setToken("")}><LogOut size={13} /> Sign out</button>
        </div>
      )}
    </div>
  );
}

function NetworkPill() {
  const chain = useChain();
  const live = useStreamState();
  const rpc = chain.data?.rpc;
  const tone = !chain.data?.configured ? "" : rpc?.ok ? "ok" : "bad";
  return (
    <button className="pill d-only" onClick={() => go("onchain")} title={rpc?.error || (rpc?.ok ? `RPC ${rpc.latency_ms}ms` : "没有配置链（CHAINSTAFF_CHAIN）")}>
      <span className={`ind ${tone}`} />
      <span className="hide-l">{chain.data?.network || "No chain"}</span>
      {rpc?.ok && <span className="num muted hide-xl">#{num(rpc.block)}</span>}
      <span className="sep" />
      {DEMO ? (
        <span className="muted" title="静态演示，没有实时连接">Snapshot</span>
      ) : (
        <>
          <span className={`ind ${live === "live" ? "ok" : "warn live"}`} title={live === "live" ? "实时连接正常" : "正在重新连接实时流"} />
          <span>{live === "live" ? "Live" : "Syncing"}</span>
        </>
      )}
    </button>
  );
}

type Hit = { kind: string; label: string; sub: string; go: () => void };

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const agents = useAtAgents();
  const missions = useAtMissions();
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test((e.target as HTMLElement)?.tagName);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  const hits = useMemo<Hit[]>(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    const out: Hit[] = [];
    agents.data?.forEach((a) => (a.name + " " + a.role + " " + a.id + " " + a.responsibility).toLowerCase().includes(s) &&
      out.push({ kind: "Worker", label: a.name, sub: a.id, go: () => go("agents", a.id) }));
    missions.data?.forEach((m) => (m.name + " " + m.id + " " + m.goal).toLowerCase().includes(s) &&
      out.push({ kind: "Mission", label: m.name, sub: m.id, go: () => go("missions", m.id) }));
    if (/^0x[0-9a-f]{64}$/i.test(s)) out.push({ kind: "Tx", label: short(s, 10), sub: "BSC live data", go: () => go("onchain", undefined, { q: s }) });
    if (/^0x[0-9a-f]{40}$/i.test(s)) out.push({ kind: "Address", label: short(s, 8), sub: "Wallet lookup", go: () => go("onchain", undefined, { tab: "wallet", address: s }) });
    if (/^job_/.test(s)) out.push({ kind: "Task", label: s, sub: "Agent task", go: () => go("missions", undefined, { task: s }) });
    if (/^tsk_/.test(s)) out.push({ kind: "Task", label: s, sub: "Legacy goal", go: () => go("tasks", s) });
    return out.slice(0, 12);
  }, [q, agents.data, missions.data]);
  useEffect(() => setI(0), [q]);
  const pick = (h?: Hit) => {
    if (!h) return;
    h.go();
    setQ("");
    input.current?.blur();
  };
  return (
    <div className="search rel">
      <Search size={13} />
      <input
        ref={input}
        className="input"
        placeholder="Search workers, missions, addresses"
        value={q}
        onChange={(e) => (setQ(e.target.value), setOpen(true))}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") (e.preventDefault(), setI((x) => Math.min(x + 1, hits.length - 1)));
          if (e.key === "ArrowUp") (e.preventDefault(), setI((x) => Math.max(x - 1, 0)));
          if (e.key === "Enter") pick(hits[i]);
          if (e.key === "Escape") (setQ(""), input.current?.blur());
        }}
        aria-label="全局搜索"
      />
      {!q && <span className="kbd">/</span>}
      {open && hits.length > 0 && (
        <div className="pop" role="listbox" style={{ left: 0, right: "auto", width: 380 }}>
          {hits.map((h, k) => (
            <button key={k} className="pop-item" role="option" aria-selected={k === i} onMouseEnter={() => setI(k)} onMouseDown={() => pick(h)}>
              <span className="tag" style={{ width: 58, justifyContent: "center" }}>{h.kind}</span>
              <span className="ellipsis grow">{h.label}</span>
              <span className="muted mono xs ellipsis" style={{ maxWidth: 120 }}>{h.sub}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
