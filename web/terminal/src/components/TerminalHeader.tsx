import { useQuery } from "@tanstack/react-query";
import { Activity, Bell, ChevronDown, LogOut, Search, Settings, ShieldCheck, SquareArrowOutUpRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, setToken } from "../lib/api";
import { DEMO } from "../lib/demo";
import { useAgentName, useAgents, useApprovals, useChain, useMeta, useMissions } from "../lib/queries";
import { useStreamState } from "../lib/stream";
import type { Message } from "../lib/types";
import { ago, go, num, short, usePref, type Route } from "../lib/util";
import { WalletButton } from "./WalletStatus";

export const NAV: [string, string][] = [
  ["terminal", "Terminal"],
  ["agents", "Agents"],
  ["squads", "Squads"],
  ["workspace", "Workspace"],
  ["tasks", "Tasks"],
  ["onchain", "On-Chain"],
  ["skills", "Skills"],
];
export const SECONDARY: [string, string][] = [
  ["activity", "Activity"],
  ["settings", "Settings"],
];

export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <rect width="20" height="20" rx="4" fill="#F0B90B" />
      <path d="M5.5 4.5h2.4v4.3l4-4.3h3l-4.6 4.9 4.9 6.1h-3l-3.6-4.6-0.7 0.7v3.9H5.5z" fill="#0B0E11" />
    </svg>
  );
}

export function TerminalHeader({ route }: { route: Route }) {
  return (
    <header className="head">
      <a className="brand" href="#/terminal" aria-label="KAGOU Terminal">
        <BrandMark />
        <span>KAGOU</span>
      </a>
      <nav className="nav" aria-label="主导航">
        {NAV.map(([id, label]) => (
          <a key={id} href={`#/${id}`} aria-current={route.page === id ? "page" : undefined}>
            {label}
          </a>
        ))}
        <MoreMenu route={route} />
      </nav>
      <div className="head-right">
        <DemoPill />
        <GlobalSearch />
        <NetworkPill />
        <RunningPill />
        <Notifications />
        <WalletButton />
      </div>
    </header>
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
  if (!meta.data || meta.data.engine.llm) return null;
  return (
    <button className="pill warn-pill d-only" onClick={() => go("settings")} title="服务器没有设置 ANTHROPIC_API_KEY：Agent 不调用模型，主管只用规则拆分">
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

function MoreMenu({ route }: { route: Route }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  const active = SECONDARY.some(([id]) => id === route.page);
  return (
    <div className="rel" ref={ref} style={{ display: "flex" }}>
      <a href="#" role="button" aria-expanded={open} aria-current={active ? "page" : undefined} onClick={(e) => (e.preventDefault(), setOpen(!open))}>
        {active ? SECONDARY.find(([id]) => id === route.page)![1] : "More"} <ChevronDown size={12} />
      </a>
      {open && (
        <div className="menu" style={{ left: 0, right: "auto", top: "calc(100% + 2px)" }} onClick={() => setOpen(false)}>
          <a href="#/activity"><Activity size={13} /> Activity</a>
          <a href="#/settings"><Settings size={13} /> Settings</a>
          {!DEMO && (
            <>
              <div className="pop-sep" />
              <a href="/"><SquareArrowOutUpRight size={13} /> Classic console</a>
              <button onClick={() => setToken("")}><LogOut size={13} /> Sign out</button>
            </>
          )}
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
      <span>{chain.data?.network || "No chain"}</span>
      {rpc?.ok && <span className="mono muted hide-xl">#{num(rpc.block)}</span>}
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

function RunningPill() {
  const missions = useMissions({ status: "pending,running,waiting", limit: 200 });
  const agents = useAgents();
  const n = missions.data?.length || 0;
  const busy = agents.data?.filter((a) => a.live === "busy").length || 0;
  return (
    <button className={`pill d-only ${n ? "acc" : ""}`} onClick={() => go("tasks", undefined, { f: "running" })} title={`${n} 个 Mission 正在执行，${busy} 个员工在工作`}>
      <span className={`ind ${n ? "info live" : ""}`} />
      <span className="mono">{n}</span>
      <span className="hide-l">running</span>
      <span className="muted mono hide-xl">· {busy} busy</span>
    </button>
  );
}

const NOTIFY = new Set(["approval.requested", "task.failed", "chain.confirmed", "chain.failed", "goal.review", "message.expired", "engine.error", "schedule.failed"]);

function Notifications() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  const approvals = useApprovals("pending");
  const name = useAgentName();
  const [seen, setSeen] = usePref<number>("notify-seen", 0);
  const events = useQuery({
    queryKey: ["activity", "notify"],
    queryFn: () => api<Message[]>("/api/wf/activity?kinds=system,task,chain,agent&limit=200"),
    select: (rows) => rows.filter((m) => NOTIFY.has(m.event) && (m.event !== "task.failed" || !!m.task_id)).slice(0, 30),
  });
  const pending = approvals.data || [];
  const unseen = (events.data || []).filter((m) => m.id > seen).length;
  const badge = pending.length + unseen;
  const toggle = () => {
    if (!open && events.data?.[0]) setSeen(events.data[0].id);
    setOpen(!open);
  };
  return (
    <div className="rel" ref={ref}>
      <button className="btn ghost icon rel" onClick={toggle} aria-label={`通知，${pending.length} 个待审批`} aria-expanded={open}>
        <Bell size={15} />
        {badge > 0 && <span className="count-dot">{badge > 99 ? "99+" : badge}</span>}
      </button>
      {open && (
        <div className="pop" style={{ width: 360 }}>
          <div className="pop-h">
            <span className="label grow">Needs you · <span className="mono">{pending.length}</span></span>
            <a className="btn ghost xs" href="#/tasks?f=review" onClick={() => setOpen(false)}>Review queue</a>
          </div>
          <div className="pop-b">
            {pending.map((a) => (
              <button key={a.id} className="pop-item" onClick={() => (setOpen(false), a.task_id && go("workspace", a.workspace_id, { m: a.task_id }))}>
                <ShieldCheck size={14} className="acc" style={{ flex: "none" }} />
                <span className="col gap2 grow">
                  <span className="ellipsis">{a.title}</span>
                  <span className="muted xs">{a.kind === "over_budget" ? "预算用完，等你决定" : "结果等你审批"} · {name(a.requested_by)} · {ago(a.at)}</span>
                </span>
              </button>
            ))}
            {!pending.length && <div className="pop-empty">没有等你处理的事项</div>}
            <div className="pop-h sep"><span className="label">Recent</span></div>
            {(events.data || []).map((m) => (
              <button key={m.id} className="pop-item" onClick={() => (setOpen(false), m.task_id ? go("tasks", m.task_id) : go("activity"))}>
                <span className={`ind ${m.event.includes("fail") || m.event.includes("error") || m.event.includes("expired") ? "bad" : m.event.startsWith("chain.confirmed") ? "ok" : "warn"}`} />
                <span className="ellipsis grow sm">{m.text}</span>
                <span className="muted mono xs">{ago(m.at)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type Hit = { kind: string; label: string; sub: string; go: () => void };

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const agents = useAgents();
  const missions = useMissions({ limit: 300 });
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
    agents.data?.forEach((a) => (a.name + " " + a.role + " " + a.id + " " + a.skills.join(" ")).toLowerCase().includes(s) &&
      out.push({ kind: "Agent", label: a.name, sub: a.role || a.id, go: () => go("terminal", a.id) }));
    missions.data?.forEach((m) => (m.name + " " + m.id + " " + m.brief).toLowerCase().includes(s) &&
      out.push({ kind: "Mission", label: m.name, sub: m.id, go: () => go("workspace", m.workspace_id, { m: m.id }) }));
    if (/^0x[0-9a-f]{64}$/i.test(s)) out.push({ kind: "Tx", label: short(s, 10), sub: "Execution ledger", go: () => go("onchain", undefined, { q: s }) });
    if (/^0x[0-9a-f]{40}$/i.test(s)) out.push({ kind: "Address", label: short(s, 8), sub: "Wallet lookup", go: () => go("onchain", undefined, { tab: "wallet", address: s }) });
    if (/^tsk_/.test(s)) out.push({ kind: "Task", label: s, sub: "Task terminal", go: () => go("tasks", s) });
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
        placeholder="Agent、Mission、任务、交易哈希"
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
