import { ArrowLeft, Download, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { ApprovalDialog, TaskInspector } from "../components/TaskInspector";
import { EmptyState, ErrorState, Loading, Priority, Seg, StatusBadge } from "../components/ui";
import { download } from "../lib/api";
import { useAgentName, useAgents, useApprovals, useTasks } from "../lib/queries";
import type { Approval, Task, TaskStatus } from "../lib/types";
import { duration, errText, fmtTime, go, toast, usd, useIsMobile, usePref, type Route } from "../lib/util";

const STATUSES: TaskStatus[] = ["pending", "assigned", "running", "waiting", "review", "completed", "failed", "cancelled"];

export default function Tasks({ route }: { route: Route }) {
  const view = route.params.get("view") === "approvals" ? "approvals" : "tasks";
  const mobile = useIsMobile();
  if (mobile && route.id)
    return (
      <div className="page">
        <div className="page-head"><a className="btn ghost sm" href="#/tasks"><ArrowLeft size={14} /> Tasks</a></div>
        <TaskInspector id={route.id} />
      </div>
    );
  return (
    <>
      <div className="page">
        <div className="page-head">
          <h1>Tasks</h1>
          <Seg value={view} onChange={(v) => go("tasks", undefined, v === "approvals" ? { view: v } : undefined)} options={[["tasks", "Task terminal"], ["approvals", "Approvals"]]} />
        </div>
        {view === "tasks" ? <TaskTable selected={route.id} /> : <Approvals />}
      </div>
      {!mobile && route.id && (
        <aside className="ctx" aria-label="任务详情">
          <TaskInspector id={route.id} onClose={() => go("tasks")} />
        </aside>
      )}
    </>
  );
}

function TaskTable({ selected }: { selected?: string }) {
  const agents = useAgents();
  const name = useAgentName();
  const mobile = useIsMobile();
  const [f, setF] = usePref("task-filters", { q: "", status: "", agent: "", priority: "", kind: "goal", start: "", end: "" });
  const [sort, setSort] = useState({ by: "created_at", desc: true });
  const [page, setPage] = useState(1);
  const size = 50;
  const q = useTasks({ ...f, sort: sort.by, order: sort.desc ? "desc" : "asc", page, size });
  const set = (k: keyof typeof f, v: string) => (setF({ ...f, [k]: v }), setPage(1));
  const statusSet = new Set(f.status ? f.status.split(",") : []);
  const toggleStatus = (s: string) => {
    statusSet.has(s) ? statusSet.delete(s) : statusSet.add(s);
    set("status", [...statusSet].join(","));
  };
  const cols: Column<Task>[] = [
    { id: "created_at", header: "Task ID", hideable: false, cell: (t) => <span className="mono">{t.id}</span> },
    { id: "title", header: "Task name", sort: () => 0, cell: (t) => <span className="ellipsis" style={{ maxWidth: 320, display: "inline-block" }} title={t.title}>{t.title}</span> },
    { id: "agent", header: "Assigned agent", cell: (t) => name(t.assigned_agent) },
    { id: "priority", header: "Priority", sort: () => 0, cell: (t) => <Priority p={t.priority} /> },
    { id: "status", header: "Status", sort: () => 0, cell: (t) => <StatusBadge status={t.status} /> },
    { id: "created", header: "Created", cell: (t) => <span className="mono muted">{fmtTime(t.created_at)}</span> },
    { id: "steps", header: "Duration", align: "r", cell: (t) => <span className="mono">{duration(t.started_at || t.created_at, t.finished_at)}</span> },
    { id: "cost_usd", header: "Cost", align: "r", sort: () => 0, cell: (t) => <span className="mono">{usd(t.cost_usd)}</span> },
    { id: "result", header: "Result", cell: (t) => (t.error ? <span className="bad ellipsis" style={{ maxWidth: 200, display: "inline-block" }} title={t.error}>{t.error}</span> : t.result_hash ? <span className="mono muted">{t.result_hash.slice(0, 12)}…</span> : <span className="muted">—</span>) },
    { id: "skill", header: "Skill", defaultHidden: true, cell: (t) => <span className="mono">{t.skill || "—"}</span> },
  ];
  const total = q.data?.total || 0;
  return (
    <>
      <div className="filters">
        <input className="input" style={{ width: mobile ? "100%" : 220 }} placeholder="搜索任务 ID 或名称" value={f.q} onChange={(e) => set("q", e.target.value)} aria-label="搜索任务" />
        <Seg value={f.kind} onChange={(v) => set("kind", v)} options={[["goal", "Goals"], ["subtask", "Subtasks"], ["", "All"]]} />
        <select className="input" value={f.agent} onChange={(e) => set("agent", e.target.value)} aria-label="员工筛选">
          <option value="">All agents</option>
          {agents.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select className="input" value={f.priority} onChange={(e) => set("priority", e.target.value)} aria-label="优先级筛选">
          <option value="">Any priority</option>
          {[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}
        </select>
        <input className="input" type="date" value={f.start} onChange={(e) => set("start", e.target.value)} aria-label="开始日期" />
        <input className="input" type="date" value={f.end} onChange={(e) => set("end", e.target.value)} aria-label="结束日期" />
        <div className="grow" />
        <button className="btn" onClick={() => download(`/api/wf/tasks.csv?${new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])}`, "tasks.csv").catch((e) => toast(errText(e), true))}>
          <Download size={13} /> Export
        </button>
      </div>
      <div className="filters" style={{ paddingTop: 0 }}>
        {STATUSES.map((s) => (
          <button key={s} className="chip" aria-pressed={statusSet.has(s)} onClick={() => toggleStatus(s)}>
            {s}
          </button>
        ))}
        {statusSet.size > 0 && <button className="btn ghost sm" onClick={() => set("status", "")}>clear</button>}
      </div>
      {q.isLoading ? (
        <Loading rows={8} />
      ) : q.error ? (
        <ErrorState error={q.error} retry={() => q.refetch()} />
      ) : mobile ? (
        <div className="m-list">
          {q.data!.items.map((t) => (
            <a key={t.id} href={`#/tasks/${t.id}`} className="m-card" style={{ textDecoration: "none" }}>
              <div className="row sp"><span className="ellipsis" style={{ fontWeight: 500 }}>{t.title}</span><StatusBadge status={t.status} /></div>
              <div className="row sp muted mono" style={{ fontSize: 11 }}><span>{t.id}</span><span>{fmtTime(t.created_at)} · {usd(t.cost_usd)}</span></div>
            </a>
          ))}
          {!q.data!.items.length && <EmptyState title="没有符合条件的任务" />}
        </div>
      ) : (
        <DataTable
          id="tasks"
          rows={q.data!.items}
          columns={cols}
          rowKey={(t) => t.id}
          selectedKey={selected}
          onRowClick={(t) => go("tasks", t.id)}
          serverSort={{ by: sort.by, desc: sort.desc, onChange: (by, desc) => setSort({ by, desc }) }}
          empty={<EmptyState title="没有符合条件的任务" />}
        />
      )}
      <div className="pager">
        <span>{total} tasks</span>
        <div className="grow" />
        <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
        <span className="mono">{page} / {Math.max(1, Math.ceil(total / size))}</span>
        <button className="btn sm" disabled={page * size >= total} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </>
  );
}

function Approvals() {
  const [status, setStatus] = useState("pending");
  const q = useApprovals(status === "all" ? "" : status);
  const name = useAgentName();
  const [open, setOpen] = useState<Approval | null>(null);
  return (
    <>
      <div className="filters">
        <Seg value={status} onChange={setStatus} options={[["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "History"]]} />
        <span className="muted" style={{ fontSize: 12 }}>发送链上交易、超预算继续执行、提交任务结果，都要在这里人工确认。</span>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data!.length ? (
        <EmptyState title={status === "pending" ? "没有待审批的事项" : "没有记录"} />
      ) : (
        <table className="dt">
          <thead><tr><th>#</th><th>Type</th><th>Title</th><th>Requested by</th><th>Status</th><th>Decided</th><th /></tr></thead>
          <tbody>
            {q.data!.map((a) => (
              <tr key={a.id}>
                <td className="mono muted">{a.id}</td>
                <td><span className="tag">{a.kind}</span></td>
                <td className="ellipsis" style={{ maxWidth: 380 }}>
                  {a.task_id ? <a href={`#/tasks/${a.task_id}`}>{a.title}</a> : a.title}
                </td>
                <td>{name(a.requested_by)}</td>
                <td><StatusBadge kind="approval" status={a.status} /></td>
                <td className="muted">{a.decided_by ? `${a.decided_by} · ${fmtTime(a.decided_at)}${a.note ? ` · ${a.note}` : ""}` : "—"}</td>
                <td>{a.status === "pending" && <button className="btn sm primary" onClick={() => setOpen(a)}><ShieldCheck size={12} /> Review</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <ApprovalDialog approval={open} onClose={() => setOpen(null)} />}
    </>
  );
}
