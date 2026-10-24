import { RefreshCw } from "lucide-react";
import { useMemo } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { Addr, ErrorState, Loading, MixBar, Seg, Stat, StatusBadge } from "../components/ui";
import { useAgentName, useChain, useOverview } from "../lib/queries";
import type { Agent, ChainRecord, Message, Overview as OV, Task } from "../lib/types";
import { ago, duration, fmtTime, go, num, usd, usePref, useIsMobile } from "../lib/util";

const COLORS: Record<string, string> = { completed: "var(--ok)", failed: "var(--bad)", running: "var(--accent)", assigned: "var(--info)", pending: "var(--line-2)", cancelled: "var(--muted)", waiting: "var(--info)", review: "var(--warn)" };

export default function Overview() {
  const [range, setRange] = usePref<string>("overview-range", "24h");
  const q = useOverview(range);
  const chain = useChain();
  const name = useAgentName();
  const mobile = useIsMobile();
  if (q.isLoading) return <Loading rows={6} />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const done = d.tasks.completed, failed = d.tasks.failed;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Overview</h1>
        <span className="sub">
          planner {d.engine.planner} · model {d.engine.llm ? "on" : "off"} · etherscan {d.engine.etherscan ? "on" : "off"}
        </span>
        <div className="grow" />
        <Seg value={range} onChange={setRange} options={[["1h", "1H"], ["24h", "24H"], ["7d", "7D"], ["30d", "30D"], ["all", "All"]]} />
        <button className="btn icon" aria-label="刷新" onClick={() => q.refetch()}>
          <RefreshCw size={14} className={q.isFetching ? "acc" : ""} />
        </button>
      </div>
      <div className="stats">
        <Stat label="Total agents" value={d.agents.total} sub={`${d.agents.paused} paused`} />
        <Stat label="Active agents" value={d.agents.active} sub={<span><span className="acc">{d.agents.busy} busy</span> · {d.agents.idle} idle</span>} />
        <Stat label="Running tasks" value={d.running} tone={d.running ? "acc" : ""} sub={`${d.tasks.pending} pending`} />
        <Stat label="Completed" value={done} sub={done + failed ? `${((done / (done + failed)) * 100).toFixed(1)}% success` : "—"} tone="ok" />
        <Stat label="Pending approvals" value={d.pending_approvals} tone={d.pending_approvals ? "warn" : ""} sub={<a href="#/tasks?view=approvals">review →</a>} />
        <Stat label="Execution cost" value={usd(d.cost_usd)} sub={`${num(d.tokens)} tokens`} />
      </div>
      <div className="split" style={{ borderBottom: "1px solid var(--line)" }}>
        <section className="section" style={{ borderBottom: 0 }}>
          <div className="section-head">
            <h3>Agent activity</h3>
            <div className="grow" />
            <a className="btn ghost sm" href="#/activity">All →</a>
          </div>
          <ActivityMini rows={d.activity.slice(0, mobile ? 8 : 14)} name={name} />
        </section>
        <section className="section" style={{ borderBottom: 0 }}>
          <div className="section-head">
            <h3>Task execution</h3>
          </div>
          <TaskExecution d={d} />
        </section>
      </div>
      <section className="section" style={{ padding: 0 }}>
        <div className="section-head" style={{ padding: "10px 16px 0" }}>
          <h3>Recent tasks</h3>
          <div className="grow" />
          <a className="btn ghost sm" href="#/tasks">Task terminal →</a>
        </div>
        <RecentTasks rows={d.recent_tasks} name={name} />
      </section>
      <div className="split">
        <section className="section" style={{ padding: 0 }}>
          <div className="section-head" style={{ padding: "10px 16px 0" }}>
            <h3>Agent performance</h3>
          </div>
          <Performance rows={d.performance} />
        </section>
        <section className="section" style={{ padding: 0 }}>
          <div className="section-head" style={{ padding: "10px 16px 0" }}>
            <h3>On-chain events</h3>
            <div className="grow" />
            <a className="btn ghost sm" href="#/onchain">On-chain →</a>
          </div>
          <OnchainMini rows={d.onchain} />
          <div className="section" style={{ borderBottom: 0 }}>
            <h3 style={{ marginBottom: 6 }}>System status</h3>
            <dl className="kv">
              <dt>RPC</dt>
              <dd>
                {chain.data?.rpc ? (
                  chain.data.rpc.ok ? (
                    <span className="ok">
                      {chain.data.network} · block {num(chain.data.rpc.block)} · {chain.data.rpc.latency_ms}ms
                    </span>
                  ) : (
                    <span className="bad">{chain.data.rpc.error}</span>
                  )
                ) : (
                  <span className="muted">未配置链（CHAINSTAFF_CHAIN）</span>
                )}
              </dd>
              <dt>Anchor wallet</dt>
              <dd>{chain.data?.anchor ? <Addr value={chain.data.anchor.address} url={chain.data.anchor.url} /> : <span className="muted">未配置</span>}</dd>
              <dt>Model planner</dt>
              <dd>{d.engine.llm ? <span className="ok">ANTHROPIC_API_KEY 已配置</span> : <span className="muted">未配置，使用规则规划</span>}</dd>
              <dt>Tx history</dt>
              <dd>{d.engine.etherscan ? <span className="ok">Etherscan V2</span> : <span className="muted">未配置 ETHERSCAN_API_KEY</span>}</dd>
            </dl>
          </div>
        </section>
      </div>
    </div>
  );
}

function ActivityMini({ rows, name }: { rows: Message[]; name: (id?: string | null) => string }) {
  if (!rows.length) return <div className="muted">还没有活动。先在 Workforce 里创建员工，再到 Workspace 提交任务。</div>;
  return (
    <div className="col" style={{ gap: 0 }}>
      {rows.map((m) => (
        <div key={m.id} className="row" style={{ height: 24, fontSize: 12 }}>
          <span className="mono muted" style={{ width: 34 }}>{ago(m.at)}</span>
          <span className="tag" style={{ width: 52, textAlign: "center" }}>{m.kind}</span>
          <span className="ellipsis" style={{ width: 110, fontWeight: 500 }}>{name(m.sender)}</span>
          <span className="ellipsis dim grow">{m.text}</span>
        </div>
      ))}
    </div>
  );
}

function TaskExecution({ d }: { d: OV }) {
  // tasks per hour, stacked by status: a compact bar chart without a chart library
  const hours = useMemo(() => {
    const by: Record<string, Record<string, number>> = {};
    d.series.forEach((s) => ((by[s.hour] ||= {})[s.status] = s.n));
    return Object.entries(by).slice(-24);
  }, [d.series]);
  const max = Math.max(1, ...hours.map(([, v]) => Object.values(v).reduce((a, b) => a + b, 0)));
  const statuses = ["completed", "running", "assigned", "pending", "failed", "cancelled"] as const;
  return (
    <div className="col">
      <MixBar height={6} parts={statuses.map((s) => [d.tasks[s] || 0, COLORS[s]])} />
      <div className="row wrap" style={{ gap: 10, fontSize: 11 }}>
        {(["pending", "assigned", "running", "waiting", "review", "completed", "failed", "cancelled"] as const).map((s) => (
          <span key={s} className="row" style={{ gap: 4 }}>
            <i style={{ width: 8, height: 8, background: COLORS[s], borderRadius: 2, display: "inline-block" }} />
            <span className="muted">{s}</span>
            <span className="mono">{d.tasks[s] || 0}</span>
          </span>
        ))}
      </div>
      {hours.length > 0 ? (
        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 90, borderBottom: "1px solid var(--line)" }} aria-label="每小时子任务数">
          {hours.map(([h, v]) => {
            const tot = Object.values(v).reduce((a, b) => a + b, 0);
            return (
              <div key={h} title={`${h.slice(5).replace("T", " ")}:00 · ${tot}`} style={{ flex: 1, maxWidth: 22, display: "flex", flexDirection: "column-reverse", height: `${(tot / max) * 100}%` }}>
                {statuses.map((s) => v[s] ? <i key={s} style={{ display: "block", height: `${(v[s] / tot) * 100}%`, background: COLORS[s] }} /> : null)}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="muted">这个时间范围里没有子任务</div>
      )}
      <span className="muted" style={{ fontSize: 11 }}>每小时子任务数（UTC 小时）</span>
    </div>
  );
}

function RecentTasks({ rows, name }: { rows: Task[]; name: (id?: string | null) => string }) {
  const cols: Column<Task>[] = [
    { id: "id", header: "Task ID", cell: (t) => <span className="mono">{t.id}</span> },
    { id: "title", header: "Task", cell: (t) => <span className="ellipsis" style={{ maxWidth: 320, display: "inline-block" }}>{t.title}</span> },
    { id: "status", header: "Status", cell: (t) => <StatusBadge status={t.status} />, sort: (t) => t.status },
    { id: "mgr", header: "Manager", cell: (t) => name(t.assigned_agent) },
    { id: "steps", header: "Steps", cell: (t) => <span className="mono">{t.steps}/{t.max_steps}</span>, align: "r" },
    { id: "dur", header: "Duration", cell: (t) => <span className="mono">{duration(t.created_at, t.finished_at)}</span>, align: "r" },
    { id: "cost", header: "Cost", cell: (t) => <span className="mono">{usd(t.cost_usd)}</span>, align: "r", sort: (t) => Number(t.cost_usd) },
    { id: "created", header: "Created", cell: (t) => <span className="mono muted">{fmtTime(t.created_at)}</span>, sort: (t) => t.created_at },
  ];
  return <DataTable id="ov-tasks" rows={rows} columns={cols} rowKey={(t) => t.id} onRowClick={(t) => go("tasks", t.id)} empty={<div className="empty">还没有任务</div>} />;
}

function Performance({ rows }: { rows: Agent[] }) {
  const cols: Column<Agent>[] = [
    { id: "name", header: "Agent", cell: (a) => <span style={{ fontWeight: 500 }}>{a.name}</span> },
    { id: "live", header: "Status", cell: (a) => <StatusBadge kind="agent" status={a.live || a.status} /> },
    { id: "done", header: "Done", cell: (a) => <span className="mono">{a.tasks_completed}</span>, align: "r", sort: (a) => a.tasks_completed || 0 },
    { id: "rate", header: "Success", cell: (a) => <span className="mono">{a.success_rate == null ? "—" : `${(a.success_rate * 100).toFixed(0)}%`}</span>, align: "r", sort: (a) => a.success_rate ?? -1 },
    { id: "avg", header: "Avg time", cell: (a) => <span className="mono">{a.avg_secs == null ? "—" : `${a.avg_secs}s`}</span>, align: "r" },
    { id: "cost", header: "Cost", cell: (a) => <span className="mono">{usd(a.cost_usd)}</span>, align: "r" },
  ];
  return <DataTable id="ov-perf" rows={rows} columns={cols} rowKey={(a) => a.id} onRowClick={(a) => go("workforce", a.id)} />;
}

function OnchainMini({ rows }: { rows: ChainRecord[] }) {
  if (!rows.length) return <div className="muted" style={{ padding: "8px 16px" }}>还没有链上记录</div>;
  return (
    <table className="dt">
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono muted">{ago(r.at)}</td>
            <td><StatusBadge kind="chain" status={r.status} /></td>
            <td><Addr value={r.tx_hash} url={r.tx_url} /></td>
            <td className="mono r">{r.block ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
