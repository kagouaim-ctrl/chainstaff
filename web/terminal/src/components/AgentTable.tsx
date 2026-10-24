import { ArrowRight, Pause, Play, Settings2, Star, Zap } from "lucide-react";
import { memo, useCallback, useMemo } from "react";
import { post } from "../lib/api";
import { useAction, useSkills } from "../lib/queries";
import type { Agent } from "../lib/types";
import { ago, pct, secs, usd } from "../lib/util";
import { AgentStatus, liveOf } from "./AgentStatus";
import { DataTable, useFlash, type Column } from "./DataTable";
import { Avatar, EmptyState } from "./ui";
import type { RunMode } from "./RunPanel";

export interface AgentTableProps {
  rows: Agent[];
  selected?: string;
  favorites: string[];
  onFavorite: (id: string) => void;
  onSelect: (a: Agent) => void;
  onAction: (a: Agent, m: RunMode) => void;
  onConfigure: (a: Agent) => void;
  detailed?: boolean;
  empty?: React.ReactNode;
}

/** The roster as a dense, sortable table. Every row can be acted on without opening anything. */
export function AgentTable({ rows, selected, favorites, onFavorite, onSelect, onAction, onConfigure, detailed, empty }: AgentTableProps) {
  const skills = useSkills();
  const skillName = useCallback((s: string) => skills.data?.find((x) => x.id === s)?.name || s, [skills.data]);
  const flash = useFlash(rows, agentKey, liveKey);
  const cols = useMemo<Column<Agent>[]>(() => [
    {
      id: "fav", header: <Star size={11} />, title: "Favorite", width: 28, hideable: false, sort: (a) => (favorites.includes(a.id) ? 1 : 0),
      cell: (a) => (
        <button className="star" aria-pressed={favorites.includes(a.id)} aria-label="收藏" onClick={(e) => (e.stopPropagation(), onFavorite(a.id))}>
          <Star size={12} fill={favorites.includes(a.id) ? "currentColor" : "none"} />
        </button>
      ),
    },
    {
      id: "agent", header: "Agent", hideable: false, sort: (a) => a.name.toLowerCase(),
      cell: (a) => (
        <span className="row gap6" style={{ minWidth: 150 }}>
          <Avatar name={a.name} mgr={a.is_manager} />
          <span className="col" style={{ gap: 0, minWidth: 0 }}>
            <span className="strong ellipsis">{a.name}</span>
            {detailed && <span className="sub">{a.id}</span>}
          </span>
          {a.is_manager && <span className="tag acc">MGR</span>}
        </span>
      ),
    },
    { id: "role", header: "Role", sort: (a) => a.role, cell: (a) => <span className="dim">{a.role || "—"}</span> },
    { id: "status", header: "Status", sort: (a) => ({ busy: 0, review: 1, error: 2, idle: 3, paused: 4, archived: 5 } as Record<string, number>)[liveOf(a)] ?? 6, cell: (a) => <AgentStatus agent={a} /> },
    {
      id: "current", header: "Current task", sort: (a) => a.current_task?.title || "~",
      cell: (a) => a.current_task ? (
        <a className="link ellipsis" style={{ display: "inline-block", maxWidth: 240, verticalAlign: "middle" }} href={`#/tasks/${a.current_task.id}`} onClick={(e) => e.stopPropagation()} title={a.current_task.title}>
          {a.current_task.title}
        </a>
      ) : <span className="muted">{a.last_activity_at ? `idle · ${ago(a.last_activity_at)}` : "—"}</span>,
    },
    { id: "done", header: "Done", align: "r", sort: (a) => a.tasks_completed || 0, cell: (a) => <span className="mono">{a.tasks_completed ?? 0}</span> },
    { id: "rate", header: "Success", align: "r", sort: (a) => a.success_rate ?? -1, cell: (a) => <span className={`mono ${a.success_rate == null ? "muted" : a.success_rate >= 0.95 ? "ok" : a.success_rate < 0.8 ? "bad" : ""}`}>{pct(a.success_rate)}</span> },
    { id: "avg", header: "Avg time", align: "r", sort: (a) => a.avg_secs ?? 1e9, cell: (a) => <span className="mono">{secs(a.avg_secs)}</span> },
    { id: "cost", header: "Cost", align: "r", sort: (a) => Number(a.cost_usd), cell: (a) => <span className="mono">{usd(a.cost_usd)}</span> },
    {
      id: "skills", header: "Skills", sort: (a) => a.skills.length,
      cell: (a) => (
        <span className="row gap4">
          {a.skills.slice(0, detailed ? 4 : 2).map((s) => <span key={s} className="tag">{skillName(s)}</span>)}
          {a.skills.length > (detailed ? 4 : 2) && <span className="muted mono xs">+{a.skills.length - (detailed ? 4 : 2)}</span>}
          {!a.skills.length && <span className="muted">—</span>}
        </span>
      ),
    },
    { id: "model", header: "Model", defaultHidden: true, cell: (a) => <span className="mono dim">{a.provider === "none" ? "—" : a.model}</span> },
    { id: "actions", header: "", title: "Actions", hideable: false, align: "r", cell: (a) => <RowActions a={a} onAction={onAction} onConfigure={onConfigure} onView={onSelect} /> },
  ], [favorites, onFavorite, onAction, onConfigure, onSelect, detailed, skillName]);
  return (
    <DataTable
      id="agents"
      rows={rows}
      columns={cols}
      rowKey={agentKey}
      onRowClick={onSelect}
      selectedKey={selected}
      flash={flash}
      rowClass={(a) => (a.status === "archived" ? "dim" : "")}
      initialSort={{ by: "status", desc: false }}
      empty={empty ?? <EmptyState title="没有符合条件的员工" />}
    />
  );
}

const agentKey = (a: Agent) => a.id;
const liveKey = (a: Agent) => `${a.live}|${a.current_task?.id || ""}|${a.tasks_completed}`;

const RowActions = memo(function RowActions({ a, onAction, onConfigure, onView }: { a: Agent; onAction: (a: Agent, m: RunMode) => void; onConfigure: (a: Agent) => void; onView: (a: Agent) => void }) {
  const status = useAction((s: string) => post(`/api/wf/agents/${a.id}/status`, { status: s }), ["agents"]);
  const off = a.status !== "active";
  return (
    <span className="acts" onClick={(e) => e.stopPropagation()}>
      {!a.is_manager && (
        <button className="btn xs outline-acc" disabled={off} title="Run：让它马上执行一个技能" onClick={() => onAction(a, "run")}>
          <Zap size={10} /> Run
        </button>
      )}
      <button className="btn xs" disabled={off} title="Assign：交给它所在 Squad 一个 Mission" onClick={() => onAction(a, "assign")}>
        <ArrowRight size={10} /> Assign
      </button>
      {a.status === "active" ? (
        <button className="btn ghost xs icon" title="Pause" aria-label="暂停" onClick={() => status.mutate("paused")}><Pause size={11} /></button>
      ) : a.status === "paused" ? (
        <button className="btn ghost xs icon" title="Resume" aria-label="恢复" onClick={() => status.mutate("active")}><Play size={11} /></button>
      ) : <span style={{ width: 20 }} />}
      <button className="btn ghost xs icon" title="Configure" aria-label="配置" onClick={() => onConfigure(a)}><Settings2 size={11} /></button>
      <button className="btn ghost xs" title="View" onClick={() => onView(a)}>View</button>
    </span>
  );
});

/** One agent as a phone-width row: who, live state, what it is on, and the one action that matters. */
export const AgentRow = memo(function AgentRow({ agent: a, onOpen }: { agent: Agent; onOpen: (a: Agent, mode?: RunMode | null) => void }) {
  return (
    <div className="m-row" role="button" tabIndex={0} onClick={() => onOpen(a)} onKeyDown={(e) => e.key === "Enter" && onOpen(a)}>
      <Avatar name={a.name} mgr={a.is_manager} />
      <div className="col gap2 grow">
        <div className="row gap6"><span className="strong ellipsis">{a.name}</span><AgentStatus agent={a} label={false} /></div>
        <span className={`xs ellipsis ${a.current_task ? "acc" : "muted"}`}>{a.current_task ? a.current_task.title : `${a.role || "—"} · ${a.tasks_completed ?? 0} done`}</span>
      </div>
      <button className="btn sm outline-acc" disabled={a.status !== "active"} onClick={(e) => (e.stopPropagation(), onOpen(a, a.is_manager ? "assign" : "run"))} aria-label={`给 ${a.name} 派活`}>
        <Zap size={12} /> {a.is_manager ? "Assign" : "Run"}
      </button>
    </div>
  );
});
