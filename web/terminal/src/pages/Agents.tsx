import { Copy, Plus, Search, Trash2 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { AgentForm } from "../components/AgentForm";
import { AgentInspector } from "../components/AgentInspector";
import { AgentStatus, liveOf } from "../components/AgentStatus";
import { DataTable, type Column } from "../components/DataTable";
import type { RunMode } from "../components/RunPanel";
import { Avatar, EmptyState, ErrorState, Loading, Sheet, Tabs } from "../components/ui";
import { del, post } from "../lib/api";
import { useAction, useAgents, useSkills, useWorkspaces } from "../lib/queries";
import type { Agent } from "../lib/types";
import { fmtTime, go, setParams, short, usd, useIsMobile, usePref, type Route } from "../lib/util";

type Filter = "roster" | "managers" | "paused" | "archived";

/** Roster management: who exists, how each one is configured, and bulk changes. Operating them lives in Terminal. */
export default function Agents({ route }: { route: Route }) {
  const agents = useAgents();
  const ws = useWorkspaces();
  const skills = useSkills();
  const mobile = useIsMobile();
  const [favs, setFavs] = usePref<string[]>("favorites", []);
  const filter = (route.params.get("f") as Filter) || "roster";
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [form, setForm] = useState<Agent | "new" | null>(null);
  const [action, setAction] = useState<RunMode | null>(null);
  const bulk = useAction((s: string) => post("/api/wf/agents/bulk", { ids: [...sel], status: s }), ["agents", "overview"], "已更新");
  const dup = useAction((id: string) => post<Agent>(`/api/wf/agents/${id}/duplicate`), ["agents"], "已复制");
  const remove = useAction((id: string) => del(`/api/wf/agents/${id}`), ["agents"], "已删除");
  const selected = route.id;
  const squads = useCallback((id: string) => (ws.data || []).filter((w) => w.members.includes(id) || w.manager_id === id), [ws.data]);
  const all = agents.data || [];
  const match = (a: Agent, f: Filter) => (f === "roster" ? a.status !== "archived" : f === "managers" ? a.is_manager && a.status !== "archived" : a.status === (f === "paused" ? "paused" : "archived"));
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((a) => match(a, filter) && (!s || `${a.name} ${a.role} ${a.id} ${a.skills.join(" ")} ${a.model}`.toLowerCase().includes(s)));
  }, [all, filter, q]);
  const skillName = (s: string) => skills.data?.find((x) => x.id === s)?.name || s;

  const cols: Column<Agent>[] = [
    {
      id: "agent", header: "Agent", hideable: false, sort: (a) => a.name.toLowerCase(),
      cell: (a) => (
        <span className="row gap6">
          <Avatar name={a.name} mgr={a.is_manager} />
          <span className="col" style={{ gap: 0, minWidth: 0 }}><span className="strong ellipsis">{a.name}</span><span className="sub">{a.id}</span></span>
        </span>
      ),
    },
    { id: "role", header: "Role", sort: (a) => a.role, cell: (a) => <span className="dim">{a.role || "—"}{a.is_manager && <span className="tag acc" style={{ marginLeft: 6 }}>MGR</span>}</span> },
    { id: "status", header: "Status", sort: (a) => liveOf(a), cell: (a) => <AgentStatus agent={a} /> },
    { id: "model", header: "Model", sort: (a) => a.model, cell: (a) => <span className="mono dim">{a.provider === "none" ? "skills only" : a.model}</span> },
    { id: "skills", header: "Skills", sort: (a) => a.skills.length, cell: (a) => <span className="row gap4">{a.skills.slice(0, 3).map((s) => <span key={s} className="tag">{skillName(s)}</span>)}{a.skills.length > 3 && <span className="muted xs">+{a.skills.length - 3}</span>}{!a.skills.length && <span className="muted">—</span>}</span> },
    { id: "perms", header: "Permissions", cell: (a) => <span className="mono muted xs">{a.permissions.join(" ") || "—"}</span> },
    { id: "squads", header: "Squads", cell: (a) => <span className="dim">{squads(a.id).map((w) => w.name).join(", ") || <span className="muted">—</span>}</span> },
    { id: "budget", header: "Budget cap", align: "r", sort: (a) => Number(a.budget_usd), cell: (a) => <span className="mono">{Number(a.budget_usd) ? usd(a.budget_usd) : "—"}</span> },
    { id: "cost", header: "Spent", align: "r", sort: (a) => Number(a.cost_usd), cell: (a) => <span className="mono">{usd(a.cost_usd)}</span> },
    { id: "erc", header: "ERC-8004", cell: (a) => <span className="mono dim">{a.onchain.agent_id ? `#${a.onchain.agent_id}` : "—"}{a.onchain.address ? ` · ${short(a.onchain.address, 4)}` : ""}</span> },
    { id: "created", header: "Created", sort: (a) => a.created_at, cell: (a) => <span className="mono muted">{fmtTime(a.created_at)}</span> },
    {
      id: "actions", header: "", title: "Actions", hideable: false, align: "r",
      cell: (a) => (
        <span className="acts" onClick={(e) => e.stopPropagation()}>
          <button className="btn ghost xs" onClick={() => setForm(a)}>Configure</button>
          <button className="btn ghost xs icon" title="Duplicate" aria-label="复制" onClick={() => dup.mutate(a.id, { onSuccess: (n) => go("agents", (n as Agent).id) })}><Copy size={11} /></button>
          <button className="btn ghost xs icon" title="Delete" aria-label="删除" onClick={() => confirm(`删除 ${a.name}？有历史任务的员工只能归档。`) && remove.mutate(a.id, { onSuccess: () => go("agents") })}><Trash2 size={11} /></button>
        </span>
      ),
    },
  ];

  if (agents.isLoading) return <div className="page"><Loading rows={10} /></div>;
  if (agents.error) return <div className="page"><ErrorState error={agents.error} retry={() => agents.refetch()} /></div>;
  const n = (f: Filter) => all.filter((a) => match(a, f)).length;
  const close = () => go("agents", undefined, Object.fromEntries(route.params));
  const inspector = selected && (
    <AgentInspector id={selected} onClose={close} onConfigure={setForm} fav={favs.includes(selected)} onFav={() => setFavs((f) => (f.includes(selected) ? f.filter((x) => x !== selected) : [...f, selected]))} action={action} onAction={setAction} />
  );

  return (
    <div className={`term no-rail ${selected && !mobile ? "" : "no-insp"}`}>
      <section className="pane center">
        <div className="toolbar">
          <h1>Agents</h1>
          <span className="sub">Roster and configuration</span>
          <div className="grow" />
          <div className="search d-only" style={{ width: 260 }}>
            <Search size={13} />
            <input className="input" placeholder="名称、角色、技能、模型" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索员工" />
          </div>
          <button className="btn primary sm" onClick={() => setForm("new")}><Plus size={12} /> New agent</button>
        </div>
        <Tabs<Filter> value={filter} onChange={(f) => setParams(route, { f: f === "roster" ? null : f })} options={[["roster", "Roster", n("roster")], ["managers", "Managers", n("managers")], ["paused", "Paused", n("paused")], ["archived", "Archived", n("archived")]]} />
        {sel.size > 0 && (
          <div className="qab" style={{ background: "var(--accent-bg)" }}>
            <span className="sm">{sel.size} selected</span>
            <span className="sep" />
            <button className="btn sm" onClick={() => bulk.mutate("active", { onSuccess: () => setSel(new Set()) })}>Activate</button>
            <button className="btn sm" onClick={() => bulk.mutate("paused", { onSuccess: () => setSel(new Set()) })}>Pause</button>
            <button className="btn sm danger" onClick={() => bulk.mutate("archived", { onSuccess: () => setSel(new Set()) })}>Archive</button>
            <button className="btn ghost sm" onClick={() => setSel(new Set())}>Clear</button>
          </div>
        )}
        {mobile ? (
          <div className="m-list">
            <div style={{ padding: "8px 12px" }}><div className="search"><Search size={13} /><input className="input" placeholder="搜索员工" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索员工" /></div></div>
            {rows.map((a) => (
              <button key={a.id} className="m-row" onClick={() => go("agents", a.id, Object.fromEntries(route.params))}>
                <Avatar name={a.name} mgr={a.is_manager} />
                <span className="col gap2 grow">
                  <span className="row gap6"><span className="strong ellipsis">{a.name}</span><AgentStatus agent={a} label={false} /></span>
                  <span className="muted xs ellipsis">{a.role || "—"} · {a.skills.map(skillName).join(", ") || "no skills"}</span>
                </span>
                <span className="mono muted xs">{a.provider === "none" ? "" : "LLM"}</span>
              </button>
            ))}
            {!rows.length && <EmptyState title="没有员工" />}
          </div>
        ) : (
          <DataTable
            id="roster"
            rows={rows}
            columns={cols}
            rowKey={(a) => a.id}
            onRowClick={(a) => go("agents", a.id, Object.fromEntries(route.params))}
            selectedKey={selected}
            selectable
            selection={sel}
            onSelection={setSel}
            empty={<EmptyState title={all.length ? "没有符合条件的员工" : "还没有 AI 员工"}><button className="btn primary" onClick={() => setForm("new")}>New agent</button></EmptyState>}
          />
        )}
      </section>
      {selected && !mobile && <aside className="pane insp">{inspector}</aside>}
      {selected && mobile && <Sheet onClose={close} tall>{inspector}</Sheet>}
      {form && <AgentForm agent={form === "new" ? undefined : form} onClose={() => setForm(null)} onSaved={(a) => form === "new" && go("agents", a.id)} />}
    </div>
  );
}
