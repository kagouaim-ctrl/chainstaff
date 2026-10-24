import { ArrowLeft, Crown, GripVertical, Pause, Pencil, Play, Plus, Send, Trash2, UserMinus, UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { AgentStatus } from "../components/AgentStatus";
import { MissionStage } from "../components/Mission";
import { AddMemberDialog, runnable, SquadForm } from "../components/SquadForm";
import { NewMissionDialog } from "../components/TaskComposer";
import { Avatar, EmptyState, ErrorState, Loading, Progress } from "../components/ui";
import { del, post, put } from "../lib/api";
import { useAction, useAgents, useMeta, useMissions, useSkills, useSquads } from "../lib/queries";
import type { Agent, Squad } from "../lib/types";
import { ago, go, pct, usd, useIsMobile, type Route } from "../lib/util";

/**
 * Squad management: the list on the left, the team in the middle, the squad's numbers and
 * policy on the right. Membership changes here are real: they decide who missions go to.
 *   #/squads/<squad>
 */
export default function Squads({ route }: { route: Route }) {
  const squads = useSquads();
  const mobile = useIsMobile();
  const [form, setForm] = useState<Squad | "new" | null>(null);
  const list = squads.data || [];
  const current = list.find((s) => s.id === route.id) || (mobile ? undefined : list[0]);
  if (squads.isLoading) return <div className="page"><Loading rows={8} /></div>;
  if (squads.error) return <div className="page"><ErrorState error={squads.error} retry={() => squads.refetch()} /></div>;
  const dialogs = form && <SquadForm squad={form === "new" ? undefined : form} onClose={() => setForm(null)} onSaved={(s) => go("squads", s.id)} />;
  if (!list.length)
    return (
      <div className="page">
        <div className="toolbar"><h1>Squads</h1></div>
        <EmptyState title="Create your first squad">
          <span>Squad 是一组一起接 Mission 的 AI 员工：一位主管负责拆分和分派，成员按技能执行。</span>
          <button className="btn primary" onClick={() => setForm("new")}><Plus size={13} /> Create squad</button>
        </EmptyState>
        {dialogs}
      </div>
    );
  if (mobile)
    return (
      <div className="page">
        {current ? (
          <>
            <div className="toolbar"><button className="btn ghost sm icon" onClick={() => go("squads")} aria-label="返回"><ArrowLeft size={14} /></button><h1 className="ellipsis">{current.name}</h1></div>
            <SquadInspector s={current} onEdit={() => setForm(current)} />
            <SquadMembers s={current} />
          </>
        ) : (
          <>
            <div className="toolbar"><h1>Squads</h1><div className="grow" /><button className="btn primary sm" onClick={() => setForm("new")}><Plus size={12} /> New</button></div>
            <SquadList list={list} current={undefined} />
          </>
        )}
        {dialogs}
      </div>
    );
  return (
    <div className="term">
      <aside className="pane rail" aria-label="Squad list" style={{ display: "flex" }}>
        <div className="pane-h"><span className="label">Squads</span><span className="mono muted xs">{list.length}</span><div className="grow" /><button className="btn ghost xs" onClick={() => setForm("new")}><Plus size={11} /> New</button></div>
        <div className="scroll"><SquadList list={list} current={current?.id} /></div>
      </aside>
      <section className="pane center" aria-label="Squad members">{current && <SquadMembers s={current} />}</section>
      <aside className="pane insp" aria-label="Squad inspector">{current && <SquadInspector s={current} onEdit={() => setForm(current)} />}</aside>
      {dialogs}
    </div>
  );
}

function SquadList({ list, current }: { list: Squad[]; current?: string }) {
  const byWs = useMemo(() => {
    const m = new Map<string, Squad[]>();
    list.forEach((s) => m.set(s.workspace_name, [...(m.get(s.workspace_name) || []), s]));
    return [...m.entries()];
  }, [list]);
  return (
    <>
      {byWs.map(([ws, rows]) => (
        <div key={ws}>
          <div className="group-h">{ws}</div>
          {rows.map((s) => (
            <button key={s.id} className="mission-item" aria-current={s.id === current} onClick={() => go("squads", s.id)}>
              <span className="row gap6">
                <span className={`ind ${s.status === "paused" ? "" : s.ready ? "ok" : "bad"}`} title={s.status === "paused" ? "已暂停" : s.ready ? "可以接 Mission" : s.issues[0]?.text} />
                <span className="t ellipsis grow">{s.name}</span>
                {s.status === "paused" && <span className="badge line">Paused</span>}
                {s.missions.review > 0 && <span className="badge acc">Review {s.missions.review}</span>}
              </span>
              <span className="row gap6 xs muted">
                <Users size={11} /> {s.members.length + (s.manager_id ? 1 : 0)}
                <span>·</span><span>{s.missions.active} active</span>
                <span>·</span><span>{s.missions.completed} done</span>
                <span className="grow" /><span className="mono">{s.running ? `${s.running} running` : ""}</span>
              </span>
            </button>
          ))}
        </div>
      ))}
    </>
  );
}

/** The team: manager first, then members; each row says what the agent can run and what it is doing. Agents below can be dragged in. */
function SquadMembers({ s }: { s: Squad }) {
  const agents = useAgents();
  const meta = useMeta();
  const skills = useSkills();
  const [adding, setAdding] = useState(false);
  const [over, setOver] = useState(false);
  const perms = useMemo(() => Object.fromEntries((meta.data?.skills || []).map((x) => [x.id, x.permissions])), [meta.data]);
  const skillName = (id: string) => skills.data?.find((x) => x.id === id)?.name || id;
  const keys = ["squads", "agents", "workspaces", "preflight"];
  const add = useAction((id: string) => post(`/api/wf/squads/${s.id}/members`, { agent_id: id }), keys, "已加入 Squad");
  const remove = useAction((id: string) => del(`/api/wf/squads/${s.id}/members/${id}`), keys, "已移出 Squad");
  const promote = useAction((id: string) => post(`/api/wf/squads/${s.id}/manager`, { agent_id: id }), keys, "主管已更换");
  const all = agents.data || [];
  const team = [s.manager_id, ...s.members].map((id) => all.find((a) => a.id === id)).filter(Boolean) as Agent[];
  const outside = all.filter((a) => a.status !== "archived" && !team.some((t) => t.id === a.id));
  return (
    <>
      <div className="toolbar">
        <h1 className="ellipsis">{s.name}</h1>
        <span className="sub ellipsis hide-m2">{s.description || s.workspace_name}</span>
        <div className="grow" />
        <button className="btn sm" onClick={() => setAdding(true)}><UserPlus size={12} /> Add agent</button>
      </div>
      <div
        className={`scroll drop ${over ? "over" : ""}`}
        onDragOver={(e) => (e.preventDefault(), setOver(true))}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const id = e.dataTransfer.getData("text/agent");
          if (id) add.mutate(id);
        }}
      >
        <table className="dt">
          <thead><tr><th>Agent</th><th>Status</th><th>Current task</th><th>Runnable skills</th><th className="r">Load</th><th className="r">Success</th><th className="r">Cost</th><th /></tr></thead>
          <tbody>
            {team.map((a) => {
              const mgr = a.id === s.manager_id;
              const r = runnable(a, perms);
              const executes = !mgr || s.permissions.includes("manager_executes");
              return (
                <tr key={a.id} className="click" onClick={() => go("agents", a.id)}>
                  <td><span className="row gap6"><Avatar name={a.name} mgr={a.is_manager} /><span className="col" style={{ minWidth: 0 }}><span className="strong ellipsis">{a.name}</span><span className="muted xs ellipsis">{mgr ? <span className="acc">Manager</span> : "Member"} · {a.role || "—"}</span></span></span></td>
                  <td><AgentStatus agent={a} /></td>
                  <td className="ellipsis" style={{ maxWidth: 180 }}>{a.current_task ? <span className="dim">{a.current_task.title}</span> : <span className="muted">—</span>}</td>
                  <td>
                    <span className="row gap4">
                      {!executes || (mgr && !r.length) ? <span className="muted xs">只负责拆分和汇总</span> : r.length ? r.slice(0, 2).map((x) => <span key={x} className="tag">{skillName(x)}</span>) : <span className="warn xs">没有可用技能</span>}
                      {executes && r.length > 2 && <span className="muted xs">+{r.length - 2}</span>}
                    </span>
                  </td>
                  <td className="r mono">{a.running ?? 0}/{a.max_concurrency ?? 2}</td>
                  <td className="r mono">{pct(a.success_rate)}</td>
                  <td className="r mono">{usd(a.cost_usd)}</td>
                  <td className="r" onClick={(e) => e.stopPropagation()}>
                    <span className="acts">
                      {!mgr && <button className="btn ghost xs icon" title="设为主管" aria-label="设为主管" onClick={() => promote.mutate(a.id)}><Crown size={11} /></button>}
                      {!mgr && <button className="btn ghost xs icon" title="移出 Squad" aria-label="移出" onClick={() => remove.mutate(a.id)}><UserMinus size={11} /></button>}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!team.length && <EmptyState title="这个 Squad 还没有成员"><button className="btn primary" onClick={() => setAdding(true)}><UserPlus size={12} /> Add agent</button></EmptyState>}
        {!s.manager_id && team.length > 0 && <div className="note bad" style={{ margin: 12 }}>还没有主管：在上面的成员里点 Manager 指定一位，或编辑 Squad。</div>}
        <div className="group-h" style={{ marginTop: 8 }}>Other agents · 拖进上面的表格或点 Add</div>
        <div className="m-list">
          {outside.map((a) => (
            <div key={a.id} className="pick-row" draggable onDragStart={(e) => e.dataTransfer.setData("text/agent", a.id)}>
              <GripVertical size={12} className="muted d-only" />
              <Avatar name={a.name} mgr={a.is_manager} />
              <span className="strong ellipsis" style={{ width: 130 }}>{a.name}</span>
              <AgentStatus agent={a} />
              <span className="muted xs ellipsis grow">{runnable(a, perms).map(skillName).join(", ") || "没有可用技能"}</span>
              <button className="btn ghost xs" onClick={() => add.mutate(a.id)}><Plus size={11} /> Add</button>
            </div>
          ))}
          {!outside.length && <div className="muted xs" style={{ padding: "6px 12px" }}>所有员工都在这个 Squad 里。</div>}
        </div>
      </div>
      {adding && <AddMemberDialog squad={s} onClose={() => setAdding(false)} />}
    </>
  );
}

function SquadInspector({ s, onEdit }: { s: Squad; onEdit: () => void }) {
  const agents = useAgents();
  const meta = useMeta();
  const skills = useSkills();
  const missions = useMissions({ squad: s.id, limit: 20 });
  const [launch, setLaunch] = useState(false);
  const keys = ["squads", "workspaces", "preflight", "missions"];
  const status = useAction((v: string) => post(`/api/wf/squads/${s.id}/status`, { status: v }), keys);
  const remove = useAction(() => del(`/api/wf/squads/${s.id}`), keys, "Squad 已删除");
  const policy = useAction((p: string[]) => put(`/api/wf/squads/${s.id}`, { permissions: p }), keys, "已更新");
  const mgr = agents.data?.find((a) => a.id === s.manager_id);
  const done = s.missions.completed;
  return (
    <>
      <div className="insp-h">
        <div className="row gap6">
          <h2 className="ellipsis grow">{s.name}</h2>
          {s.status === "paused" ? <span className="badge line">Paused</span> : s.ready ? <span className="badge ok"><span className="dot" />Ready</span> : <span className="badge bad"><span className="dot" />Not ready</span>}
        </div>
        <div className="muted xs">{s.workspace_name} · <span className="mono">{s.id}</span></div>
        <div className="row gap6 wrap">
          <button className="btn primary sm" disabled={!s.ready || s.status === "paused"} onClick={() => setLaunch(true)} title={s.ready ? "" : s.issues[0]?.text}><Send size={12} /> New mission</button>
          {s.status === "paused" ? (
            <button className="btn sm" onClick={() => status.mutate("active")}><Play size={12} /> Resume</button>
          ) : (
            <button className="btn sm" onClick={() => status.mutate("paused")} title="暂停：进行中的 Mission 不再派新任务"><Pause size={12} /> Pause</button>
          )}
          <button className="btn ghost sm" onClick={onEdit}><Pencil size={12} /> Edit</button>
          <div className="grow" />
          <button className="btn ghost sm icon danger" title="删除 Squad" aria-label="删除" onClick={() => confirm(`删除 Squad ${s.name}？员工不会被删除。`) && remove.mutate(undefined, { onSuccess: () => go("squads") })}><Trash2 size={12} /></button>
        </div>
      </div>
      <div className="metrics">
        <div><span className="label">Active</span><span className="v">{s.missions.active + s.missions.review}</span></div>
        <div><span className="label">Done</span><span className="v">{done}</span></div>
        <div><span className="label">Success</span><span className="v">{pct(s.success_rate)}</span></div>
        <div><span className="label">Cost</span><span className="v">{usd(s.cost_usd)}</span></div>
      </div>
      <div className="insp-b">
        {s.issues.length > 0 && (
          <div className="insp-sec">
            <span className="label bad">Needs attention</span>
            {s.issues.map((c) => <div key={c.key} className="note bad xs">{c.text}</div>)}
          </div>
        )}
        <div className="insp-sec">
          <dl className="kv">
            <dt>Manager</dt><dd>{mgr ? <a className="link" href={`#/agents/${mgr.id}`}>{mgr.name}</a> : <span className="bad">未指定</span>}</dd>
            <dt>Members</dt><dd>{s.members.length}</dd>
            <dt>Can execute</dt><dd>{s.executors.length} agents</dd>
            <dt>Running now</dt><dd className="mono">{s.running}{s.max_parallel ? ` / ${s.max_parallel}` : ""}</dd>
            <dt>Failed</dt><dd className="mono">{s.missions.failed}</dd>
            <dt>Created</dt><dd>{ago(s.created_at)} ago</dd>
          </dl>
        </div>
        <div className="insp-sec">
          <span className="label">Available skills</span>
          <div className="chips">{s.skills.length ? s.skills.map((x) => <span key={x} className="tag">{skills.data?.find((k) => k.id === x)?.name || x}</span>) : <span className="muted xs">—</span>}</div>
        </div>
        <div className="insp-sec">
          <span className="label">Permissions</span>
          {Object.entries(meta.data?.squad_permissions || {}).map(([p, d]) => (
            <label key={p} className="check">
              <input type="checkbox" checked={s.permissions.includes(p)} disabled={policy.isPending}
                onChange={() => policy.mutate(s.permissions.includes(p) ? s.permissions.filter((x) => x !== p) : [...s.permissions, p])} />
              <span className="col gap2"><span className="mono xs">{p}</span><span className="muted xs">{d}</span></span>
            </label>
          ))}
        </div>
        <div className="insp-sec">
          <span className="label">Missions</span>
          {(missions.data || []).map((m) => (
            <button key={m.id} className="list-row click" style={{ background: "none", border: 0, width: "100%", textAlign: "left", color: "inherit" }} onClick={() => go("workspace", m.workspace_id, { s: s.id, m: m.id })}>
              <span className="ellipsis grow">{m.name}</span>
              <MissionStage m={m} />
              <span style={{ width: 70 }}><Progress done={m.progress.completed} failed={m.progress.failed} total={m.progress.total} compact /></span>
            </button>
          ))}
          {!missions.data?.length && <span className="muted xs">还没有 Mission</span>}
        </div>
      </div>
      {launch && <NewMissionDialog workspaceId={s.workspace_id} squadId={s.id} onClose={() => setLaunch(false)} />}
    </>
  );
}
