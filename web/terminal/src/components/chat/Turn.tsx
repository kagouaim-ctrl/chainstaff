import { AlertCircle, Check, ChevronRight, CircleStop, FileText, Loader2, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { post } from "../../lib/api";
import { refreshAt, TASK_ST, useAtAgents } from "../../lib/at";
import { isOpen, type ChatDetail, type ChatMessage, type SubTask } from "../../lib/chat";
import { DEMO } from "../../lib/demo";
import { ago, errText, fmtTime, toast } from "../../lib/util";
import { ApprovalMessage } from "../Approval";
import { AssignBox, TaskState } from "../AtBits";
import { Markdown } from "../Markdown";
import { BrandMark } from "../TerminalHeader";
import { WorkerAvatar } from "../WorkerAvatar";
import { KIND_LABEL } from "./Composer";

/**
 * One exchange: what the person asked, then KAGOU's answer, which is the task's real state. Running work shows the
 * agents and the steps actually recorded; a finished task shows its result; nothing here is simulated.
 */
export function Turn({ m, chat, onTask }: { m: ChatMessage; chat: ChatDetail; onTask: (id: string) => void }) {
  return (
    <div className="turn">
      <div className="msg user">
        <div className="bubble">{m.text}</div>
        <div className="msg-meta">{m.kind !== "general" && <span className="tag">{KIND_LABEL[m.kind] || m.kind}</span>}<span title={fmtTime(m.at)}>{ago(m.at) === "now" ? "刚刚" : ago(m.at)}</span></div>
      </div>
      <Answer m={m} chat={chat} onTask={onTask} />
    </div>
  );
}

function Answer({ m, chat, onTask }: { m: ChatMessage; chat: ChatDetail; onTask: (id: string) => void }) {
  const agents = useAtAgents(true);
  const name = (id?: string | null) => (id ? chat.names[id] || agents.data?.find((a) => a.id === id)?.name || id : "KAGOU");
  const look = (id?: string | null) => agents.data?.find((a) => a.id === id)?.appearance;
  const t = m.task;
  if (!t) {
    return (
      <div className="msg bot">
        <span className="msg-av"><BrandMark size={28} /></span>
        <div className="msg-body">
          <div className="msg-head"><b>KAGOU</b></div>
          <div className="note bad row gap8"><AlertCircle size={15} style={{ flex: "none", marginTop: 2 }} /><span>没能开始这个任务：{m.error || "未知原因"}</span></div>
        </div>
      </div>
    );
  }
  const who = t.to_agent_id;
  const open = isOpen(m);
  const pending = m.approvals.filter((a) => a.status === "pending");
  return (
    <div className="msg bot">
      <span className="msg-av">{who ? <WorkerAvatar id={who} appearance={look(who)} size={30} /> : <BrandMark size={28} />}</span>
      <div className="msg-body">
        <div className="msg-head">
          <b>{who ? name(who) : "KAGOU"}</b>
          <TaskState status={t.status} />
          {open && t.started_at && <span className="muted xs">已用时 {ago(t.started_at)}</span>}
        </div>
        <Routing m={m} name={name} />
        <Progress m={m} name={name} />
        {t.status === "unassigned" && <AssignBox id={t.id} />}
        {pending.map((a) => <ApprovalMessage key={a.id} a={a} />)}
        <Result m={m} />
        {m.approvals.filter((a) => a.status !== "pending").map((a) => <ApprovalMessage key={a.id} a={a} compact />)}
        <Actions m={m} onTask={onTask} />
      </div>
    </div>
  );
}

function Routing({ m, name }: { m: ChatMessage; name: (id?: string | null) => string }) {
  const [why, setWhy] = useState(false);
  const t = m.task!;
  const r = m.routing;
  if (t.deduplicated) return <p className="msg-line">同样的任务已经在处理中，KAGOU 没有重复创建，下面是那个任务的进展。</p>;
  if (!r?.auto) return null;
  const c = r.considered || [];
  return (
    <div className="msg-line">
      {t.to_agent_id && r.agent_id ? <>KAGOU 按职责和权限把任务交给了 <b>{name(r.agent_id)}</b>。</> : <>没有 Agent 同时具备这个工作区的权限、所需权限和相关职责。</>}
      {!!c.length && <button className="plain-btn link xs" onClick={() => setWhy(!why)}>{why ? "收起" : "为什么"}</button>}
      {why && (
        <ul className="why">
          {c.map((x) => <li key={x.agent_id}><span className={x.eligible ? "ok" : "muted"}>{x.eligible ? <Check size={11} /> : "·"}</span> {x.name}{x.eligible ? `（相关度 ${x.relevance}，在做 ${x.load} 个任务）` : `：${x.reason}`}</li>)}
        </ul>
      )}
    </div>
  );
}

const STATUS_LINE: Partial<Record<string, string>> = {
  queued: "已排队，等待 Worker 唤醒 Agent。关掉页面也会继续执行。",
  waiting: "已把部分工作交给其他 Agent，等待它们的结果。",
  waiting_approval: "产出了需要你确认的修改。",
};

function Progress({ m, name }: { m: ChatMessage; name: (id?: string | null) => string }) {
  const t = m.task!;
  if (!isOpen(m) && !m.tree.length) return null;
  const line = t.status === "running" ? (m.steps.length ? null : "正在处理…") : STATUS_LINE[t.status];
  return (
    <div className="prog">
      {line && <p className="msg-line muted">{line}</p>}
      {t.status === "running" && !!m.steps.length && (
        <ul className="prog-steps">
          {m.steps.map((s, i) => (
            <li key={i}>
              {i === m.steps.length - 1 ? <Loader2 size={12} className="spin" /> : s.ok ? <Check size={12} className="ok" /> : <AlertCircle size={12} className="bad" />}
              <span>{s.kind === "model" ? "思考" : s.kind === "tool" ? `调用 ${s.name}` : s.name}</span>
            </li>
          ))}
        </ul>
      )}
      {!!m.tree.length && (
        <ul className="prog-tree">
          {m.tree.map((x) => <SubRow key={x.id} x={x} name={name} />)}
        </ul>
      )}
    </div>
  );
}

function SubRow({ x, name }: { x: SubTask; name: (id?: string | null) => string }) {
  const [l, tone, live] = TASK_ST[x.status] || [x.status, ""];
  return (
    <li className={`sub ${tone}`} style={{ marginLeft: Math.max(0, x.depth - 1) * 14 }}>
      <i className={`ind ${tone === "info" ? "info" : tone === "ok" ? "ok" : tone === "bad" ? "bad" : tone === "acc" ? "warn" : ""} ${live ? "live" : ""}`} />
      <span className="col gap2 grow" style={{ minWidth: 0 }}>
        <span><b>{name(x.to_agent_id)}</b> <span className="muted">{l === "Running" ? "正在" : ""}{KIND_LABEL[x.kind] || x.kind}</span> · <span className="sub-obj">{x.objective}</span></span>
        {x.summary && x.status === "completed" && <span className="muted xs clamp2">{x.summary}</span>}
        {x.error && <span className="bad xs clamp2">{x.error}</span>}
      </span>
    </li>
  );
}

function Result({ m }: { m: ChatMessage }) {
  const t = m.task!;
  const r = t.result;
  if (t.status === "failed" || t.status === "rejected") return <div className="note bad">{t.status === "rejected" ? "Agent 拒绝了这个任务：" : "执行失败："}{t.error || r?.summary || "没有给出原因"}</div>;
  if (t.status === "cancelled") return <p className="msg-line muted">任务已停止。{t.error ? `（${t.error}）` : ""}</p>;
  if (!r) return null;
  const findings = Array.isArray(r.findings) ? r.findings : [];
  return (
    <div className="result">
      {r.summary && <Markdown text={r.summary} />}
      {!!findings.length && (
        <div className="findings">
          <span className="label">关键发现</span>
          <ul>{findings.slice(0, 8).map((f, i) => <li key={i}>{finding(f)}</li>)}</ul>
          {findings.length > 8 && <span className="muted xs">还有 {findings.length - 8} 条，见详细报告</span>}
        </div>
      )}
    </div>
  );
}

function finding(f: unknown): string {
  if (typeof f === "string") return f;
  if (f && typeof f === "object") {
    const o = f as Record<string, unknown>;
    const head = [o.severity && `[${o.severity}]`, o.title || o.summary || o.finding || o.description].filter(Boolean).join(" ");
    return head || JSON.stringify(f);
  }
  return String(f);
}

function Actions({ m, onTask }: { m: ChatMessage; onTask: (id: string) => void }) {
  const t = m.task!;
  const [busy, setBusy] = useState(false);
  const act = async (path: "cancel" | "retry") => {
    if (path === "cancel" && !confirm("停止这个任务和它交出去的子任务？")) return;
    setBusy(true);
    try {
      await (path === "cancel" ? post(`/api/at/tasks/${t.id}/cancel`, { reason: "用户在聊天里停止" }) : post(`/api/at/tasks/${t.id}/retry`));
      toast(path === "cancel" ? "已停止" : "已重新排队");
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const canStop = isOpen(m) && t.status !== "unassigned";
  const canRetry = (t.status === "failed" || t.status === "cancelled") && !!t.to_agent_id;
  return (
    <div className="msg-actions">
      <button className="chip-btn" onClick={() => onTask(t.id)}><FileText size={12} /> {t.status === "completed" ? "查看详细报告" : "查看执行过程"} <ChevronRight size={12} /></button>
      {canStop && <button className="chip-btn" disabled={busy || DEMO} onClick={() => act("cancel")}><CircleStop size={12} /> 停止</button>}
      {canRetry && <button className="chip-btn" disabled={busy || DEMO} onClick={() => act("retry")}><RotateCcw size={12} /> 重试</button>}
      {t.status === "completed" && Number(t.cost_usd) > 0 && <span className="muted xs" title="这个任务的模型花费">${Number(t.cost_usd).toFixed(4)}</span>}
    </div>
  );
}

export function Thinking() {
  return <div className="msg bot"><span className="msg-av"><BrandMark size={28} /></span><div className="msg-body"><p className="msg-line muted"><Sparkles size={13} /> 正在创建任务…</p></div></div>;
}
