import { useMemo } from "react";
import { useAgentName, useMissions } from "../lib/queries";
import type { Task } from "../lib/types";
import { ago, duration, fmtTime, usd, useIsMobile } from "../lib/util";
import { DataTable, useFlash, type Column } from "./DataTable";
import { MissionStage } from "./Mission";
import { Avatar, EmptyState, Pipe, Priority, Progress, Status } from "./ui";

export interface TaskTableProps {
  rows: Task[];
  kind: "goal" | "subtask";
  selected?: string | null;
  onOpen: (t: Task) => void;
  sort: { by: string; desc: boolean };
  onSort: (by: string, desc: boolean) => void;
}

/** Tasks (or missions) as a compact, server-sorted table; on a phone, a one-line list. Rows flash when their status changes. */
export function TaskTable({ rows, kind, selected, onOpen, sort, onSort }: TaskTableProps) {
  const name = useAgentName();
  const mobile = useIsMobile();
  const missions = useMissions({ limit: 500 });
  const byMission = useMemo(() => new Map((missions.data || []).map((m) => [m.id, m])), [missions.data]);
  const flash = useFlash(rows, taskKey, statusKey);
  const isGoal = kind === "goal";
  const cols: Column<Task>[] = [
    {
      id: "title", header: isGoal ? "Mission" : "Task", hideable: false, sort: () => 0,
      cell: (t) => (
        <span className="col" style={{ gap: 0, minWidth: 0, maxWidth: 380 }}>
          <span className="ellipsis strong" title={t.title}>{t.title}</span>
          <span className="sub">{t.id}{!isGoal && byMission.get(t.root_id) ? ` · ${byMission.get(t.root_id)!.name.slice(0, 40)}` : ""}</span>
        </span>
      ),
    },
    { id: "agent", header: isGoal ? "Owner" : "Agent", cell: (t) => <span className="row gap6"><Avatar name={name(t.assigned_agent)} /><span className="ellipsis">{name(t.assigned_agent)}</span></span> },
    { id: "priority", header: "Priority", sort: () => 0, cell: (t) => <Priority p={t.priority} /> },
    { id: "status", header: "Status", sort: () => 0, cell: (t) => (isGoal && byMission.get(t.id) ? <MissionStage m={byMission.get(t.id)!} /> : <Status status={t.status} />) },
    {
      id: "progress", header: "Progress",
      cell: (t) => {
        const m = isGoal ? byMission.get(t.id) : null;
        return m ? <span style={{ display: "inline-block", width: 110 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></span> : <Pipe status={t.status} />;
      },
    },
    { id: "duration", header: "Duration", align: "r", cell: (t) => <span className="mono">{t.started_at || isGoal ? duration(t.started_at || t.created_at, t.finished_at) : "—"}</span> },
    { id: "cost_usd", header: "Cost", align: "r", sort: () => 0, cell: (t) => <span className="mono">{usd(t.cost_usd)}</span> },
    { id: "created_at", header: "Created", sort: () => 0, cell: (t) => <span className="mono muted" title={fmtTime(t.created_at)}>{ago(t.created_at)}</span> },
    { id: "skill", header: "Skill", defaultHidden: isGoal, cell: (t) => (t.skill ? <span className="tag">{t.skill}</span> : <span className="muted">—</span>) },
    { id: "result", header: "Result", defaultHidden: true, cell: (t) => (t.error ? <span className="bad ellipsis" style={{ maxWidth: 200, display: "inline-block" }} title={t.error}>{t.error}</span> : t.result_hash ? <span className="mono muted">{t.result_hash.slice(0, 12)}…</span> : <span className="muted">—</span>) },
  ];
  if (mobile)
    return (
      <div className="m-list scroll">
        {rows.map((t) => (
          <button key={t.id} className="m-row" onClick={() => onOpen(t)}>
            {isGoal ? null : <Pipe status={t.status} />}
            <span className="col gap2 grow">
              <span className="ellipsis sm strong">{t.title}</span>
              <span className="muted xs ellipsis">{name(t.assigned_agent)} · {ago(t.created_at)} · {usd(t.cost_usd)}</span>
            </span>
            <Status status={t.status} />
          </button>
        ))}
        {!rows.length && <EmptyState title="没有符合条件的任务" />}
      </div>
    );
  return (
    <DataTable
      id={`tasks-${kind}`}
      rows={rows}
      columns={cols}
      rowKey={taskKey}
      selectedKey={selected}
      onRowClick={onOpen}
      flash={flash}
      serverSort={{ by: sort.by, desc: sort.desc, onChange: onSort }}
      empty={<EmptyState title="没有符合条件的任务" />}
    />
  );
}

const taskKey = (t: Task) => t.id;
const statusKey = (t: Task) => t.status;
