import { ChevronDown, CircleAlert, Crown, Inbox, PanelRight, Plus, Settings2, UserMinus, UserPlus, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AgentStatus } from "../components/AgentStatus";
import { ExecutionLogs } from "../components/Execution";
import { ExecutionFeed } from "../components/ExecutionFeed";
import { ExecutionInspector, type ExecTab } from "../components/ExecutionInspector";
import { LiveBoard } from "../components/LiveBoard";
import { MissionHeader, MissionStage } from "../components/Mission";
import { AddMemberDialog, SquadForm } from "../components/SquadForm";
import { TaskComposer } from "../components/TaskComposer";
import { Avatar, Dialog, EmptyState, ErrorState, Loading, Progress, Seg, Sheet } from "../components/ui";
import { api, del, post } from "../lib/api";
import { useAction, useAgentName, useAgents, useMission, useMissions, useSquads, useTask, useWorkspaces } from "../lib/queries";
import type { Agent, Mission, Squad, TaskDetail, Workspace as WS } from "../lib/types";
import { ago, go, setParams, useIsMobile, usePref, type Route } from "../lib/util";

/**
 * Mission Workspace: the squad on the left, the selected mission in the middle as structured
 * sections (brief, assignment, collaboration, results, approval), and its execution on the right.
 *   #/board/<workspace>?m=<mission>&t=<task>&tab=<inspector tab>
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
        <EmptyState title="还没有工作区" icon={<Users size={18} />}>
          <span>工作区装着一个或多个 Squad；每个 Squad 是一位 AI 主管加几位员工，一起接 Mission。</span>
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
  const squads = useSquads(ws.id);
  const [squadForm, setSquadForm] = useState<Squad | "new" | null>(null);
  const [adding, setAdding] = useState(false);
  const [sheet, setSheet] = useState<"squad" | "inspector" | null>(null);
  const [scope, setScope] = usePref<"squad" | "all">("ws-mission-scope", "squad");
  const sqList = (squads.data || []).filter((x) => x.workspace_id === ws.id);
  const explicit = route.params.get("m");
  const lookup = useMission(explicit && explicit !== "new" ? explicit : null);
  const squadParam = route.params.get("s") || lookup.data?.squad_id || null;
  const squad = sqList.find((x) => x.id === squadParam) || sqList.find((x) => x.status === "active" && x.manager_id) || sqList[0];
  const missions = useMissions({ workspace: ws.id, squad: scope === "squad" && squad ? squad.id : undefined, limit: 100 });
  const list = missions.data || [];
  const fallback = list.find((m) => ["planning", "executing", "review"].includes(m.stage)) || list[0];
  const missionId = explicit === "new" ? null : explicit || fallback?.id || null;
  const m = list.find((x) => x.id === missionId) || (lookup.data?.id === missionId ? lookup.data : null);
  const one = useMission(missionId && !m ? missionId : null);
  const mission = m || one.data || null;
  const detail = useTask(missionId);
  const taskId = route.params.get("t");
  const tab = (route.params.get("tab") as ExecTab) || "overview";
  const members = useMemo(
    () => (squad ? [squad.manager_id, ...squad.members] : []).map((id) => agents.data?.find((a) => a.id === id)).filter(Boolean) as Agent[],
    [squad, agents.data],
  );
  const pickSquad = (id: string) => setParams(route, { s: id, m: null, t: null, tab: null }, ws.id);
  const pick = (id: string | null) => setParams(route, { m: id, t: null, tab: null }, ws.id);
  const pickTask = (id: string | null) => {
    setParams(route, { m: missionId, t: id, tab: id && tab === "overview" ? "overview" : tab }, ws.id);
    if (mobile && id) setSheet("inspector");
  };
  const setTab = (t: ExecTab) => setParams(route, { m: missionId, tab: t === "overview" ? null : t }, ws.id);

  const left = (
    <SquadPanel ws={ws} all={all} squads={sqList} squad={squad} members={members} missions={list} current={missionId} scope={scope} onScope={setScope}
      onPick={(id) => (pick(id), setSheet(null))} onSquad={pickSquad} onCreate={onCreate} onNewSquad={() => setSquadForm("new")}
      onEditSquad={() => squad && setSquadForm(squad)} onAdd={() => setAdding(true)} />
  );
  const inspector = <ExecutionInspector m={mission} d={detail.data} taskId={taskId} onTask={pickTask} tab={tab} onTab={setTab} />;
  const center = mission ? (
    <MissionView m={mission} d={detail.data} ws={ws} squad={squad} taskId={taskId} onTask={pickTask} onLaunched={(id) => pick(id)} onSquad={pickSquad} mobile={mobile}
      onReport={() => (setParams(route, { m: missionId, t: null, tab: "results" }, ws.id), mobile && setSheet("inspector"))} />
  ) : (
    <Launcher ws={ws} squad={squad} missions={list} loading={missions.isLoading} onPick={pick} onSquad={pickSquad} onNewSquad={() => setSquadForm("new")} />
  );
  const dialogs = (
    <>
      {squadForm && <SquadForm squad={squadForm === "new" ? undefined : squadForm} workspaceId={ws.id} onClose={() => setSquadForm(null)} onSaved={(x) => pickSquad(x.id)} />}
      {adding && squad && <AddMemberDialog squad={squad} onClose={() => setAdding(false)} />}
    </>
  );

  if (mobile)
    return (
      <div className="page" style={{ overflow: "hidden" }}>
        <div className="toolbar" style={{ gap: 6 }}>
          <button className="btn sm" onClick={() => setSheet("squad")} aria-label="Squad 和 Mission 列表"><Users size={13} /> <span className="ellipsis" style={{ maxWidth: 140 }}>{squad?.name || ws.name}</span> <ChevronDown size={12} /></button>
          <div className="grow" />
          {mission && <button className="btn sm" onClick={() => setSheet("inspector")}><PanelRight size={13} /> Inspector</button>}
          <button className="btn sm primary" onClick={() => pick("new")} aria-label="New mission"><Plus size={13} /></button>
        </div>
        <div className="pane center" style={{ flex: 1 }}>{center}</div>
        {sheet === "squad" && <Sheet onClose={() => setSheet(null)} side="left">{left}</Sheet>}
        {sheet === "inspector" && <Sheet onClose={() => setSheet(null)} tall>{inspector}</Sheet>}
        {dialogs}
      </div>
    );

  return (
    <div className="term ws-term">
      <aside className="pane rail" aria-label="Agent squad">{left}</aside>
      <section className="pane center" aria-label="Mission">{center}</section>
      <aside className="pane insp" aria-label="Execution inspector">{inspector}</aside>
      {dialogs}
    </div>
  );
}

/** Left column: workspace and squad, who is in it and what each one is doing, then the missions. */
function SquadPanel({ ws, all, squads, squad, members, missions, current, scope, onScope, onPick, onSquad, onCreate, onNewSquad, onEditSquad, onAdd }: {
  ws: WS; all: WS[]; squads: Squad[]; squad?: Squad; members: Agent[]; missions: Mission[]; current: string | null; scope: "squad" | "all"; onScope: (s: "squad" | "all") => void;
  onPick: (id: string | null) => void; onSquad: (id: string) => void; onCreate: () => void; onNewSquad: () => void; onEditSquad: () => void; onAdd: () => void;
}) {
  const live = missions.filter((m) => ["planning", "executing", "review"].includes(m.stage));
  const keys = ["squads", "agents", "workspaces", "preflight"];
  const remove = useAction((id: string) => del(`/api/wf/squads/${squad!.id}/members/${id}`), keys, "已移出 Squad");
  const promote = useAction((id: string) => post(`/api/wf/squads/${squad!.id}/manager`, { agent_id: id }), keys, "主管已更换");
  return (
    <>
      <div className="pane-h" style={{ gap: 6 }}>
        <select className="input sm grow" value={ws.id} onChange={(e) => go("board", e.target.value)} aria-label="切换工作区">
          {all.filter((w) => !w.archived).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button className="btn ghost sm icon" onClick={onCreate} title="新建工作区" aria-label="新建工作区"><Plus size={13} /></button>
      </div>
      <div className="pane-h" style={{ gap: 6 }}>
        {squads.length ? (
          <select className="input sm grow" value={squad?.id || ""} onChange={(e) => onSquad(e.target.value)} aria-label="切换 Squad">
            {squads.map((x) => <option key={x.id} value={x.id}>{x.name}{x.status === "paused" ? "（暂停）" : ""}</option>)}
          </select>
        ) : <span className="muted xs grow">还没有 Squad</span>}
        <button className="btn ghost sm icon" onClick={onNewSquad} title="新建 Squad" aria-label="新建 Squad"><Users size={13} /></button>
        {squad && <button className="btn ghost sm icon" onClick={onEditSquad} title="编辑 Squad" aria-label="编辑 Squad"><Settings2 size={13} /></button>}
      </div>
      {squad && !squad.ready && squad.issues[0] && <div className="note bad xs" style={{ margin: "4px 16px 8px" }}>{squad.issues[0].text}</div>}
      <div className="pane-h">
        <span className="label">Squad</span><span className="mono muted xs">{members.length}</span>
        <div className="grow" />
        {squad && <button className="btn ghost xs icon" onClick={onAdd} title="添加员工" aria-label="添加员工"><UserPlus size={12} /></button>}
        {squad && <a className="btn ghost xs" href={`#/squads/${squad.id}`} title="在 Squads 页管理">Manage</a>}
      </div>
      <div style={{ flex: "none" }}>
        {members.map((a) => {
          const mgr = a.id === squad?.manager_id;
          const m = a.current_task && missions.find((x) => x.id === a.current_task!.id || x.tasks.some((t) => t.id === a.current_task!.id));
          return (
            <div key={a.id} className="squad-item" role="button" tabIndex={0} onClick={() => (m ? onPick(m.id) : go("team", a.id))} title={m ? "打开它正在做的 Mission" : "打开员工详情"}>
              <Avatar name={a.name} mgr={a.is_manager} />
              <span className="who">
                <span className="ellipsis">{a.name}</span>{mgr && <span className="tag acc">MGR</span>}
                <span className="row gap2" style={{ marginLeft: "auto" }} onClick={(e) => e.stopPropagation()}>
                  {!mgr && <button className="btn ghost xs icon sq-act" title="设为主管" aria-label="设为主管" onClick={() => promote.mutate(a.id)}><Crown size={10} /></button>}
                  {!mgr && <button className="btn ghost xs icon sq-act" title="移出 Squad" aria-label="移出 Squad" onClick={() => remove.mutate(a.id)}><UserMinus size={10} /></button>}
                </span>
              </span>
              <span className={`what ellipsis ${a.current_task ? "" : "muted"}`}>
                <AgentStatus agent={a} /> {a.current_task ? `· ${a.current_task.title}` : ""}
              </span>
              {m && <span className="meta"><span style={{ flex: 1 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} /></span></span>}
            </div>
          );
        })}
        {squad && !members.length && <EmptyState small title="还没有成员"><button className="btn sm" onClick={onAdd}><UserPlus size={13} /> Add agent</button></EmptyState>}
        {!squad && <EmptyState small title="先建一个 Squad"><span className="xs">一位主管加几位有技能的员工。</span><button className="btn sm primary" onClick={onNewSquad}>Create squad</button></EmptyState>}
      </div>
      <div className="pane-h sub-h">
        <span className="label">Missions</span>
        <span className="mono muted xs">{missions.length}</span>
        {live.length > 0 && <span className="badge info">{live.length} live</span>}
        <div className="grow" />
        <button className="btn ghost sm icon" onClick={() => onPick("new")} title="New mission" aria-label="New mission"><Plus size={14} /></button>
      </div>
      <div className="rail-filter"><Seg value={scope} onChange={onScope} options={[["squad", "This squad"], ["all", "All squads"]]} /></div>
      <div className="scroll squad-missions">
        {missions.map((m) => (
          <button key={m.id} className="mission-item" aria-current={m.id === current} onClick={() => onPick(m.id)}>
            <span className="row gap6"><span className="t ellipsis grow">{m.name}</span>{m.approvals.length > 0 && <span className="badge acc">Review</span>}</span>
            <span className="row gap6 xs muted"><MissionStage m={m} /><span className="grow" /><span className="mono">{ago(m.created_at)}</span></span>
            {m.progress.total > 0 && <Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} />}
          </button>
        ))}
        {!missions.length && <EmptyState small title="还没有 Mission" icon={<Inbox size={16} />}><span className="xs">在中间写下目标，交给这个 Squad。</span></EmptyState>}
      </div>
    </>
  );
}

function MissionView({ m, d, ws, squad, taskId, onTask, onLaunched, onSquad, mobile, onReport }: {
  m: Mission; d?: TaskDetail; ws: WS; squad?: Squad; taskId: string | null; onTask: (id: string | null) => void; onLaunched: (id: string) => void;
  onSquad: (id: string) => void; mobile: boolean; onReport: () => void;
}) {
  const squads = useSquads(ws.id);
  const owner = squads.data?.find((x) => x.id === m.squad_id);
  const [logs, setLogs] = useState(false);
  void taskId;
  return (
    <>
      <MissionHeader m={m} d={d} compact={mobile} squadName={owner?.name} />
      <div className="scroll">
        {!d ? <Loading rows={8} /> : (
          <>
            <section className="msec">
              <FailureSummary m={m} d={d} onTask={onTask} />
              <div className="msec-h"><h3>Execution</h3><span className="muted xs"><span className="mono">{d.subtasks.length}</span> tasks · <span className="mono">{new Set(d.subtasks.map((s) => s.assigned_agent)).size}</span> agents</span>
                <div className="grow" />
                {mobile && <button className="btn ghost xs" onClick={() => setLogs(!logs)}>{logs ? "Hide logs" : "Logs"}</button>}
              </div>
              <div className="brief"><span className="label">Brief</span>{d.messages.find((x) => x.event === "user.instruction")?.text || m.brief}</div>
              <LiveBoard d={d} onTask={(id) => onTask(id)} />
              <ExecutionFeed d={d} onTask={(id) => onTask(id)} />
              {mobile && logs && <div style={{ marginTop: 10 }}><ExecutionLogs d={d} /></div>}
            </section>
            <section className="msec">
              <div className="msec-h"><h3>Deliverables</h3></div>
              <Deliverables m={m} d={d} onReport={onReport} />
            </section>
          </>
        )}
      </div>
      <TaskComposer ws={ws} squadId={squad?.id} onSquad={onSquad} onSubmitted={(t) => onLaunched(t.id)} />
    </>
  );
}

/** What broke and why, in plain words, before the timeline: one line per failed task with its reason. */
function FailureSummary({ m, d, onTask }: { m: Mission; d: TaskDetail; onTask: (id: string | null) => void }) {
  const name = useAgentName();
  const failed = d.subtasks.filter((s) => s.status === "failed");
  if (!failed.length && m.stage !== "failed") return null;
  const index = new Map(d.subtasks.map((s, i) => [s.id, i + 1]));
  return (
    <div className="fail-sum" role="alert">
      <div className="fail-sum-h"><CircleAlert size={15} /> {failed.length ? `${failed.length} 个任务失败` : "Mission 失败"}
        {failed.length > 0 && m.stage !== "failed" && <span className="muted xs">· 其余任务继续执行，结果里会注明缺失部分</span>}
      </div>
      {failed.map((s) => (
        <div key={s.id} className="fail-item">
          <span className="mono muted">#{index.get(s.id)}</span>
          <button className="task-ref what" onClick={() => onTask(s.id)} title="在 Inspector 中查看">{s.title}</button>
          <span className="muted xs nowrap">{name(s.assigned_agent)} · <span className="mono">{s.skill}</span></span>
          <span className="why">{s.error || "没有返回错误信息，打开 Logs 查看工具输出。"}</span>
        </div>
      ))}
      {!failed.length && <div className="fail-item"><span className="why" style={{ gridColumn: "1 / -1" }}>{m.error || "没有返回错误信息"}</span></div>}
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

/** No mission selected: start one for the squad, or pick from its history. */
function Launcher({ ws, squad, missions, loading, onPick, onSquad, onNewSquad }: {
  ws: WS; squad?: Squad; missions: Mission[]; loading: boolean; onPick: (id: string) => void; onSquad: (id: string) => void; onNewSquad: () => void;
}) {
  return (
    <>
      <div className="toolbar"><h1>New mission</h1><span className="sub">{squad ? `for ${squad.name} · ${ws.name}` : ws.name}</span></div>
      <div className="scroll">
        {!squad ? (
          <EmptyState title="这个工作区还没有 Squad" icon={<Users size={18} />}>
            <span>Mission 交给一个 Squad：主管拆分任务，成员按技能执行并交接结果。</span>
            <button className="btn primary" onClick={onNewSquad}><Users size={13} /> Create squad</button>
          </EmptyState>
        ) : (
          <div className="msec">
            <div className="msec-h"><h3>Recent missions</h3></div>
            {loading ? <Loading rows={4} /> : !missions.length ? <div className="muted sm">还没有 Mission。在下面写下目标，主管会按成员技能拆分。</div> : (
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
        )}
      </div>
      <TaskComposer ws={ws} squadId={squad?.id} onSquad={onSquad} rows={4} autoFocus onSubmitted={(t) => onPick(t.id)} />
    </>
  );
}

/** A workspace is the container; its teams are squads, created right after. */
export function WorkspaceForm({ ws, onClose }: { ws?: WS; onClose: () => void }) {
  const [name, setName] = useState(ws?.name || "");
  const [desc, setDesc] = useState(ws?.description || "");
  const save = useAction(
    () => (ws ? api(`/api/wf/workspaces/${ws.id}`, { method: "PUT", json: { name, description: desc } }) : post("/api/wf/workspaces", { name, description: desc })),
    ["workspaces"],
    ws ? "已保存" : "工作区已建立",
  );
  return (
    <Dialog
      title={ws ? `Workspace · ${ws.name}` : "New workspace"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: (w: any) => (onClose(), !ws && go("board", w.id)) })}>保存</button>
        </>
      }
    >
      <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
      <label className="field"><span>Description</span><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
      {!ws && <div className="note xs">建好后在左栏新建 Squad，指定主管并加入员工，就可以下达 Mission。</div>}
    </Dialog>
  );
}
