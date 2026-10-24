import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Hash, MoreHorizontal, PanelRightClose, PanelRightOpen, Plus, Send, Users } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApprovalDialog, TaskInspector } from "../components/TaskInspector";
import { TimelineItem } from "../components/Timeline";
import { Dialog, EmptyState, ErrorState, Loading, Sheet, StatusBadge } from "../components/ui";
import { api, post } from "../lib/api";
import { useAction, useAgentName, useAgents, useApprovals, useWorkspaces } from "../lib/queries";
import { useLiveMessages } from "../lib/stream";
import type { Agent, Approval, Message, Workspace as WS } from "../lib/types";
import { errText, go, toast, useIsMobile, usePref, type Route } from "../lib/util";

export default function Workspace({ route }: { route: Route }) {
  const ws = useWorkspaces();
  const [savedId, setSavedId] = usePref<string>("workspace", "");
  const [creating, setCreating] = useState(false);
  const mobile = useIsMobile();
  const id = route.id || savedId || ws.data?.[0]?.id;
  const current = ws.data?.find((w) => w.id === id) || ws.data?.[0];
  useEffect(() => {
    if (current && current.id !== savedId) setSavedId(current.id);
  }, [current, savedId, setSavedId]);

  if (ws.isLoading) return <Loading rows={6} />;
  if (ws.error) return <ErrorState error={ws.error} retry={() => ws.refetch()} />;
  if (!current)
    return (
      <div className="page">
        <EmptyState title="还没有工作区">
          <span>工作区把一个 AI 主管和几个员工放在一起协作。</span>
          <button className="btn primary" onClick={() => setCreating(true)}>
            <Plus size={14} /> 建立工作区
          </button>
        </EmptyState>
        {creating && <WorkspaceForm onClose={() => setCreating(false)} />}
      </div>
    );
  return (
    <>
      {mobile ? <MobileWorkspace ws={current} all={ws.data!} route={route} onCreate={() => setCreating(true)} /> : <DesktopWorkspace ws={current} all={ws.data!} route={route} onCreate={() => setCreating(true)} />}
      {creating && <WorkspaceForm onClose={() => setCreating(false)} />}
    </>
  );
}

/** Timeline data: newest page first, older pages on demand, live entries appended from the stream. */
function useTimeline(wsId: string, taskFilter: string | null) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["timeline", wsId, taskFilter || ""],
    queryFn: () => api<Message[]>(`/api/wf/workspaces/${wsId}/timeline?limit=300${taskFilter ? `&task=${taskFilter}` : ""}`),
    staleTime: Infinity,
  });
  const [older, setOlder] = useState<Message[]>([]);
  const [noMore, setNoMore] = useState(false);
  useEffect(() => (setOlder([]), setNoMore(false)), [wsId, taskFilter]);
  const refetchTimer = useRef<number>();
  useLiveMessages(
    useCallback(
      (m: Message) => {
        if (m.workspace_id !== wsId) return;
        if (!taskFilter) {
          qc.setQueryData<Message[]>(["timeline", wsId, ""], (prev) => (prev && !prev.some((x) => x.id === m.id) ? [...prev, m] : prev));
          // Delivery status (queued → processed) changes without a new event, so once the burst settles,
          // re-read only the rows that were still in flight and merge them in.
          window.clearTimeout(refetchTimer.current);
          refetchTimer.current = window.setTimeout(async () => {
            const cur = qc.getQueryData<Message[]>(["timeline", wsId, ""]) || [];
            const open = cur.filter((x) => x.recipient && (x.status === "queued" || x.status === "delivered"));
            if (!open.length) return;
            const fresh = await api<Message[]>(`/api/wf/workspaces/${wsId}/timeline?after=${open[0].id - 1}&limit=300`).catch(() => null);
            if (!fresh) return;
            const byId = new Map(fresh.map((x) => [x.id, x]));
            qc.setQueryData<Message[]>(["timeline", wsId, ""], (prev) => prev?.map((x) => byId.get(x.id) || x));
          }, 1200);
          return;
        }
        // A task group spans the goal and its subtasks; let the server do the grouping, in one batched refetch.
        window.clearTimeout(refetchTimer.current);
        refetchTimer.current = window.setTimeout(() => qc.invalidateQueries({ queryKey: ["timeline", wsId, taskFilter] }), 400);
      },
      [wsId, taskFilter, qc],
    ),
  );
  const rows = useMemo(() => [...older, ...(q.data || [])], [older, q.data]);
  const loadOlder = async () => {
    const first = rows[0];
    if (!first) return;
    const more = await api<Message[]>(`/api/wf/workspaces/${wsId}/timeline?before=${first.id}&limit=200${taskFilter ? `&task=${taskFilter}` : ""}`);
    if (more.length < 200) setNoMore(true);
    setOlder((o) => [...more, ...o]);
  };
  return { q, rows, loadOlder, noMore: noMore || (q.data?.length || 0) < 300 };
}

function DesktopWorkspace({ ws, all, route, onCreate }: { ws: WS; all: WS[]; route: Route; onCreate: () => void }) {
  const agents = useAgents();
  const name = useAgentName();
  const [inspector, setInspector] = usePref("ws-inspector", true);
  const [left, setLeft] = usePref("ws-left", true);
  const group = route.params.get("task");
  const goals = ws.goals || [];
  const activeGoal = group || goals.find((g) => !["completed", "failed", "cancelled"].includes(g.status))?.id || goals[0]?.id || null;
  const members = useMemo(() => [ws.manager_id, ...ws.members].map((id) => agents.data?.find((a) => a.id === id)).filter(Boolean) as Agent[], [ws, agents.data]);
  const tl = useTimeline(ws.id, group);
  const [approval, setApproval] = useState<Approval | null>(null);
  const approvals = useApprovals("pending");
  const openApproval = (aid: number) => {
    const a = approvals.data?.find((x) => x.id === aid);
    a ? setApproval(a) : toast("这个审批已经处理过了");
  };

  return (
    <>
      <div className="page" style={{ overflow: "hidden" }}>
        <div className="page-head" style={{ minHeight: 44, padding: "6px 12px" }}>
          <button className="btn ghost sm icon" onClick={() => setLeft(!left)} aria-label="切换左栏" title="切换左栏">
            <Hash size={14} />
          </button>
          <h1>{ws.name}</h1>
          <span className="sub">{group ? `· ${goals.find((g) => g.id === group)?.title || group}` : "· all activity"}</span>
          <div className="grow" />
          <span className="row" style={{ gap: 4 }}>
            {members.slice(0, 6).map((a) => (
              <span key={a.id} className={`badge ${a.live === "busy" ? "acc pulse" : a.live === "error" ? "bad" : a.status !== "active" ? "warn" : "ok"}`} title={`${a.name} · ${a.live}`}>
                <span className="dot" />
                {a.name.split(" ")[0]}
              </span>
            ))}
          </span>
          <button className="btn ghost sm icon" onClick={() => setInspector(!inspector)} aria-label="切换执行面板" title="Execution inspector">
            {inspector ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
          </button>
        </div>
        <div className={`ws ${left ? "" : "no-left"}`}>
          {left && (
            <nav className="ws-left" aria-label="工作区导航">
              <h3 className="row sp">
                Workspaces
                <button className="btn ghost sm icon" onClick={onCreate} aria-label="新建工作区"><Plus size={12} /></button>
              </h3>
              {all.map((w) => (
                <button key={w.id} className="item" aria-current={w.id === ws.id} onClick={() => go("workspace", w.id)}>
                  <Hash size={12} />
                  <span className="ellipsis">{w.name}</span>
                </button>
              ))}
              <h3>Channels</h3>
              <button className="item" aria-current={!group} onClick={() => go("workspace", ws.id)}>
                <Hash size={12} /> general
              </button>
              <h3>Task groups</h3>
              {goals.slice(0, 30).map((g) => (
                <button key={g.id} className="item" aria-current={group === g.id} onClick={() => go("workspace", ws.id, { task: g.id })} title={g.title}>
                  <StatusDot s={g.status} />
                  <span className="ellipsis">{g.title}</span>
                </button>
              ))}
              {!goals.length && <div className="muted" style={{ padding: "0 12px", fontSize: 12 }}>提交第一个任务后出现在这里</div>}
              <h3 className="row sp">
                Agents
                <a className="btn ghost sm icon" href="#/workforce" aria-label="管理员工"><Users size={12} /></a>
              </h3>
              {members.map((a) => (
                <button key={a.id} className="item" onClick={() => go("workforce", a.id)}>
                  <StatusDot s={a.live === "busy" ? "running" : a.live === "error" ? "failed" : a.status === "active" ? "completed" : "cancelled"} />
                  <span className="ellipsis">{a.name}</span>
                  {a.is_manager && <span className="tag" style={{ marginLeft: "auto" }}>mgr</span>}
                </button>
              ))}
              <ManageMembers ws={ws} />
            </nav>
          )}
          <div className="ws-center">
            <TimelineView tl={tl} name={name} onApprove={openApproval} onTask={(t) => (goals.some((g) => g.id === t) ? go("workspace", ws.id, { task: t }) : go("tasks", t))} />
            <Composer ws={ws} />
          </div>
        </div>
      </div>
      {inspector && (
        <aside className="ctx" aria-label="Execution inspector">
          {activeGoal ? <TaskInspector id={activeGoal} /> : <WorkspaceSummary ws={ws} members={members} />}
        </aside>
      )}
      {approval && <ApprovalDialog approval={approval} onClose={() => setApproval(null)} />}
    </>
  );
}

function StatusDot({ s }: { s: string }) {
  const c = s === "completed" ? "var(--ok)" : s === "failed" ? "var(--bad)" : s === "review" ? "var(--warn)" : s === "cancelled" ? "var(--muted)" : "var(--accent)";
  return <i style={{ width: 7, height: 7, borderRadius: 4, background: c, flex: "none", display: "inline-block" }} />;
}

function TimelineView({ tl, name, onApprove, onTask }: { tl: ReturnType<typeof useTimeline>; name: (id?: string | null) => string; onApprove: (id: number) => void; onTask: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [tl.rows.length]);
  if (tl.q.isLoading) return <div className="scroll"><Loading rows={8} /></div>;
  if (tl.q.error) return <div className="scroll"><ErrorState error={tl.q.error} retry={() => tl.q.refetch()} /></div>;
  return (
    <div className="scroll" ref={ref} onScroll={(e) => {
      const el = e.currentTarget;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    }}>
      <div className="tl">
        {!tl.noMore && (
          <button className="btn ghost sm" style={{ alignSelf: "center" }} onClick={() => tl.loadOlder().catch((e) => toast(errText(e), true))}>
            <ChevronDown size={12} style={{ transform: "rotate(180deg)" }} /> 更早的记录
          </button>
        )}
        {!tl.rows.length && <EmptyState title="这里还没有消息">在下面输入一个链上分析任务，例如：Analyze this BSC contract 0x…</EmptyState>}
        {tl.rows.map((m) => (
          <TimelineItem key={m.id} m={m} name={name} onApprove={onApprove} onTask={onTask} />
        ))}
      </div>
    </div>
  );
}

function Composer({ ws }: { ws: WS }) {
  const [text, setText] = usePref<string>(`draft:${ws.id}`, "");
  const [mode, setMode] = useState<"task" | "note">("task");
  const [opts, setOpts] = useState(false);
  const [o, setO] = useState({ priority: "2", max_steps: "20", budget_usd: "1", timeout_s: "600" });
  const qc = useQueryClient();
  const send = useAction(
    () =>
      mode === "task"
        ? post(`/api/wf/workspaces/${ws.id}/goals`, { text, priority: Number(o.priority), max_steps: Number(o.max_steps), budget_usd: o.budget_usd, timeout_s: Number(o.timeout_s) })
        : post(`/api/wf/workspaces/${ws.id}/notes`, { text }),
    ["workspaces"],
  );
  const submit = () => {
    if (!text.trim() || send.isPending) return;
    send.mutate(undefined, {
      onSuccess: () => {
        setText("");
        qc.invalidateQueries({ queryKey: ["timeline", ws.id] });
      },
    });
  };
  return (
    <div className="composer">
      {!ws.manager_id && mode === "task" && <div className="warn" style={{ fontSize: 12 }}>这个工作区还没有 AI 主管，任务提交会被拒绝。</div>}
      <textarea
        className="input"
        rows={2}
        placeholder={mode === "task" ? "给主管下达任务，例如：Analyze this BSC contract 0x…（Enter 发送，Shift+Enter 换行）" : "写一条备注，所有人可见，不会启动任务"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        aria-label="消息"
      />
      <div className="row">
        <div className="seg">
          <button aria-pressed={mode === "task"} onClick={() => setMode("task")}>Task → Manager</button>
          <button aria-pressed={mode === "note"} onClick={() => setMode("note")}>Note</button>
        </div>
        {mode === "task" && (
          <button className="btn ghost sm" onClick={() => setOpts(!opts)} aria-expanded={opts}>
            <MoreHorizontal size={12} /> P{o.priority} · {o.max_steps} steps · ${o.budget_usd} · {Number(o.timeout_s) / 60}m
          </button>
        )}
        <div className="grow" />
        <button className="btn primary" onClick={submit} disabled={!text.trim() || send.isPending}>
          <Send size={13} /> {mode === "task" ? "Submit" : "Post"}
        </button>
      </div>
      {opts && mode === "task" && (
        <div className="row wrap" style={{ fontSize: 12 }}>
          <label className="row">Priority
            <select className="input" value={o.priority} onChange={(e) => setO({ ...o, priority: e.target.value })}>
              {[0, 1, 2, 3].map((p) => <option key={p} value={p}>P{p}</option>)}
            </select>
          </label>
          <label className="row">Max steps <input className="input mono" style={{ width: 60 }} value={o.max_steps} onChange={(e) => setO({ ...o, max_steps: e.target.value.replace(/\D/g, "") })} /></label>
          <label className="row">Budget $ <input className="input mono" style={{ width: 70 }} value={o.budget_usd} onChange={(e) => setO({ ...o, budget_usd: e.target.value })} /></label>
          <label className="row">Timeout s <input className="input mono" style={{ width: 80 }} value={o.timeout_s} onChange={(e) => setO({ ...o, timeout_s: e.target.value.replace(/\D/g, "") })} /></label>
        </div>
      )}
    </div>
  );
}

function WorkspaceSummary({ ws, members }: { ws: WS; members: Agent[] }) {
  return (
    <div className="col" style={{ padding: 12 }}>
      <h3>Workspace</h3>
      <div className="dim">{ws.description || "没有描述"}</div>
      <h3>Agents · {members.length}</h3>
      {members.map((a) => (
        <div key={a.id} className="row sp" style={{ fontSize: 12 }}>
          <span>{a.name}</span>
          <StatusBadge kind="agent" status={a.live || a.status} />
        </div>
      ))}
      <div className="note">提交任务后，这里会显示当前任务、分配的员工、依赖、执行步骤、工具调用、成本和审批。</div>
    </div>
  );
}

function ManageMembers({ ws }: { ws: WS }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="item" onClick={() => setOpen(true)} style={{ color: "var(--muted)" }}>
        <Plus size={12} /> 管理成员
      </button>
      {open && <WorkspaceForm ws={ws} onClose={() => setOpen(false)} />}
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
    ["workspaces"],
    ws ? "已保存" : "工作区已建立",
  );
  return (
    <Dialog
      title={ws ? `成员 · ${ws.name}` : "建立工作区"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: (w: any) => (onClose(), !ws && go("workspace", w.id)) })}>
            保存
          </button>
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
        {!managers.length && <small className="warn">还没有标记为 AI Manager 的员工，先去 Workforce 创建一个。</small>}
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

function MobileWorkspace({ ws, all, route, onCreate }: { ws: WS; all: WS[]; route: Route; onCreate: () => void }) {
  const agents = useAgents();
  const name = useAgentName();
  const group = route.params.get("task");
  const tl = useTimeline(ws.id, group);
  const [sheet, setSheet] = useState<"more" | "task" | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const approvals = useApprovals("pending");
  const members = [ws.manager_id, ...ws.members].map((id) => agents.data?.find((a) => a.id === id)).filter(Boolean) as Agent[];
  const busy = members.filter((a) => a.live === "busy").length;
  const goals = ws.goals || [];
  return (
    <div className="ws-mobile page" style={{ overflow: "hidden" }}>
      <div className="page-head" style={{ flexWrap: "nowrap" }}>
        <div className="col grow" style={{ gap: 0 }}>
          <h1 className="ellipsis">{ws.name}</h1>
          <span className="muted" style={{ fontSize: 11 }}>
            {members.length} agents · <span className={busy ? "acc" : ""}>{busy} busy</span>
            {group && ` · ${goals.find((g) => g.id === group)?.title.slice(0, 24) || group}`}
          </span>
        </div>
        {goals[0] && (
          <button className="btn sm" onClick={() => (setTaskId(group || goals[0].id), setSheet("task"))}>Task</button>
        )}
        <button className="btn icon" onClick={() => setSheet("more")} aria-label="更多操作"><MoreHorizontal size={16} /></button>
      </div>
      <div className="ws-center" style={{ flex: 1, minHeight: 0 }}>
        <TimelineView
          tl={tl}
          name={name}
          onApprove={(aid) => {
            const a = approvals.data?.find((x) => x.id === aid);
            a ? setApproval(a) : toast("这个审批已经处理过了");
          }}
          onTask={(t) => (setTaskId(goals.find((g) => g.id === t)?.id || t), setSheet("task"))}
        />
        <Composer ws={ws} />
      </div>
      {sheet === "task" && taskId && (
        <Sheet onClose={() => setSheet(null)}>
          <TaskInspector id={taskId} onClose={() => setSheet(null)} />
        </Sheet>
      )}
      {sheet === "more" && (
        <Sheet onClose={() => setSheet(null)}>
          <div className="col" style={{ padding: 12 }}>
            <h3>Workspaces</h3>
            {all.map((w) => (
              <button key={w.id} className="btn" style={{ justifyContent: "flex-start" }} aria-pressed={w.id === ws.id} onClick={() => (go("workspace", w.id), setSheet(null))}>
                <Hash size={12} /> {w.name}
              </button>
            ))}
            <button className="btn" style={{ justifyContent: "flex-start" }} onClick={() => (setSheet(null), onCreate())}><Plus size={12} /> 新建工作区</button>
            <h3>Task groups</h3>
            <button className="btn" style={{ justifyContent: "flex-start" }} onClick={() => (go("workspace", ws.id), setSheet(null))}># general</button>
            {goals.slice(0, 20).map((g) => (
              <button key={g.id} className="btn" style={{ justifyContent: "flex-start" }} onClick={() => (go("workspace", ws.id, { task: g.id }), setSheet(null))}>
                <StatusDot s={g.status} /> <span className="ellipsis">{g.title}</span>
              </button>
            ))}
            <h3>Agents</h3>
            {members.map((a) => (
              <div key={a.id} className="row sp"><span>{a.name}</span><StatusBadge kind="agent" status={a.live || a.status} /></div>
            ))}
          </div>
        </Sheet>
      )}
      {approval && <ApprovalDialog approval={approval} onClose={() => setApproval(null)} />}
    </div>
  );
}
