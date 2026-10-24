import { ChevronDown, PanelRight, Plus, Settings2, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AgentStatus } from "../components/AgentStatus";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { ExecutionInspector, type ExecTab } from "../components/ExecutionInspector";
import { AssignmentTable, MissionHeader, MissionStage, MissionTimeline } from "../components/Mission";
import { TaskComposer } from "../components/TaskComposer";
import { Avatar, Dialog, EmptyState, ErrorState, Loading, Progress, Sheet } from "../components/ui";
import { api, post } from "../lib/api";
import { useAction, useAgentName, useAgents, useMission, useMissions, useTask, useWorkspaces } from "../lib/queries";
import type { Agent, Mission, TaskDetail, Workspace as WS } from "../lib/types";
import { ago, go, setParams, useIsMobile, usePref, type Route } from "../lib/util";

/**
 * Mission Workspace: the squad on the left, the selected mission in the middle as structured
 * sections (brief, assignment, collaboration, results, approval), and its execution on the right.
 *   #/workspace/<workspace>?m=<mission>&t=<task>&tab=<inspector tab>
 */
export default function Workspace({ route }: { route: Route }) {
  const ws = useWorkspaces();
  const [savedId, setSavedId] = usePref<string>("workspace", "");
  const [creating, setCreating] = useState(false);
  const missionId = route.params.get("m");
  // A link may name only the mission; find its workspace.
  const lookup = useMission(!route.id && missionId ? missionId : null);
  const id = route.id || lookup.data?.workspace_id || savedId;
  const current = ws.data?.find((w) => w.id === id) || ws.data?.find((w) => !w.archived);
  useEffect(() => {
    if (current && current.id !== savedId) setSavedId(current.id);
  }, [current, savedId, setSavedId]);

  if (ws.isLoading || (missionId && !route.id && lookup.isLoading)) return <div className="page"><Loading rows={8} /></div>;
  if (ws.error) return <div className="page"><ErrorState error={ws.error} retry={() => ws.refetch()} /></div>;
  if (!current)
    return (
      <div className="page">
        <EmptyState title="还没有工作区">
          <span>工作区是一个 Squad：一位 AI 主管加几位员工，一起接 Mission。</span>
          <button className="btn primary" onClick={() => setCreating(true)}><Plus size={13} /> 建立工作区</button>
        </EmptyState>
        {creating && <WorkspaceForm onClose={() => setCreating(false)} />}
      </div>
    );
  return (
    <>
      <MissionWorkspace ws={current} all={ws.data!} route={route} onCreate={() => setCreating(true)} />
      {creating && <WorkspaceForm onClose={() => setCreating(false)} />}
    </>
  );
}

function MissionWorkspace({ ws, all, route, onCreate }: { ws: WS; all: WS[]; route: Route; onCreate: () => void }) {
  const mobile = useIsMobile();
  const agents = useAgents();
  const missions = useMissions({ workspace: ws.id, limit: 100 });
  const [managing, setManaging] = useState(false);
  const [sheet, setSheet] = useState<"squad" | "inspector" | null>(null);
  const list = missions.data || [];
  const explicit = route.params.get("m");
  const fallback = list.find((m) => ["planning", "executing", "review"].includes(m.stage)) || list[0];
  const missionId = explicit === "new" ? null : explicit || fallback?.id || null;
  const m = list.find((x) => x.id === missionId) || null;
  const one = useMission(missionId && !m ? missionId : null);
  const mission = m || one.data || null;
  const detail = useTask(missionId);
  const taskId = route.params.get("t");
  const tab = (route.params.get("tab") as ExecTab) || "overview";
  const members = useMemo(
    () => [ws.manager_id, ...ws.members].map((id) => agents.data?.find((a) => a.id === id)).filter(Boolean) as Agent[],
    [ws, agents.data],
  );
  const pick = (id: string | null) => setParams(route, { m: id, t: null, tab: null }, ws.id);
  const pickTask = (id: string | null) => {
    setParams(route, { m: missionId, t: id, tab: id && tab === "overview" ? "overview" : tab }, ws.id);
    if (mobile && id) setSheet("inspector");
  };
  const setTab = (t: ExecTab) => setParams(route, { m: missionId, tab: t === "overview" ? null : t }, ws.id);

  const squad = <Squad ws={ws} all={all} members={members} missions={list} current={missionId} onPick={(id) => (pick(id), setSheet(null))} onCreate={onCreate} onManage={() => setManaging(true)} />;
  const inspector = <ExecutionInspector m={mission} d={detail.data} taskId={taskId} onTask={pickTask} tab={tab} onTab={setTab} />;
  const center = mission ? (
    <MissionView m={mission} d={detail.data} ws={ws} taskId={taskId} onTask={pickTask} onLaunched={(id) => pick(id)} mobile={mobile} onReport={() => (setParams(route, { m: missionId, t: null, tab: "results" }, ws.id), mobile && setSheet("inspector"))} />
  ) : (
    <Launcher ws={ws} missions={list} loading={missions.isLoading} onPick={pick} />
  );

  if (mobile)
    return (
      <div className="page" style={{ overflow: "hidden" }}>
        <div className="toolbar" style={{ gap: 6 }}>
          <button className="btn sm" onClick={() => setSheet("squad")} aria-label="Squad 和 Mission 列表"><Users size={13} /> {ws.name.slice(0, 14)} <ChevronDown size={12} /></button>
          <div className="grow" />
          {mission && <button className="btn sm" onClick={() => setSheet("inspector")}><PanelRight size={13} /> Inspector</button>}
          <button className="btn sm primary" onClick={() => pick("new")}><Plus size={13} /></button>
        </div>
        <div className="pane center" style={{ flex: 1 }}>{center}</div>
        {sheet === "squad" && <Sheet onClose={() => setSheet(null)} side="left">{squad}</Sheet>}
        {sheet === "inspector" && <Sheet onClose={() => setSheet(null)} tall>{inspector}</Sheet>}
        {managing && <WorkspaceForm ws={ws} onClose={() => setManaging(false)} />}
      </div>
    );

  return (
    <div className="term ws-term">
      <aside className="pane rail" aria-label="Agent squad">{squad}</aside>
      <section className="pane center" aria-label="Mission">
        {center}
      </section>
      <aside className="pane insp" aria-label="Execution inspector">{inspector}</aside>
      {managing && <WorkspaceForm ws={ws} onClose={() => setManaging(false)} />}
    </div>
  );
}

/** Left column: who is in the squad and what each one is doing, then the squad's missions. */
function Squad({ ws, all, members, missions, current, onPick, onCreate, onManage }: {
  ws: WS; all: WS[]; members: Agent[]; missions: Mission[]; current: string | null; onPick: (id: string | null) => void; onCreate: () => void; onManage: () => void;
}) {
  const live = missions.filter((m) => ["planning", "executing", "review"].includes(m.stage));
  const missionOf = (a: Agent) => a.current_task && missions.find((m) => m.id === a.current_task!.id || m.tasks.some((t) => t.id === a.current_task!.id));
  return (
    <>
      <div className="pane-h" style={{ gap: 6 }}>
        <select className="input sm grow" value={ws.id} onChange={(e) => go("workspace", e.target.value)} aria-label="切换工作区">
          {all.filter((w) => !w.archived).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button className="btn ghost sm icon" onClick={onCreate} title="新建工作区" aria-label="新建工作区"><Plus size={13} /></button>
        <button className="btn ghost sm icon" onClick={onManage} title="管理成员" aria-label="管理成员"><Settings2 size={13} /></button>
      </div>
      <div className="pane-h"><span className="label">Agent squad</span><span className="mono muted xs">{members.length}</span></div>
      <div style={{ flex: "none" }}>
        {members.map((a) => {
          const m = missionOf(a);
          return (
            <button key={a.id} className="squad-item" onClick={() => (m ? onPick(m.id) : go("terminal", a.id))} title={m ? "打开它正在做的 Mission" : "在 Terminal 打开"}>
              <Avatar name={a.name} mgr={a.is_manager} />
              <span className="who"><span className="ellipsis">{a.name}</span>{a.is_manager && <span className="tag acc">MGR</span>}<span style={{ marginLeft: "auto" }}><AgentStatus agent={a} label={false} /></span></span>
              <span className={`what ellipsis ${a.current_task ? "" : "muted"}`}>{a.current_task ? a.current_task.title : a.status === "active" ? "Idle" : a.status}</span>
              {m && <span className="meta"><span style={{ flex: 1 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></span></span>}
              <span className="meta"><span>{a.role || "—"}</span><span>·</span><span>{a.last_activity_at ? ago(a.last_activity_at) : "no activity"}</span></span>
            </button>
          );
        })}
        {!members.length && <div className="muted xs" style={{ padding: 12 }}>还没有成员。<button className="btn ghost xs" onClick={onManage}>添加</button></div>}
      </div>
      <div className="pane-h" style={{ borderTop: "1px solid var(--line)" }}>
        <span className="label">Missions</span>
        <span className="mono muted xs nowrap">{live.length} live · {missions.length}</span>
        <div className="grow" />
        <button className="btn ghost xs" onClick={() => onPick("new")}><Plus size={11} /> New</button>
      </div>
      <div className="scroll squad-missions">
        {missions.map((m) => (
          <button key={m.id} className="mission-item" aria-current={m.id === current} onClick={() => onPick(m.id)}>
            <span className="row gap6"><span className="t ellipsis grow">{m.name}</span>{m.approvals.length > 0 && <span className="badge acc">REVIEW</span>}</span>
            <span className="row gap6 xs muted"><MissionStage m={m} /><span className="grow" /><span className="mono">{ago(m.created_at)}</span></span>
            {m.progress.total > 0 && <Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} />}
          </button>
        ))}
        {!missions.length && <div className="muted xs" style={{ padding: 12 }}>还没有 Mission</div>}
      </div>
    </>
  );
}

function MissionView({ m, d, ws, taskId, onTask, onLaunched, mobile, onReport }: {
  m: Mission; d?: TaskDetail; ws: WS; taskId: string | null; onTask: (id: string | null) => void; onLaunched: (id: string) => void; mobile: boolean; onReport: () => void;
}) {
  const pending = d?.approvals.filter((a) => a.status === "pending") || [];
  const brief = d?.messages.find((x) => x.event === "user.instruction")?.text || m.brief;
  return (
    <>
      <MissionHeader m={m} d={d} compact={mobile} />
      <div className="scroll">
        {!d ? <Loading rows={8} /> : (
          <>
            {pending.length > 0 && (
              <div className="msec col gap6">
                {pending.map((a) => <ApprovalPanel key={a.id} approval={a} root={d.root} />)}
              </div>
            )}
            <section className="msec">
              <div className="msec-h"><span className="idx">1</span><h3>Brief</h3></div>
              <div className="brief">{brief}</div>
            </section>
            <section className="msec">
              <div className="msec-h"><span className="idx">2</span><h3>Task assignment</h3><span className="muted xs">{d.subtasks.length} tasks · click a row to inspect it</span></div>
              {mobile ? <MobileAssignments d={d} onTask={onTask} /> : <AssignmentTable d={d} selected={taskId} onSelect={(id) => onTask(id === taskId ? null : id)} />}
            </section>
            <section className="msec">
              <div className="msec-h"><span className="idx">3</span><h3>Agent progress &amp; collaboration</h3></div>
              <MissionTimeline d={d} onTask={onTask} />
            </section>
            <section className="msec">
              <div className="msec-h"><span className="idx">4</span><h3>Results</h3></div>
              <Deliverables m={m} d={d} onReport={onReport} />
            </section>
          </>
        )}
      </div>
      <TaskComposer ws={ws} onSubmitted={(t) => onLaunched(t.id)} />
    </>
  );
}

function MobileAssignments({ d, onTask }: { d: TaskDetail; onTask: (id: string) => void }) {
  const name = useAgentName();
  if (!d.subtasks.length) return <div className="muted sm">主管正在拆分任务…</div>;
  return (
    <div className="col gap2">
      {d.subtasks.map((s, i) => (
        <button key={s.id} className="m-row" style={{ padding: "8px 0" }} onClick={() => onTask(s.id)}>
          <span className="mono muted xs" style={{ width: 16 }}>{i + 1}</span>
          <span className="col gap2 grow"><span className="ellipsis sm">{s.title}</span><span className="muted xs">{name(s.assigned_agent)} · {s.skill}</span></span>
          <span className={`st ${s.status === "completed" ? "ok" : s.status === "failed" ? "bad" : s.status === "running" || s.status === "assigned" ? "acc live" : "mute"}`}><i /></span>
        </button>
      ))}
    </div>
  );
}

function Deliverables({ m, d, onReport }: { m: Mission; d: TaskDetail; onReport: () => void }) {
  const r = d.root.result;
  if (!r) return <div className="muted sm">{m.stage === "failed" || m.stage === "cancelled" ? `没有交付物：${m.error || m.stage}` : "所有任务结束后，结果汇总到这里，等你审批。"}</div>;
  const lines = (r.report_md || "").split("\n").filter((l: string) => l.trim()).slice(0, 8);
  return (
    <div className="col gap6">
      {r.findings?.length > 0 && (
        <div className="note acc col gap2">
          <span className="label">Findings · {r.findings.length}</span>
          {r.findings.map((f: string, i: number) => <span key={i} className="sm">• {f}</span>)}
        </div>
      )}
      {lines.length > 0 && <div className="report" style={{ maxHeight: 200 }}>{lines.join("\n")}{(r.report_md || "").split("\n").length > 8 ? "\n…" : ""}</div>}
      {r.output && <pre className="code" style={{ maxHeight: 180 }}>{JSON.stringify(r.output, null, 2)}</pre>}
      <div className="row gap12 xs muted wrap">
        <span>result hash <span className="mono dim">{d.root.result_hash?.slice(0, 18)}…</span></span>
        <span>{r.subtasks?.filter((s: any) => s.status === "completed").length || 0} task results included</span>
        <button className="btn ghost xs" onClick={onReport}>Full report →</button>
      </div>
    </div>
  );
}

/** No mission selected: start one, or pick from the squad's history. */
function Launcher({ ws, missions, loading, onPick }: { ws: WS; missions: Mission[]; loading: boolean; onPick: (id: string) => void }) {
  return (
    <>
      <div className="toolbar"><h1>New mission</h1><span className="sub">for {ws.name}</span></div>
      <div className="scroll">
        <div className="msec">
          <div className="note">
            一个 Mission 是交给 AI 员工的完整目标。主管把它拆成多个任务，分给 Squad 里有对应技能的员工并行执行，员工之间交接结果，最后汇总成报告交给你审批；审批通过后可以把结果哈希写到 BSC 测试网。
          </div>
        </div>
        <div className="msec">
          <div className="msec-h"><h3>Recent missions</h3></div>
          {loading ? <Loading rows={4} /> : !missions.length ? <div className="muted sm">还没有 Mission。</div> : (
            <table className="dt inner">
              <tbody>
                {missions.slice(0, 12).map((m) => (
                  <tr key={m.id} className="click" onClick={() => onPick(m.id)}>
                    <td className="ellipsis" style={{ maxWidth: 340 }}>{m.name}</td>
                    <td><MissionStage m={m} /></td>
                    <td style={{ width: 120 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></td>
                    <td className="r mono muted">{ago(m.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <TaskComposer ws={ws} rows={4} autoFocus onSubmitted={(t) => onPick(t.id)} />
    </>
  );
}

export function WorkspaceForm({ ws, onClose }: { ws?: WS; onClose: () => void }) {
  const agents = useAgents();
  const [name, setName] = useState(ws?.name || "");
  const [desc, setDesc] = useState(ws?.description || "");
  const [manager, setManager] = useState(ws?.manager_id || "");
  const [members, setMembers] = useState<string[]>(ws?.members || []);
  const managers = (agents.data || []).filter((a) => a.is_manager && a.status !== "archived");
  const workers = (agents.data || []).filter((a) => !a.is_manager && a.status !== "archived");
  const save = useAction(
    () => (ws ? api(`/api/wf/workspaces/${ws.id}`, { method: "PUT", json: { name, description: desc, manager_id: manager || null, members } }) : post("/api/wf/workspaces", { name, description: desc, manager_id: manager || null, members })),
    ["workspaces", "agents"],
    ws ? "已保存" : "工作区已建立",
  );
  return (
    <Dialog
      title={ws ? `Squad · ${ws.name}` : "New workspace"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: (w: any) => (onClose(), !ws && go("workspace", w.id)) })}>保存</button>
        </>
      }
    >
      <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
      <label className="field"><span>Description</span><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
      <label className="field">
        <span>AI Manager</span>
        <select className="input" value={manager} onChange={(e) => setManager(e.target.value)}>
          <option value="">— 选择主管 —</option>
          {managers.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {!managers.length && <small className="warn">还没有标记为 AI Manager 的员工，先到 Agents 创建一个。</small>}
      </label>
      <div className="field">
        <span>Members</span>
        <div className="chips">
          {workers.map((a) => (
            <button key={a.id} type="button" className="chip" aria-pressed={members.includes(a.id)} onClick={() => setMembers((m) => (m.includes(a.id) ? m.filter((x) => x !== a.id) : [...m, a.id]))}>
              {a.name}
            </button>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
