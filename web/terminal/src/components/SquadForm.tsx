import { Plus, Search, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { post, put } from "../lib/api";
import { useAction, useAgents, useMeta, useSkills, useWorkspaces } from "../lib/queries";
import type { Agent, Squad } from "../lib/types";
import { AgentForm } from "./AgentForm";
import { AgentStatus } from "./AgentStatus";
import { Avatar, Dialog, EmptyState } from "./ui";

const DEFAULT_POLICY = ["manager_executes", "anchor_results"];

/** What an agent can actually run: a skill it has, whose permissions it also holds. */
export function runnable(a: Agent, perms: Record<string, string[]>) {
  return a.skills.filter((s) => (perms[s] || []).every((p) => a.permissions.includes(p)));
}

/**
 * Create or edit a squad: name, manager, members, policy. While you pick, it shows what the
 * team can do (skills, roles, who is off duty), so a squad is never saved as just a name.
 */
export function SquadForm({ squad, workspaceId, onClose, onSaved }: { squad?: Squad; workspaceId?: string; onClose: () => void; onSaved?: (s: Squad) => void }) {
  const agents = useAgents();
  const meta = useMeta();
  const skills = useSkills();
  const ws = useWorkspaces();
  const spaces = (ws.data || []).filter((w) => !w.archived);
  const [wsId, setWsId] = useState(squad?.workspace_id || workspaceId || spaces[0]?.id || "");
  const [newWs, setNewWs] = useState("");
  const [name, setName] = useState(squad?.name || "");
  const [desc, setDesc] = useState(squad?.description || "");
  const [manager, setManager] = useState(squad?.manager_id || "");
  const [members, setMembers] = useState<string[]>(squad?.members || []);
  const [policy, setPolicy] = useState<string[]>(squad?.permissions || DEFAULT_POLICY);
  const [parallel, setParallel] = useState(String(squad?.max_parallel ?? 0));
  const [creatingAgent, setCreatingAgent] = useState(false);
  const roster = (agents.data || []).filter((a) => a.status !== "archived");
  const managers = [...roster].sort((a, b) => Number(b.is_manager) - Number(a.is_manager));
  const perms = useMemo(() => Object.fromEntries((meta.data?.skills || []).map((s) => [s.id, s.permissions])), [meta.data]);
  const skillName = (s: string) => skills.data?.find((x) => x.id === s)?.name || s;
  const team = roster.filter((a) => members.includes(a.id) || (policy.includes("manager_executes") && a.id === manager));
  const covered = [...new Set(team.filter((a) => a.status === "active").flatMap((a) => runnable(a, perms)))];
  const off = team.filter((a) => a.status !== "active");
  const save = useAction(
    async () => {
      let wid = wsId;
      if (!squad && !wid) wid = (await post<{ id: string }>("/api/wf/workspaces", { name: newWs.trim() || "Main" })).id;
      const body = { name, description: desc, manager_id: manager || null, members: members.filter((m) => m !== manager), permissions: policy, max_parallel: Number(parallel) || 0 };
      return squad ? put<Squad>(`/api/wf/squads/${squad.id}`, body) : post<Squad>("/api/wf/squads", { ...body, workspace_id: wid });
    },
    ["squads", "workspaces", "agents", "preflight"],
    squad ? "Squad 已保存" : "Squad 已建立",
  );
  const toggle = (id: string) => setMembers((m) => (m.includes(id) ? m.filter((x) => x !== id) : [...m, id]));
  return (
    <Dialog
      wide
      title={squad ? `Edit squad · ${squad.name}` : "Create squad"}
      onClose={onClose}
      footer={
        <>
          <span className="muted xs grow">{team.length} agents · {covered.length} runnable skills</span>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: (s) => (onSaved?.(s as Squad), onClose()) })}>
            {squad ? "保存" : "创建 Squad"}
          </button>
        </>
      }
    >
      <div className="grid2">
        <label className="field"><span>Squad name *</span><input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} autoFocus placeholder="BSC Research Desk" /></label>
        {!squad && (spaces.length ? (
          <label className="field"><span>Workspace</span>
            <select className="input" value={wsId} onChange={(e) => setWsId(e.target.value)}>{spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
          </label>
        ) : (
          <label className="field"><span>Workspace（会一起建立）</span><input className="input" value={newWs} onChange={(e) => setNewWs(e.target.value)} placeholder="Main" /></label>
        ))}
      </div>
      <label className="field"><span>Description</span><input className="input" value={desc} maxLength={500} onChange={(e) => setDesc(e.target.value)} placeholder="这个团队负责什么" /></label>
      <label className="field">
        <span>Manager agent</span>
        <select className="input" value={manager} onChange={(e) => (setManager(e.target.value), setMembers((m) => m.filter((x) => x !== e.target.value)))}>
          <option value="">— 选择主管 —</option>
          {managers.map((a) => <option key={a.id} value={a.id}>{a.name}{a.is_manager ? " · AI Manager" : ""}{a.status !== "active" ? ` (${a.status})` : ""}</option>)}
        </select>
        <small>主管接收 Mission、按成员技能拆分任务、汇总结果。{policy.includes("manager_executes") ? "主管有对应技能时也会亲自执行。" : ""}</small>
      </label>
      <div className="field">
        <span className="row sp">
          <span>Member agents · {members.filter((m) => m !== manager).length}</span>
          <button type="button" className="btn ghost xs" onClick={() => setCreatingAgent(true)}><Plus size={11} /> Create agent</button>
        </span>
        {!roster.length ? (
          <div className="note">还没有员工。<button type="button" className="btn ghost xs" onClick={() => setCreatingAgent(true)}>创建第一个员工</button></div>
        ) : (
          <div className="pick-list">
            {roster.filter((a) => a.id !== manager).map((a) => {
              const on = members.includes(a.id);
              const r = runnable(a, perms);
              return (
                <button key={a.id} type="button" className="pick-row" aria-pressed={on} onClick={() => toggle(a.id)}>
                  <input type="checkbox" readOnly checked={on} tabIndex={-1} />
                  <Avatar name={a.name} mgr={a.is_manager} />
                  <span className="strong ellipsis" style={{ width: 120 }}>{a.name}</span>
                  <span className="muted ellipsis" style={{ width: 100 }}>{a.role || "—"}</span>
                  <AgentStatus agent={a} />
                  <span className="row gap4 grow ellipsis">{r.length ? r.slice(0, 3).map((s) => <span key={s} className="tag">{skillName(s)}</span>) : <span className="warn xs">没有可用技能</span>}{r.length > 3 && <span className="muted xs">+{r.length - 3}</span>}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      <div className="cap-grid">
        <div>
          <span className="label">Available skills</span>
          <div className="chips">{covered.length ? covered.map((s) => <span key={s} className="tag">{skillName(s)}</span>) : <span className="warn xs">没有成员能执行任何技能，Mission 会被拒绝</span>}</div>
        </div>
        <div>
          <span className="label">Agent roles</span>
          <div className="xs dim">{[...new Set(team.map((a) => a.role || "—"))].join(" · ") || "—"}</div>
        </div>
        <div>
          <span className="label">Execution</span>
          <div className="xs dim">
            {(() => {
              const m = roster.find((a) => a.id === manager);
              if (!m) return <span className="warn">没有主管，不能接 Mission</span>;
              return m.provider === "anthropic" && meta.data?.engine.llm ? "模型规划，能理解任意目标" : "规则规划：只处理带 0x 地址的链上分析";
            })()}
            {off.length > 0 && <div className="warn">{off.map((a) => a.name).join("、")} 不在岗</div>}
          </div>
        </div>
      </div>
      <div className="field">
        <span>Permissions</span>
        {Object.entries(meta.data?.squad_permissions || {}).map(([p, d]) => (
          <label key={p} className="check">
            <input type="checkbox" checked={policy.includes(p)} onChange={() => setPolicy((x) => (x.includes(p) ? x.filter((y) => y !== p) : [...x, p]))} />
            <span className="mono xs">{p}</span><span className="muted xs">{d}</span>
          </label>
        ))}
      </div>
      <label className="field" style={{ maxWidth: 220 }}>
        <span>Max parallel tasks (0 = no cap)</span>
        <input className="input mono" inputMode="numeric" value={parallel} onChange={(e) => setParallel(e.target.value.replace(/\D/g, ""))} />
        <small>每个员工另有自己的并发上限（默认 2）。</small>
      </label>
      {creatingAgent && <AgentForm onClose={() => setCreatingAgent(false)} onSaved={(a) => setMembers((m) => [...m, a.id])} />}
    </Dialog>
  );
}

/** Add agents to a squad: search, add, or create one and add it in one go. */
export function AddMemberDialog({ squad, onClose }: { squad: Squad; onClose: () => void }) {
  const agents = useAgents();
  const meta = useMeta();
  const skills = useSkills();
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const perms = useMemo(() => Object.fromEntries((meta.data?.skills || []).map((s) => [s.id, s.permissions])), [meta.data]);
  const add = useAction((id: string) => post(`/api/wf/squads/${squad.id}/members`, { agent_id: id }), ["squads", "agents", "workspaces", "preflight"], "已加入 Squad");
  const inSquad = new Set([squad.manager_id, ...squad.members]);
  const rows = (agents.data || []).filter((a) => a.status !== "archived" && !inSquad.has(a.id) && (!q.trim() || `${a.name} ${a.role} ${a.skills.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase())));
  const skillName = (s: string) => skills.data?.find((x) => x.id === s)?.name || s;
  return (
    <Dialog title={`Add agents · ${squad.name}`} onClose={onClose} wide footer={<><button className="btn" onClick={() => setCreating(true)}><Plus size={12} /> Create agent</button><div className="grow" /><button className="btn primary" onClick={onClose}>完成</button></>}>
      <div className="search"><Search size={13} /><input className="input" placeholder="名称、角色、技能" value={q} onChange={(e) => setQ(e.target.value)} autoFocus /></div>
      {!rows.length ? (
        <EmptyState title={agents.data?.length ? "所有员工都已在这个 Squad 里" : "还没有员工"}>
          <button className="btn primary" onClick={() => setCreating(true)}><Plus size={12} /> Create agent</button>
        </EmptyState>
      ) : (
        <div className="pick-list">
          {rows.map((a) => {
            const r = runnable(a, perms);
            return (
              <div key={a.id} className="pick-row">
                <Avatar name={a.name} mgr={a.is_manager} />
                <span className="strong ellipsis" style={{ width: 120 }}>{a.name}</span>
                <span className="muted ellipsis" style={{ width: 100 }}>{a.role || "—"}</span>
                <AgentStatus agent={a} />
                <span className="row gap4 grow ellipsis">{r.length ? r.slice(0, 3).map((s) => <span key={s} className="tag">{skillName(s)}</span>) : <span className="warn xs">没有可用技能</span>}</span>
                <button className="btn sm" disabled={add.isPending} onClick={() => add.mutate(a.id)}><UserPlus size={12} /> Add</button>
              </div>
            );
          })}
        </div>
      )}
      {creating && <AgentForm onClose={() => setCreating(false)} onSaved={(a) => add.mutate(a.id)} />}
    </Dialog>
  );
}
