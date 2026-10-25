import { LogOut, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Dialog, EmptyState, ErrorState, Loading, Seg, Status, Tabs } from "../components/ui";
import { del, post, put, setToken } from "../lib/api";
import { useAction, useAgents, useMemory, useMeta, useSchedules, useSystem, useWorkspaces } from "../lib/queries";
import { API_BASE } from "../lib/api";
import type { Memory } from "../lib/types";
import { DEMO } from "../lib/demo";
import { fmtTime, go, usePref } from "../lib/util";

type Tab = "schedules" | "memory" | "system";

export default function Settings() {
  const [tab, setTab] = usePref<Tab>("settings-tab", "schedules");
  return (
    <div className="page">
      <div className="toolbar"><h1>Settings</h1></div>
      <Tabs<Tab> value={tab} onChange={setTab} options={[["schedules", "Scheduled missions"], ["memory", "Memory"], ["system", "System"]]} />
      {tab === "schedules" && <Schedules />}
      {tab === "memory" && <MemoryAdmin />}
      {tab === "system" && <System />}
    </div>
  );
}

function Schedules() {
  const q = useSchedules();
  const ws = useWorkspaces();
  const [adding, setAdding] = useState(false);
  const act = useAction(({ id, action }: { id: number; action: string }) => (action === "delete" ? del(`/api/wf/schedules/${id}`) : post(`/api/wf/schedules/${id}/${action}`)), ["schedules", "tasks"], "已更新");
  const wsName = (id: string) => ws.data?.find((w) => w.id === id)?.name || id;
  return (
    <>
      <div className="qab">
        <span className="muted xs">标准 5 段 cron，按 UTC 计算。到点后把目标交给工作区的 AI 主管；连续失败 3 次自动暂停。</span>
        <div className="grow" />
        <button className="btn primary" disabled={!ws.data?.length} onClick={() => setAdding(true)}><Plus size={13} /> New schedule</button>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data!.length ? <EmptyState title="还没有定时任务" /> : (
        <div className="table-wrap">
          <table className="dt">
            <thead><tr><th>Name</th><th>Workspace</th><th>Cron (UTC)</th><th>Next run</th><th>Last run</th><th>Failures</th><th>State</th><th /></tr></thead>
            <tbody>
              {q.data!.map((s) => (
                <tr key={s.id}>
                  <td><div className="medium">{s.name}</div><div className="muted xs ellipsis" style={{ maxWidth: 300 }}>{s.goal}</div></td>
                  <td>{wsName(s.workspace_id)}</td>
                  <td className="mono">{s.cron}</td>
                  <td className="mono muted">{s.enabled ? fmtTime(s.next_run_at) : "—"}</td>
                  <td>
                    {s.last_run_at ? <span className="mono muted">{fmtTime(s.last_run_at)}</span> : "—"}{" "}
                    {s.last_task_id && <a className="mono" href={`#/tasks/${s.last_task_id}`}>{s.last_task_id}</a>}{" "}
                    {s.last_status && <Status status={s.last_status} />}
                  </td>
                  <td className={`mono ${s.failures ? "bad" : ""}`}>{s.failures}</td>
                  <td><Status status={s.enabled ? "completed" : "cancelled"} label={s.enabled ? "Enabled" : "Paused"} /></td>
                  <td className="r">
                    <div className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                      <button className="btn sm" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: "run" })} title="立即运行"><Play size={12} /></button>
                      <button className="btn sm" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: s.enabled ? "disable" : "enable" })}>{s.enabled ? "Pause" : "Resume"}</button>
                      <button className="btn sm ghost" disabled={act.isPending} onClick={() => confirm(`删除定时任务「${s.name}」？`) && act.mutate({ id: s.id, action: "delete" })} aria-label="删除"><Trash2 size={12} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <ScheduleForm onClose={() => setAdding(false)} />}
    </>
  );
}

function ScheduleForm({ onClose }: { onClose: () => void }) {
  const ws = useWorkspaces();
  const [f, setF] = useState({ workspace_id: ws.data?.[0]?.id || "", name: "", cron: "0 * * * *", goal: "" });
  const save = useAction(() => post("/api/wf/schedules", f), ["schedules"], "定时任务已创建");
  return (
    <Dialog
      title="新建定时任务"
      onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.goal.trim() || !f.cron.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>创建</button></>}
    >
      <label className="field"><span>Workspace</span>
        <select className="input" value={f.workspace_id} onChange={(e) => setF({ ...f, workspace_id: e.target.value })}>
          {ws.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </label>
      <div className="grid2">
        <label className="field"><span>Name</span><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field"><span>Cron (UTC)</span><input className="input mono" value={f.cron} onChange={(e) => setF({ ...f, cron: e.target.value })} /></label>
      </div>
      <label className="field"><span>Goal for the AI manager</span><textarea className="input" rows={4} value={f.goal} placeholder="监控 0x… 最近 1000 个区块的 Transfer 事件并出报告" onChange={(e) => setF({ ...f, goal: e.target.value })} /></label>
    </Dialog>
  );
}

function MemoryAdmin() {
  const agents = useAgents();
  const ws = useWorkspaces();
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("");
  const mem = useMemory(q, scope);
  const [edit, setEdit] = useState<Memory | null>(null);
  const [adding, setAdding] = useState(false);
  const forget = useAction((id: number) => del(`/api/wf/memory/${id}`), ["memory"], "已删除");
  const owner = (m: Memory) =>
    m.scope === "agent" ? agents.data?.find((a) => a.id === m.owner)?.name || m.owner : m.scope === "workspace" ? ws.data?.find((w) => w.id === m.owner)?.name || m.owner : m.scope;
  return (
    <>
      <div className="qab">
        <input className="input" style={{ width: 260 }} placeholder="全文搜索记忆（至少 3 个字符）" value={q} onChange={(e) => setQ(e.target.value)} />
        <Seg value={scope} onChange={setScope} options={[["", "All"], ["agent", "Agent"], ["workspace", "Workspace"], ["project", "Project"], ["user", "User"]]} />
        <div className="grow" />
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={13} /> Add memory</button>
      </div>
      <div className="note" style={{ margin: "0 16px 8px" }}>员工只读得到自己的记忆、所在工作区的记忆，以及项目和用户级记忆。检索是关键词全文搜索，还没有向量检索。</div>
      {mem.isLoading ? <Loading /> : mem.error ? <ErrorState error={mem.error} /> : !mem.data!.length ? <EmptyState title="没有记忆" /> : (
        <table className="dt">
          <thead><tr><th>#</th><th>Scope</th><th>Owner</th><th>Text</th><th>Source</th><th>Created</th><th>Expires</th><th /></tr></thead>
          <tbody>
            {mem.data!.map((m) => (
              <tr key={m.id}>
                <td className="mono muted">{m.id}</td>
                <td><span className="tag">{m.scope}</span></td>
                <td>{owner(m)}</td>
                <td className="ellipsis" style={{ maxWidth: 420 }} title={m.text}>{m.text}</td>
                <td className="mono muted xs">{m.source}</td>
                <td className="mono muted">{fmtTime(m.created_at)}</td>
                <td className="mono muted">{m.expires_at ? fmtTime(m.expires_at) : "never"}</td>
                <td className="r">
                  <div className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                    <button className="btn sm" onClick={() => setEdit(m)}>Edit</button>
                    <button className="btn sm ghost" onClick={() => confirm(`删除记忆 #${m.id}？`) && forget.mutate(m.id)} aria-label="删除"><Trash2 size={12} /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(edit || adding) && <MemoryForm m={edit} onClose={() => (setEdit(null), setAdding(false))} />}
    </>
  );
}

function MemoryForm({ m, onClose }: { m: Memory | null; onClose: () => void }) {
  const agents = useAgents();
  const ws = useWorkspaces();
  const [f, setF] = useState({ scope: m?.scope || "project", owner: m?.owner || "", text: m?.text || "", ttl_days: "" });
  const save = useAction(
    () => (m ? put(`/api/wf/memory/${m.id}`, { text: f.text, ttl_days: f.ttl_days ? Number(f.ttl_days) : undefined }) : post("/api/wf/memory", { ...f, ttl_days: f.ttl_days ? Number(f.ttl_days) : undefined })),
    ["memory"],
    "已保存",
  );
  const owners = f.scope === "agent" ? agents.data?.map((a) => [a.id, a.name]) : f.scope === "workspace" ? ws.data?.map((w) => [w.id, w.name]) : null;
  return (
    <Dialog
      title={m ? `编辑记忆 #${m.id}` : "添加记忆"}
      onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.text.trim() || (!!owners && !f.owner) || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>保存</button></>}
    >
      {!m && (
        <div className="grid2">
          <label className="field"><span>Scope</span>
            <select className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value as Memory["scope"], owner: "" })}>
              {["agent", "workspace", "project", "user"].map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          {owners && (
            <label className="field"><span>Owner</span>
              <select className="input" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>
                <option value="">选择…</option>
                {owners.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      <label className="field"><span>Text</span><textarea className="input" rows={5} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} /></label>
      <label className="field"><span>Expires after (days, empty = {m ? "unchanged" : "never"})</span><input className="input mono" inputMode="numeric" value={f.ttl_days} onChange={(e) => setF({ ...f, ttl_days: e.target.value.replace(/\D/g, "") })} /></label>
    </Dialog>
  );
}

function ServerInfo() {
  const q = useSystem();
  if (q.isLoading) return <Loading rows={2} />;
  if (q.error) return <ErrorState error={q.error} />;
  const s = q.data!;
  const live = s.mode === "live";
  return (
    <>
      <div className="label">Server</div>
      <dl className="kv">
        <dt>Mode</dt>
        <dd><span className={`badge ${live ? "ok" : "warn"}`}><span className="dot" />{live ? "Live · 真实后端" : "Demo · 示例快照，不连接服务器"}</span></dd>
        {live && (
          <>
            <dt>Server</dt><dd className="mono">{API_BASE || location.origin}{s.version ? ` · ${s.version}` : ""}</dd>
            <dt>Database</dt>
            <dd><span className={`badge ${s.database.persistent ? "ok" : "warn"}`}><span className="dot" />{s.database.persistent ? `SQLite · ${s.database.file}` : "内存数据库：重启后数据丢失"}</span></dd>
            <dt>Chain writes</dt><dd>{s.writes.payments ? "实盘付款已开启" : "关闭：只读链上数据"} · Agent 不能签名或转账</dd>
          </>
        )}
      </dl>
    </>
  );
}

function System() {
  const meta = useMeta();
  const [density, setDensity] = usePref("density", "compact");
  if (meta.isLoading) return <Loading />;
  if (meta.error) return <ErrorState error={meta.error} />;
  const e = meta.data!.engine;
  const row = (label: string, ok: boolean, on: string, off: string) => (
    <><dt>{label}</dt><dd><span className={`badge ${ok ? "ok" : "warn"}`}><span className="dot" />{ok ? on : off}</span></dd></>
  );
  return (
    <div className="section col" style={{ maxWidth: 720 }}>
      <div className="label">Engine</div>
      <dl className="kv">
        {row("Model", e.llm, `Anthropic · planner ${e.planner}`, "没有 ANTHROPIC_API_KEY：主管用规则拆分，员工不调用模型")}
        {row("Chain", e.chain, `${meta.data!.network} (${meta.data!.chain_id})`, "没有 CHAINSTAFF_CHAIN：链上技能不可用")}
        {row("Etherscan", e.etherscan, "configured", "没有 ETHERSCAN_API_KEY：交易历史技能不可用")}
        {row("Anchor wallet", e.anchor, "BSC testnet signer ready", "没有 CHAINSTAFF_ANCHOR_WALLET：结果哈希不能上链")}
      </dl>
      <ServerInfo />
      <div className="label">Display</div>
      <div className="row"><span className="dim">Table density</span><Seg value={density} onChange={setDensity} options={[["compact", "Compact"], ["detailed", "Detailed"]]} /></div>
      <div className="label">Session</div>
      <div className="row wrap">
        <button className="btn" onClick={() => (setToken(""), go("terminal"))}><LogOut size={13} /> 退出（清除这个标签页的访问令牌）</button>
        {!DEMO && <a className="btn ghost" href="/">Classic console</a>}
      </div>
      <div className="note">当前是单用户模式：一个访问令牌对应一个所有者。多用户登录、钱包签名登录、按用户隔离工作区和角色权限还没有实现。</div>
    </div>
  );
}

