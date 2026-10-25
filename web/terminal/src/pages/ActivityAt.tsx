import { Pause, Play } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { ActivityRow, TaskDialog } from "../components/AtBits";
import { EmptyState, ErrorState, Loading, Seg } from "../components/ui";
import { useAtActivity, useAtAgents, useAtLive, useAtStreamState, useAtWorkspaces, type AtActivity } from "../lib/at";
import { setParams, type Route } from "../lib/util";

const GROUPS: [string, string][] = [
  ["", "Everything"],
  ["agent.*,task.*,investigation.*,fix.*", "Agent work"],
  ["bug.*,alert.*,event.*,watcher.*", "Events & watchers"],
  ["patch.*,pr.*,approval.*,tests.*", "Patches & approvals"],
  ["mission.*", "Missions"],
  ["worker.*,system.*,budget.*", "Runtime"],
];

/** Every wake-up, hand-off and result: who triggered it, why, who it went to, what was done, the result, and whether you are needed. */
export default function Activity({ route }: { route: Route }) {
  const p = route.params;
  const filters = {
    range: p.get("range") || "24h",
    agent_id: p.get("agent") || "",
    workspace_id: p.get("ws") || "",
    severity: p.get("sev") || "",
    types: p.get("types") || "",
    needs_human: p.get("human") ? true : undefined,
  };
  const q = useAtActivity({ ...filters, limit: 500 });
  const agents = useAtAgents(true);
  const wss = useAtWorkspaces();
  const live = useAtStreamState();
  const [paused, setPaused] = useState(false);
  const [extra, setExtra] = useState<AtActivity[]>([]);
  const matches = useCallback((a: AtActivity) => (!filters.agent_id || a.agent_id === filters.agent_id || a.target_agent_id === filters.agent_id)
    && (!filters.workspace_id || a.workspace_id === filters.workspace_id) && (!filters.severity || filters.severity.split(",").includes(a.severity))
    && (!filters.needs_human || !!a.needs_human)
    && (!filters.types || filters.types.split(",").some((t) => (t.endsWith("*") ? a.type.startsWith(t.slice(0, -1)) : a.type === t))),
  [filters.agent_id, filters.workspace_id, filters.severity, filters.needs_human, filters.types]);
  useAtLive(useCallback((a: AtActivity) => { if (!paused && matches(a)) setExtra((x) => [a, ...x].slice(0, 300)); }, [paused, matches]));
  const rows = useMemo(() => {
    const seen = new Set<number>();
    return [...extra, ...(q.data || [])].filter((a) => (seen.has(a.id) ? false : (seen.add(a.id), true))).sort((a, b) => b.id - a.id);
  }, [extra, q.data]);
  const set = (k: string, v: string | null) => (setExtra([]), setParams(route, { [k]: v }));
  const task = p.get("task");
  return (
    <div className="page">
      <div className="toolbar">
        <h1>Activity</h1>
        <span className="count">{rows.length}</span>
        <span className={`st ${live === "live" ? "ok live" : "acc"}`}><i /><span className="t">{live === "live" ? "Live" : "Reconnecting"}</span></span>
        <div className="grow" />
        <button className="btn sm" onClick={() => setPaused(!paused)}>{paused ? <><Play size={12} /> Resume live</> : <><Pause size={12} /> Pause live</>}</button>
      </div>
      <div className="filterbar wrap">
        <Seg value={filters.range} onChange={(v) => set("range", v === "24h" ? null : v)} options={[["1h", "1h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"]]} />
        <select className="input sm" value={filters.agent_id} onChange={(e) => set("agent", e.target.value || null)} aria-label="Agent">
          <option value="">All agents</option>{(agents.data || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select className="input sm" value={filters.workspace_id} onChange={(e) => set("ws", e.target.value || null)} aria-label="Workspace">
          <option value="">All workspaces</option>{(wss.data || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <select className="input sm" value={filters.severity} onChange={(e) => set("sev", e.target.value || null)} aria-label="Severity">
          <option value="">Any severity</option><option value="medium,high,critical">Medium and above</option><option value="high,critical">High and above</option><option value="critical">Critical</option>
        </select>
        <select className="input sm" value={filters.types} onChange={(e) => set("types", e.target.value || null)} aria-label="Type">
          {GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label className="check xs"><input type="checkbox" checked={!!filters.needs_human} onChange={(e) => set("human", e.target.checked ? "1" : null)} /> Needs a human</label>
      </div>
      <div className="section">
        {q.isLoading ? <Loading rows={10} /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !rows.length ? (
          <EmptyState title="Nothing in this range">换一个时间范围或筛选条件。Agent 每一次被唤醒、交接、执行和完成都会记录在这里。</EmptyState>
        ) : <div className="act-list">{rows.map((a) => <ActivityRow key={a.id} a={a} onTask={(t) => setParams(route, { task: t })} />)}</div>}
      </div>
      {task && <TaskDialog id={task} onClose={() => setParams(route, { task: null })} />}
    </div>
  );
}
