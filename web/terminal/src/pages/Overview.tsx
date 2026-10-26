import { AlertTriangle, ArrowRight, Bot, FileSearch, Plus, Target, Wallet } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";
import { ApprovalCard, Sev, TaskDialog } from "../components/AtBits";
import { WorkerCard } from "../components/WorkerCard";
import { EmptyState, ErrorState } from "../components/ui";
import { useAtActivity, useAtApprovals, useAtLive, useAtOverview, useAtRuntime, type AtActivity } from "../lib/at";
import { DEMO } from "../lib/demo";
import { openCreateWorker } from "../lib/shell";
import { ago, fmtTime, go, setParams, usd, type Route } from "../lib/util";

/**
 * Overview: the four numbers that matter, what you can start right now, your workers and what they did last.
 * Every number comes from the server; when the server has nothing to say the card says so instead of guessing.
 */
export default function Overview({ route }: { route: Route }) {
  const o = useAtOverview();
  const rt = useAtRuntime();
  const approvals = useAtApprovals("pending");
  const task = route.params.get("task");
  const openTask = useCallback((id: string) => setParams(route, { task: id }), [route]);
  const d = o.data;
  const agents = d?.agents || [];
  const active = agents.filter((a) => a.activated && a.status === "active");
  const working = agents.filter((a) => ["running", "investigating", "queued", "waiting"].includes(a.state)).length;
  const watching = agents.filter((a) => a.state === "watching").length;
  const k = d?.kpis;
  const g = rt.data?.gateway;
  return (
    <div className="page ov">
      <header className="hero">
        <div className="col gap6">
          <h1 className="hero-t">Welcome to KAGOU</h1>
          <p className="hero-s">Your Web3 Intelligence Workspace</p>
        </div>
        <div className="grow" />
        <button className="btn primary lg" disabled={DEMO} title={DEMO ? "演示版不能创建" : undefined} onClick={openCreateWorker}><Plus size={16} /> New Worker</button>
      </header>

      {o.error ? <ErrorState error={o.error} retry={() => o.refetch()} /> : (
        <div className="kpi-grid">
          <Kpi label="Active Workers" loading={o.isLoading} value={active.length} sub={`${working} working · ${watching} watching`} onClick={() => go("agents")} />
          <Kpi label="Running Missions" loading={o.isLoading} value={k?.running_missions} sub={k ? `${k.active_missions} active in total` : ""} onClick={() => go("missions", undefined, { status: "running" })} />
          <Kpi label="Pending Approvals" loading={o.isLoading} value={k?.pending_approvals} tone={k?.pending_approvals ? "acc" : ""} sub={k?.pending_approvals ? "Waiting for your decision" : "Nothing waiting"}
            onClick={() => (approvals.data?.length ? document.getElementById("ov-approvals")?.scrollIntoView({ behavior: "smooth" }) : go("activity", undefined, { human: "1" }))} />
          <Kpi label="Execution Cost" loading={rt.isLoading} value={g ? usd(g.spent_today_usd) : undefined} empty="—" sub={g ? `today · ${usd(g.spent_month_usd)} this month` : "Not reported by this server"}
            onClick={() => go("settings", undefined, { s: "billing" })} />
        </div>
      )}

      {!!d?.health.problems.length && (
        <div className="ov-problems">
          {d.health.problems.map((p) => (
            <div key={p} className="banner warn">
              <AlertTriangle size={15} className="acc" /> <span className="grow">{p}</span>
              {p.includes("Worker") && <a className="btn ghost sm" href="#/settings?s=runtime">Runtime</a>}
              {p.includes("API Key") && <a className="btn ghost sm" href="#/settings?s=models">AI Models</a>}
            </div>
          ))}
        </div>
      )}

      <section className="block">
        <div className="block-h"><h2>Quick Actions</h2></div>
        <div className="qa-grid">
          <QuickAction icon={<Bot size={20} />} title="New Worker" sub="Name it, pick a look, done" disabled={DEMO} onClick={openCreateWorker} />
          <QuickAction icon={<Target size={20} />} title="New Mission" sub="Give workers a goal" disabled={DEMO} onClick={() => go("missions", undefined, { new: "1" })} />
          <QuickAction icon={<Wallet size={20} />} title="Analyze Wallet" sub="Balance, tokens, activity" onClick={() => go("onchain", undefined, { tab: "wallet" })} />
          <QuickAction icon={<FileSearch size={20} />} title="Analyze Contract" sub="Owner, permissions, risks" onClick={() => go("onchain", undefined, { tab: "contract" })} />
        </div>
      </section>

      {!!approvals.data?.length && (
        <section className="block" id="ov-approvals">
          <div className="block-h"><h2>Waiting for you</h2><span className="block-n">{approvals.data.length}</span></div>
          <div className="col gap8">{approvals.data.slice(0, 3).map((a) => <ApprovalCard key={a.id} a={a} />)}</div>
        </section>
      )}

      <div className="ov-split">
        <section className="block">
          <div className="block-h">
            <h2>My Workers</h2><span className="block-n">{agents.length}</span>
            <div className="grow" />
            {agents.length > 0 && <a className="btn ghost sm" href="#/agents">View all <ArrowRight size={13} /></a>}
          </div>
          {o.isLoading ? <div className="wk-grid">{[0, 1, 2].map((i) => <div key={i} className="wk-card sk"><i className="skeleton" /><i className="skeleton" /><i className="skeleton" /></div>)}</div>
            : !agents.length ? (
              <div className="card"><EmptyState title="Create your first worker" icon={<Bot size={20} />}>
                <span className="muted">Name it and pick a look. Give it a job and a workspace when you are ready.</span>
                {!DEMO && <button className="btn primary" onClick={openCreateWorker}><Plus size={14} /> New Worker</button>}
              </EmptyState></div>
            ) : (
              <div className="wk-grid">{agents.slice(0, 6).map((a) => <WorkerCard key={a.id} a={a} onTask={openTask} />)}</div>
            )}
        </section>
        <section className="block">
          <div className="block-h"><h2>Recent Activity</h2><div className="grow" /><a className="btn ghost sm" href="#/activity">View all <ArrowRight size={13} /></a></div>
          <RecentActivity onTask={openTask} />
        </section>
      </div>
      {task && <TaskDialog id={task} onClose={() => setParams(route, { task: null })} />}
    </div>
  );
}

function Kpi({ label, value, sub, tone, loading, empty = "0", onClick }: { label: string; value?: ReactNode; sub?: ReactNode; tone?: string; loading?: boolean; empty?: string; onClick?: () => void }) {
  return (
    <button className="kpi" onClick={onClick}>
      <span className="kpi-l">{label}</span>
      {loading ? <i className="skeleton kpi-sk" /> : <span className={`kpi-v ${tone || ""}`}>{value ?? empty}</span>}
      <span className="kpi-s">{sub}</span>
    </button>
  );
}

function QuickAction({ icon, title, sub, onClick, disabled }: { icon: ReactNode; title: string; sub: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="qa" onClick={onClick} disabled={disabled} title={disabled ? "演示版只能查看" : undefined}>
      <span className="qa-ic">{icon}</span>
      <span className="col gap2"><span className="qa-t">{title}</span><span className="qa-s">{sub}</span></span>
      <ArrowRight size={15} className="qa-go" />
    </button>
  );
}

/** The last few things that happened; the full record is one click away. */
function RecentActivity({ onTask }: { onTask: (id: string) => void }) {
  const q = useAtActivity({ limit: 8 });
  const [live, setLive] = useState<AtActivity[]>([]);
  useAtLive(useCallback((a: AtActivity) => setLive((l) => [a, ...l.filter((x) => x.id !== a.id)].slice(0, 8)), []));
  const seen = new Set(live.map((a) => a.id));
  const rows = [...live, ...(q.data || []).filter((a) => !seen.has(a.id))].sort((a, b) => b.id - a.id).slice(0, 7);
  return (
    <div className="card ra">
      {q.isLoading ? <div className="col gap12" style={{ padding: 20 }}>{[0, 1, 2, 3].map((i) => <i key={i} className="skeleton" />)}</div>
        : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} />
        : !rows.length ? <EmptyState small title="No activity yet">Wake-ups, hand-offs and results will show up here.</EmptyState>
        : rows.map((a) => (
          <button key={a.id} className={`ra-row ${a.task_id ? "" : "static"}`} onClick={() => a.task_id && onTask(a.task_id)} disabled={!a.task_id}>
            <Sev s={a.severity} />
            <span className="col gap2 grow" style={{ minWidth: 0 }}>
              <span className="ra-t ellipsis">{a.title}</span>
              <span className="ra-s ellipsis">{a.agent_name || a.actor_name || a.actor}{a.result ? ` · ${a.result}` : ""}</span>
            </span>
            <span className="ra-when" title={fmtTime(a.at)}>{ago(a.at)}</span>
          </button>
        ))}
    </div>
  );
}
