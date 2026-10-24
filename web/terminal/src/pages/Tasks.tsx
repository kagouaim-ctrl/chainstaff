import { Download, Search } from "lucide-react";
import { useState } from "react";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { TaskInspector } from "../components/TaskInspector";
import { TaskTable } from "../components/TaskTable";
import { EmptyState, ErrorState, Loading, Seg, Sheet, Status, Tabs } from "../components/ui";
import { download } from "../lib/api";
import { useAgentName, useAgents, useApprovals, useMissions, useTasks } from "../lib/queries";
import type { Task, TaskStatus } from "../lib/types";
import { errText, fmtTime, go, setParams, toast, useIsMobile, type Route } from "../lib/util";

type F = "all" | "running" | "pending" | "review" | "completed" | "failed" | "approvals";
const GROUPS: Record<Exclude<F, "approvals">, TaskStatus[]> = {
  all: [],
  running: ["assigned", "running", "waiting"],
  pending: ["pending"],
  review: ["review"],
  completed: ["completed"],
  failed: ["failed", "cancelled"],
};

/** Full-width task table; a row opens the inspector beside it. Missions and tasks are separate views. */
export default function Tasks({ route }: { route: Route }) {
  const mobile = useIsMobile();
  const f = (route.params.get("f") as F) || "all";
  const kind = route.params.get("kind") === "goal" ? "goal" : "subtask";
  const close = () => go("tasks", undefined, Object.fromEntries(route.params));
  const insp = route.id && <TaskInspector id={route.id} onClose={close} />;
  return (
    <div className={`term no-rail ${route.id && !mobile ? "" : "no-insp"}`}>
      <section className="pane center">
        {f === "approvals" ? <ApprovalLog route={route} /> : <TaskList route={route} f={f} kind={kind} />}
      </section>
      {route.id && !mobile && <aside className="pane insp" aria-label="Task inspector">{insp}</aside>}
      {route.id && mobile && <Sheet onClose={close} tall>{insp}</Sheet>}
    </div>
  );
}

function Header({ route, f, counts, kind }: { route: Route; f: F; counts?: Record<TaskStatus, number>; kind: string }) {
  const n = (k: Exclude<F, "approvals">) => (counts ? (k === "all" ? Object.values(counts).reduce((a, b) => a + b, 0) : GROUPS[k].reduce((a, s) => a + (counts[s] || 0), 0)) : undefined);
  const approvals = useApprovals("pending");
  return (
    <>
      <div className="toolbar">
        <h1>Tasks</h1>
        <Seg value={kind} onChange={(v) => setParams(route, { kind: v === "goal" ? "goal" : null }, undefined)} options={[["subtask", "Tasks"], ["goal", "Missions"]]} />
        <span className="sub hide-m2">{kind === "goal" ? "A mission is the goal you hand over; it holds the tasks." : "Each task is one agent running one skill inside a mission."}</span>
      </div>
      <Tabs<F>
        value={f}
        onChange={(v) => setParams(route, { f: v === "all" ? null : v }, undefined)}
        options={[
          ["all", "All", n("all")], ["running", "Running", n("running")], ["pending", "Pending", n("pending")], ["review", "Review", n("review")],
          ["completed", "Completed", n("completed")], ["failed", "Failed", n("failed")], ["approvals", "Approval log", approvals.data?.length || undefined],
        ]}
      />
    </>
  );
}

function TaskList({ route, f, kind }: { route: Route; f: Exclude<F, "approvals">; kind: string }) {
  const agents = useAgents();
  const mobile = useIsMobile();
  const [q, setQ] = useState("");
  const [agent, setAgent] = useState("");
  const [priority, setPriority] = useState("");
  const [sort, setSort] = useState({ by: "created_at", desc: true });
  const [page, setPage] = useState(1);
  const size = 50;
  const status = GROUPS[f].join(",");
  const params = { q, agent, priority, kind, status, sort: sort.by, order: sort.desc ? "desc" : "asc", page, size };
  const tq = useTasks(params);
  const rows = tq.data?.items || [];
  const isGoal = kind === "goal";
  const total = tq.data?.total || 0;
  const open = (t: Task) => go("tasks", t.id, Object.fromEntries(route.params));
  return (
    <>
      <Header route={route} f={f} counts={tq.data?.counts} kind={kind} />
      <div className="qab">
        <div className="search" style={{ width: mobile ? "100%" : 260 }}>
          <Search size={13} />
          <input className="input" placeholder="任务 ID、名称、描述" value={q} onChange={(e) => (setQ(e.target.value), setPage(1))} aria-label="搜索任务" />
        </div>
        {!mobile && (
          <>
            <select className="input sm" value={agent} onChange={(e) => (setAgent(e.target.value), setPage(1))} aria-label="员工">
              <option value="">All agents</option>
              {agents.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <select className="input sm" value={priority} onChange={(e) => (setPriority(e.target.value), setPage(1))} aria-label="优先级">
              <option value="">Any priority</option>
              {[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}
            </select>
            <div className="grow" />
            <button className="btn sm" onClick={() => download(`/api/wf/tasks.csv?${new URLSearchParams(Object.entries({ q, agent, priority, kind, status }).filter(([, v]) => v) as [string, string][])}`, "tasks.csv").catch((e) => toast(errText(e), true))}>
              <Download size={12} /> CSV
            </button>
          </>
        )}
      </div>
      {tq.isLoading ? <Loading rows={10} /> : tq.error ? <ErrorState error={tq.error} retry={() => tq.refetch()} /> : (
        <TaskTable rows={rows} kind={isGoal ? "goal" : "subtask"} selected={route.id} onOpen={open} sort={sort} onSort={(by, desc) => setSort({ by, desc })} />
      )}
      <div className="pager">
        <span className="mono">{total}</span><span>{isGoal ? "missions" : "tasks"}</span>
        <div className="grow" />
        <button className="btn xs" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
        <span className="mono">{page} / {Math.max(1, Math.ceil(total / size))}</span>
        <button className="btn xs" disabled={page * size >= total} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </>
  );
}

function ApprovalLog({ route }: { route: Route }) {
  const [status, setStatus] = useState("pending");
  const q = useApprovals(status === "all" ? "" : status);
  const name = useAgentName();
  const missions = useMissions({ status: "review", limit: 200 });
  return (
    <>
      <Header route={route} f="approvals" kind={route.params.get("kind") === "goal" ? "goal" : "subtask"} />
      <div className="qab">
        <Seg value={status} onChange={setStatus} options={[["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "History"]]} />
        <span className="muted xs hide-m2">提交任务结果、超预算继续执行、写入链上，都要你在这里或 Workspace 里确认。</span>
      </div>
      <div className="scroll">
        {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data!.length ? <EmptyState title={status === "pending" ? "没有待审批的事项" : "没有记录"} /> : status === "pending" ? (
          <div className="col gap6" style={{ padding: 12 }}>
            {q.data!.map((a) => (
              <div key={a.id} className="col gap4">
                <div className="row gap6 xs muted">
                  <span>Mission</span>
                  <a className="link" href={`#/workspace/${a.workspace_id}?m=${a.task_id}`}>{missions.data?.find((m) => m.id === a.task_id)?.name || a.task_id}</a>
                </div>
                <ApprovalPanel approval={a} />
              </div>
            ))}
          </div>
        ) : (
          <table className="dt">
            <thead><tr><th>#</th><th>Type</th><th>Title</th><th>Requested by</th><th>Status</th><th>Decided</th></tr></thead>
            <tbody>
              {q.data!.map((a) => (
                <tr key={a.id} className="click" onClick={() => a.task_id && go("tasks", a.task_id, { f: "approvals" })}>
                  <td className="mono muted">{a.id}</td>
                  <td><span className="tag">{a.kind}</span></td>
                  <td className="ellipsis" style={{ maxWidth: 380 }}>{a.title}</td>
                  <td>{name(a.requested_by)}</td>
                  <td><Status kind="approval" status={a.status} /></td>
                  <td className="muted">{a.decided_by ? `${a.decided_by} · ${fmtTime(a.decided_at)}${a.note ? ` · ${a.note}` : ""}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
