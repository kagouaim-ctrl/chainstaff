import { ArrowLeft, Copy, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { AgentForm } from "../components/AgentForm";
import { DataTable, type Column } from "../components/DataTable";
import { Addr, EmptyState, ErrorState, Loading, Seg, Stat, StatusBadge } from "../components/ui";
import { del, post } from "../lib/api";
import { useAction, useAgent, useAgents, useMeta } from "../lib/queries";
import type { Agent } from "../lib/types";
import { fmtTime, go, usd, useIsMobile, usePref, type Route } from "../lib/util";

export default function Workforce({ route }: { route: Route }) {
  const agents = useAgents();
  const mobile = useIsMobile();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [density, setDensity] = usePref<"compact" | "detailed">("workforce-density", "compact");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [form, setForm] = useState<Agent | "new" | null>(null);
  const bulk = useAction((s: string) => post("/api/wf/agents/bulk", { ids: [...sel], status: s }), ["agents", "overview"], "已更新");
  const selected = route.id;

  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (agents.data || []).filter(
      (a) =>
        (status === "all" ? a.status !== "archived" : status === "archived" ? a.status === "archived" : a.live === status) &&
        (!s || (a.name + a.role + a.id + a.skills.join(" ")).toLowerCase().includes(s)),
    );
  }, [agents.data, q, status]);

  if (agents.isLoading) return <Loading rows={6} />;
  if (agents.error) return <ErrorState error={agents.error} retry={() => agents.refetch()} />;
  const all = agents.data!;
  const count = (f: (a: Agent) => boolean) => all.filter(f).length;

  if (mobile && selected) return <div className="page"><AgentDetail id={selected} onEdit={setForm} back />{form && form !== "new" && <AgentForm agent={form} onClose={() => setForm(null)} />}</div>;

  const cols: Column<Agent>[] = [
    {
      id: "agent", header: "Agent", hideable: false, sort: (a) => a.name,
      cell: (a) => (
        <div className="col" style={{ gap: 0 }}>
          <span style={{ fontWeight: 500 }}>
            {a.name} {a.is_manager && <span className="badge acc" style={{ height: 16 }}>Manager</span>}
          </span>
          {density === "detailed" && <span className="muted mono" style={{ fontSize: 11 }}>{a.id}</span>}
        </div>
      ),
    },
    { id: "role", header: "Role", cell: (a) => <span className="dim">{a.role || "—"}</span>, sort: (a) => a.role },
    { id: "status", header: "Status", cell: (a) => <StatusBadge kind="agent" status={a.live || a.status} />, sort: (a) => a.live || "" },
    { id: "current", header: "Current task", cell: (a) => (a.current_task ? <a href={`#/tasks/${a.current_task.id}`} onClick={(e) => e.stopPropagation()} className="ellipsis" style={{ maxWidth: 220, display: "inline-block" }}>{a.current_task.title}</a> : <span className="muted">—</span>) },
    { id: "done", header: "Tasks done", align: "r", cell: (a) => <span className="mono">{a.tasks_completed}</span>, sort: (a) => a.tasks_completed || 0 },
    { id: "rate", header: "Success", align: "r", cell: (a) => <span className={`mono ${a.success_rate != null && a.success_rate < 0.8 ? "warn" : ""}`}>{a.success_rate == null ? "—" : `${(a.success_rate * 100).toFixed(1)}%`}</span>, sort: (a) => a.success_rate ?? -1 },
    { id: "avg", header: "Avg exec", align: "r", cell: (a) => <span className="mono">{a.avg_secs == null ? "—" : `${a.avg_secs}s`}</span>, sort: (a) => a.avg_secs ?? 1e9 },
    { id: "cost", header: "Cost", align: "r", cell: (a) => <span className="mono">{usd(a.cost_usd)}</span>, sort: (a) => Number(a.cost_usd) },
    { id: "model", header: "Model", defaultHidden: true, cell: (a) => <span className="mono dim">{a.provider === "none" ? "—" : a.model}</span> },
    { id: "skills", header: "Skills", cell: (a) => <span className="row" style={{ gap: 4 }}>{a.skills.slice(0, density === "detailed" ? 6 : 2).map((s) => <span key={s} className="tag">{s}</span>)}{a.skills.length > (density === "detailed" ? 6 : 2) && <span className="muted">+{a.skills.length - (density === "detailed" ? 6 : 2)}</span>}</span> },
    { id: "actions", header: "Actions", hideable: false, cell: (a) => <RowActions a={a} onEdit={() => setForm(a)} /> },
  ];

  return (
    <>
      <div className="page">
        <div className="page-head">
          <h1>AI Workforce</h1>
          <div className="grow" />
          <button className="btn primary" onClick={() => setForm("new")}>
            <Plus size={14} />
            New agent
          </button>
        </div>
        <div className="stats">
          <Stat label="Total" value={count((a) => a.status !== "archived")} />
          <Stat label="Online" value={count((a) => a.status === "active")} tone="ok" />
          <Stat label="Busy" value={count((a) => a.live === "busy")} tone="acc" />
          <Stat label="Idle" value={count((a) => a.live === "idle")} />
          <Stat label="Failed (24h)" value={count((a) => a.live === "error")} tone={count((a) => a.live === "error") ? "bad" : ""} />
        </div>
        <div className="filters">
          <input className="input" style={{ width: mobile ? "100%" : 240 }} placeholder="搜索名称、角色、技能" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索员工" />
          <Seg value={status} onChange={setStatus} options={[["all", "All"], ["busy", "Busy"], ["idle", "Idle"], ["error", "Error"], ["paused", "Paused"], ["archived", "Archived"]]} />
          <div className="grow" />
          {!mobile && <Seg value={density} onChange={setDensity} options={[["compact", "Compact"], ["detailed", "Detailed"]]} />}
        </div>
        {sel.size > 0 && (
          <div className="filters" style={{ background: "var(--accent-bg)" }}>
            <span>{sel.size} selected</span>
            <button className="btn sm" onClick={() => bulk.mutate("active", { onSuccess: () => setSel(new Set()) })}>启用</button>
            <button className="btn sm" onClick={() => bulk.mutate("paused", { onSuccess: () => setSel(new Set()) })}>暂停</button>
            <button className="btn sm danger" onClick={() => bulk.mutate("archived", { onSuccess: () => setSel(new Set()) })}>归档</button>
            <button className="btn ghost sm" onClick={() => setSel(new Set())}>清除</button>
          </div>
        )}
        {!all.length ? (
          <EmptyState title="还没有 AI 员工">
            <span>先创建一个 AI Manager，再创建几个带技能的员工（钱包分析、合约分析、数据报告）。</span>
            <button className="btn primary" onClick={() => setForm("new")}>
              <Plus size={14} /> 创建第一个员工
            </button>
          </EmptyState>
        ) : mobile ? (
          <div className="m-list">
            {rows.map((a) => (
              <a key={a.id} className="m-card" href={`#/workforce/${a.id}`} style={{ textDecoration: "none" }}>
                <div className="row sp">
                  <span style={{ fontWeight: 600 }}>{a.name}</span>
                  <StatusBadge kind="agent" status={a.live || a.status} />
                </div>
                <div className="row sp muted" style={{ fontSize: 12 }}>
                  <span>{a.role || (a.is_manager ? "Manager" : "—")}</span>
                  <span className="mono">{a.tasks_completed} done · {a.success_rate == null ? "—" : `${(a.success_rate * 100).toFixed(0)}%`}</span>
                </div>
                {a.current_task && <div className="acc ellipsis" style={{ fontSize: 12 }}>▶ {a.current_task.title}</div>}
              </a>
            ))}
          </div>
        ) : (
          <DataTable id="workforce" rows={rows} columns={cols} rowKey={(a) => a.id} onRowClick={(a) => go("workforce", a.id)} selectedKey={selected} selectable selection={sel} onSelection={setSel} />
        )}
      </div>
      {!mobile && selected && (
        <aside className="ctx" aria-label="员工详情">
          <AgentDetail id={selected} onEdit={setForm} />
        </aside>
      )}
      {form && <AgentForm agent={form === "new" ? undefined : form} onClose={() => setForm(null)} onSaved={(a) => form === "new" && go("workforce", a.id)} />}
    </>
  );
}

function RowActions({ a, onEdit }: { a: Agent; onEdit: () => void }) {
  const status = useAction((s: string) => post(`/api/wf/agents/${a.id}/status`, { status: s }), ["agents"]);
  const dup = useAction(() => post(`/api/wf/agents/${a.id}/duplicate`), ["agents"], "已复制");
  const stop = (e: React.MouseEvent) => e.stopPropagation();
  return (
    <span className="row" style={{ gap: 2 }} onClick={stop}>
      <button className="btn ghost sm icon" title="编辑" aria-label="编辑" onClick={onEdit}><Pencil size={12} /></button>
      {a.status === "active" ? (
        <button className="btn ghost sm icon" title="暂停" aria-label="暂停" onClick={() => status.mutate("paused")}><Pause size={12} /></button>
      ) : (
        <button className="btn ghost sm icon" title="启用" aria-label="启用" onClick={() => status.mutate("active")}><Play size={12} /></button>
      )}
      <button className="btn ghost sm icon" title="复制" aria-label="复制" onClick={() => dup.mutate(undefined)}><Copy size={12} /></button>
    </span>
  );
}

type Tab = "overview" | "skills" | "memory" | "tasks" | "logs" | "permissions" | "identity";

function AgentDetail({ id, onEdit, back }: { id: string; onEdit: (a: Agent) => void; back?: boolean }) {
  const q = useAgent(id);
  const meta = useMeta();
  const [tab, setTab] = useState<Tab>("overview");
  const status = useAction((s: string) => post(`/api/wf/agents/${id}/status`, { status: s }), ["agents"]);
  const remove = useAction(() => del(`/api/wf/agents/${id}`), ["agents"], "已删除");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  return (
    <div className="col" style={{ gap: 0 }}>
      <div className="ctx-head">
        {back && <a className="btn ghost icon" href="#/workforce" aria-label="返回"><ArrowLeft size={16} /></a>}
        <div className="col grow" style={{ gap: 2 }}>
          <div className="row">
            <StatusBadge kind="agent" status={a.live || a.status} />
            {a.is_manager && <span className="badge acc">Manager</span>}
            <span className="mono muted">{a.id}</span>
          </div>
          <h2>{a.name}</h2>
        </div>
        <button className="btn sm" onClick={() => onEdit(a)}><Pencil size={12} /> Edit</button>
        {!back && <a className="btn ghost sm" href="#/workforce" aria-label="关闭">✕</a>}
      </div>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)", overflowX: "auto" }}>
        <Seg<Tab> value={tab} onChange={setTab} options={[["overview", "Overview"], ["skills", "Skills"], ["memory", "Memory"], ["tasks", "Tasks"], ["logs", "Logs"], ["permissions", "Permissions"], ["identity", "Identity"]]} />
      </div>
      <div style={{ padding: 12 }} className="col">
        {tab === "overview" && (
          <>
            {a.description && <div className="dim">{a.description}</div>}
            <dl className="kv">
              <dt>Role</dt><dd>{a.role || "—"}</dd>
              <dt>Current task</dt><dd>{a.current_task ? <a href={`#/tasks/${a.current_task.id}`}>{a.current_task.title}</a> : "—"}</dd>
              <dt>Completed</dt><dd className="mono">{a.tasks_completed} · failed {a.tasks_failed}</dd>
              <dt>Success</dt><dd className="mono">{a.success_rate == null ? "—" : `${(a.success_rate * 100).toFixed(1)}%`}</dd>
              <dt>Avg exec</dt><dd className="mono">{a.avg_secs == null ? "—" : `${a.avg_secs}s`}</dd>
              <dt>Model</dt><dd className="mono">{a.provider === "none" ? "none" : `${a.provider} · ${a.model}`}</dd>
              <dt>Cost</dt><dd className="mono">{usd(a.cost_usd)} · {a.tokens_in + a.tokens_out} tok · cap {Number(a.budget_usd) ? usd(a.budget_usd) : "none"}</dd>
              <dt>Workspaces</dt><dd>{a.workspaces.map((w) => <a key={w.id} href={`#/workspace/${w.id}`} style={{ marginRight: 8 }}>{w.name}</a>)}</dd>
              <dt>Created</dt><dd>{fmtTime(a.created_at)}</dd>
            </dl>
            {a.system_prompt && <details><summary className="muted">System prompt</summary><div className="report">{a.system_prompt}</div></details>}
            <div className="row wrap">
              {a.status !== "active" && <button className="btn sm" onClick={() => status.mutate("active")}><Play size={12} /> 启用</button>}
              {a.status === "active" && <button className="btn sm" onClick={() => status.mutate("paused")}><Pause size={12} /> 暂停</button>}
              {a.status !== "archived" && <button className="btn sm" onClick={() => status.mutate("archived")}>归档</button>}
              <button className="btn sm danger" onClick={() => confirm(`删除 ${a.name}？有历史任务的员工只能归档。`) && remove.mutate(undefined, { onSuccess: () => go("workforce") })}><Trash2 size={12} /> 删除</button>
            </div>
          </>
        )}
        {tab === "skills" && (
          <div className="col">
            {a.skills.length === 0 && <div className="muted">没有技能。编辑员工来添加。</div>}
            {a.skills.map((s) => (
              <div key={s} className="row sp" style={{ borderBottom: "1px dashed var(--line)", padding: "4px 0" }}>
                <span className="mono">{s}</span>
                {a.can_run[s] ? <span className="bad" style={{ fontSize: 12 }}>{a.can_run[s]}</span> : <span className="badge ok"><span className="dot" />ready</span>}
              </div>
            ))}
          </div>
        )}
        {tab === "memory" && (
          <div className="col">
            {a.memory.length === 0 && <div className="muted">还没有私有记忆。完成任务后会自动记录。</div>}
            {a.memory.map((m) => (
              <div key={m.id} className="note" style={{ fontSize: 12 }}>
                <div>{m.text}</div>
                <div className="muted mono" style={{ fontSize: 11 }}>{fmtTime(m.created_at)} · {m.expires_at ? `expires ${fmtTime(m.expires_at)}` : "no expiry"}</div>
              </div>
            ))}
          </div>
        )}
        {tab === "tasks" && (
          <table className="dt">
            <tbody>
              {a.tasks.map((t) => (
                <tr key={t.id} className="click" onClick={() => go("tasks", t.id)}>
                  <td className="ellipsis" style={{ maxWidth: 180 }}>{t.title}</td>
                  <td><StatusBadge status={t.status} /></td>
                  <td className="mono muted r">{fmtTime(t.created_at)}</td>
                </tr>
              ))}
              {!a.tasks.length && <tr><td className="muted">没有任务</td></tr>}
            </tbody>
          </table>
        )}
        {tab === "logs" && (
          <div className="col" style={{ gap: 0 }}>
            {a.logs.map((m) => (
              <div key={m.id} className="row" style={{ fontSize: 12, height: 24 }}>
                <span className="mono muted" style={{ width: 70 }}>{fmtTime(m.at, false)}</span>
                <span className="tag">{m.event}</span>
                <span className="ellipsis dim">{m.text}</span>
              </div>
            ))}
            {!a.logs.length && <div className="muted">没有执行日志</div>}
          </div>
        )}
        {tab === "permissions" && (
          <div className="col">
            {Object.entries(meta.data?.permissions || {}).map(([p, d]) => (
              <div key={p} className="row sp" style={{ fontSize: 12 }}>
                <span><span className="mono">{p}</span> <span className="muted">{d}</span></span>
                {a.permissions.includes(p) ? <span className="ok">granted</span> : <span className="muted">—</span>}
              </div>
            ))}
            <div className="note">员工没有任何签名或转账权限。链上写入只发生在人工审批之后，由服务器的测试网钱包执行。</div>
            <h3>Audit</h3>
            {a.audit.map((e) => <div key={e.id} className="muted" style={{ fontSize: 12 }}>{fmtTime(e.at)} · {e.text}</div>)}
          </div>
        )}
        {tab === "identity" && (
          <dl className="kv">
            <dt>Agent ID</dt><dd className="mono">{a.id}</dd>
            <dt>ERC-8004 ID</dt><dd className="mono">{a.onchain.agent_id || "—"}</dd>
            <dt>Address</dt><dd><Addr value={a.onchain.address} url={a.onchain.address && meta.data?.explorer ? meta.data.explorer.replace("/tx/", "/address/") + a.onchain.address : null} /></dd>
            <dt>Verified</dt><dd className="muted">未在链上核验（只记录你填写的身份）</dd>
          </dl>
        )}
      </div>
    </div>
  );
}
