import { useQuery } from "@tanstack/react-query";
import { Activity, BarChart3, Blocks, Boxes, CircleUser, History, LogOut, Menu, Search, Settings, SlidersHorizontal, SquareArrowOutUpRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, setToken } from "../lib/api";
import { useAtAgents, useAtMissions } from "../lib/at";
import { useChats } from "../lib/chat";
import { DEMO } from "../lib/demo";
import { useChain, useMeta } from "../lib/queries";
import { setSideOpen, toggleOps, useShell } from "../lib/shell";
import { useStreamState } from "../lib/stream";
import { go, num, short } from "../lib/util";
import { useOpsBadge } from "./LiveOps";

/** Everything used less often than chatting, agents, missions and on-chain work. Every entry opens a working page. */
export const MORE: { group: string; items: [string, string, string, typeof Boxes, Record<string, string>?][] }[] = [
  { group: "Work", items: [
    ["workspace", "Workspaces", "Repositories, contracts, tokens, wallets and watchers agents can use", Boxes],
    ["activity", "Activity History", "Every wake-up, hand-off and result", History],
  ] },
  { group: "Account", items: [
    ["usage", "Usage & Billing", "Model spend today and this month, by agent and by model, and budgets", BarChart3],
    ["settings", "Settings", "Models, API keys, notifications", Settings],
    ["settings", "Skills & Tools", "Web3 skills and the tools agents can call", Blocks, { s: "tools" }],
    ["settings", "Advanced", "Runtime, workers, sandbox and limits", SlidersHorizontal, { s: "runtime" }],
  ] },
];
const UNDER_MORE = new Set(["skills", "squads", "activity", "settings", "more", "terminal", "team", "board", "tasks", "log", "workspace", "usage"]);
export const navCurrent = (page: string) => (page === "overview" ? "chat" : UNDER_MORE.has(page) ? "more" : page);

export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <rect width="20" height="20" rx="6" fill="#F0B90B" />
      <path d="M5.5 4.5h2.4v4.3l4-4.3h3l-4.6 4.9 4.9 6.1h-3l-3.6-4.6-0.7 0.7v3.9H5.5z" fill="#0B0E11" />
    </svg>
  );
}

/** Brand, search and the few facts worth seeing on every page. Navigation lives in the sidebar. */
export function TerminalHeader() {
  return (
    <header className="head">
      <button className="btn ghost icon m-only-flex" onClick={() => setSideOpen(true)} aria-label="Open sidebar"><Menu size={18} /></button>
      <a className="brand" href="#/chat" aria-label="KAGOU">
        <BrandMark size={26} />
        <span>KAGOU</span>
      </a>
      <GlobalSearch />
      <div className="head-right">
        <DemoPill />
        <NetworkPill />
        <OpsButton />
        <AccountMenu />
      </div>
    </header>
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
      <button className="btn ghost icon acct" aria-label="账户" aria-expanded={open} onClick={() => setOpen(!open)}><CircleUser size={18} /></button>
      {open && (
        <div className="menu" onClick={() => setOpen(false)}>
          <a href="#/usage"><BarChart3 size={13} /> Usage & Billing</a>
          <a href="#/settings"><Settings size={13} /> Settings</a>
          <div className="pop-sep" />
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
  const chats = useChats();
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
    chats.data?.forEach((c) => (c.title + " " + (c.agent_name || "")).toLowerCase().includes(s) &&
      out.push({ kind: "Chat", label: c.title, sub: c.agent_name || "KAGOU", go: () => go("chat", c.id) }));
    agents.data?.forEach((a) => (a.name + " " + a.role + " " + a.id + " " + a.responsibility).toLowerCase().includes(s) &&
      out.push({ kind: "Agent", label: a.name, sub: a.id, go: () => go("agents", a.id) }));
    missions.data?.forEach((m) => (m.name + " " + m.id + " " + m.goal).toLowerCase().includes(s) &&
      out.push({ kind: "Mission", label: m.name, sub: m.id, go: () => go("missions", m.id) }));
    if (/^0x[0-9a-f]{64}$/i.test(s)) out.push({ kind: "Tx", label: short(s, 10), sub: "BSC live data", go: () => go("onchain", undefined, { q: s }) });
    if (/^0x[0-9a-f]{40}$/i.test(s)) out.push({ kind: "Address", label: short(s, 8), sub: "Wallet lookup", go: () => go("onchain", undefined, { tab: "wallet", address: s }) });
    if (/^job_/.test(s)) out.push({ kind: "Task", label: s, sub: "Agent task", go: () => go("missions", undefined, { task: s }) });
    if (/^tsk_/.test(s)) out.push({ kind: "Task", label: s, sub: "Legacy goal", go: () => go("tasks", s) });
    return out.slice(0, 12);
  }, [q, agents.data, missions.data, chats.data]);
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
        placeholder="搜索会话、Agent、任务或地址"
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
