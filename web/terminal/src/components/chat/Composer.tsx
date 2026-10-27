import { ArrowUp, Bot, ChevronDown, Layers, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useAtAgents, useAtWorkspaces } from "../../lib/at";
import type { SendBody } from "../../lib/chat";
import { DEMO, DEMO_READ_ONLY } from "../../lib/demo";
import { WorkerAvatar } from "../WorkerAvatar";

export const KIND_LABEL: Record<string, string> = {
  general: "通用", analyze: "分析", investigate: "调查", fix: "修复代码", test: "运行测试", verify: "验证", review: "审阅", report: "汇报",
};

/**
 * The input: a roomy text area, then who should do it (KAGOU picks, or one agent), where (when there is more than
 * one workspace) and what kind of task it is. Every choice maps to a field the server's task API takes.
 */
export function Composer({ onSend, fixedAgent, defaultAgent, defaultWorkspace, placeholder, autoFocus, draft, onDraftUsed, busy }: {
  onSend: (b: SendBody) => Promise<boolean>;
  fixedAgent?: { id: string; name: string } | null;
  defaultAgent?: string | null;
  defaultWorkspace?: string | null;
  placeholder?: string;
  autoFocus?: boolean;
  draft?: string | null;
  onDraftUsed?: () => void;
  busy?: boolean;
}) {
  const [text, setText] = useState("");
  const [agent, setAgent] = useState<string>(defaultAgent || "");
  const [ws, setWs] = useState<string>(defaultWorkspace || "");
  const [kind, setKind] = useState("general");
  const [sending, setSending] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const agents = useAtAgents();
  const wss = useAtWorkspaces();
  const ready = useMemo(() => (agents.data || []).filter((a) => a.status === "active" && a.autonomous), [agents.data]);
  const workspaces = (wss.data || []).filter((w) => !w.archived);
  const picked = fixedAgent?.id || agent;
  const pickedRow = ready.find((a) => a.id === picked);
  // Workspace only matters when KAGOU picks the agent, or the picked agent has more than one.
  const wsOptions = picked ? workspaces.filter((w) => pickedRow?.workspaces.some((x) => x.id === w.id)) : workspaces;

  useEffect(() => {
    if (draft) {
      setText(draft);
      onDraftUsed?.();
      requestAnimationFrame(() => ta.current?.focus());
    }
  }, [draft, onDraftUsed]);
  useEffect(() => setAgent(defaultAgent || ""), [defaultAgent]);
  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 260) + "px";
  }, [text]);

  const send = async () => {
    const t = text.trim();
    if (!t || sending || busy || DEMO) return;
    setSending(true);
    const ok = await onSend({ text: t, agent_id: fixedAgent ? undefined : agent || null, workspace_id: ws || null, kind });
    setSending(false);
    if (ok) setText("");
  };
  const key = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };
  return (
    <div className="composer-box">
      <textarea ref={ta} className="composer-ta" rows={1} value={text} autoFocus={autoFocus} onChange={(e) => setText(e.target.value)} onKeyDown={key}
        placeholder={DEMO ? DEMO_READ_ONLY : placeholder || "告诉 KAGOU 要做什么，例如：检查国库钱包最近 24 小时的大额转出"} disabled={DEMO} aria-label="消息" maxLength={4000} />
      <div className="composer-bar">
        {!fixedAgent && (
          <Pick icon={agent ? <WorkerAvatar id={agent} appearance={pickedRow?.appearance} size={18} /> : <Sparkles size={14} />} label={agent ? pickedRow?.name || "Agent" : "自动调度"}
            title="谁来做：自动调度时 KAGOU 按职责、权限和工作区选择 Agent" value={agent} onChange={setAgent}
            options={[["", "自动调度（按职责选择 Agent）"], ...ready.map((a) => [a.id, a.name] as [string, string])]} />
        )}
        {wsOptions.length > 1 && (
          <Pick icon={<Layers size={14} />} label={workspaces.find((w) => w.id === ws)?.name || (picked ? "Agent 的工作区" : "自动选择工作区")}
            title="在哪个工作区里做（决定可用的仓库、合约、钱包和 Agent）" value={ws} onChange={setWs}
            options={[["", picked ? "Agent 的默认工作区" : "自动选择（Agent 最多的工作区）"], ...wsOptions.map((w) => [w.id, w.name] as [string, string])]} />
        )}
        <Pick icon={<Bot size={14} />} label={KIND_LABEL[kind] || kind} title="任务类型：决定接手的 Agent 需要哪些权限（例如修复代码需要 code:write）" value={kind} onChange={setKind}
          options={Object.entries(KIND_LABEL)} />
        <div className="grow" />
        <span className="composer-hint d-only">Enter 发送 · Shift+Enter 换行</span>
        <button className="composer-send" onClick={send} disabled={!text.trim() || sending || busy || DEMO} aria-label="发送" title={DEMO ? DEMO_READ_ONLY : "发送"}>
          <ArrowUp size={18} strokeWidth={2.2} />
        </button>
      </div>
    </div>
  );
}

function Pick({ icon, label, title, value, onChange, options }: { icon: React.ReactNode; label: string; title: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="pick" title={title}>
      {icon}
      <span className="ellipsis">{label}</span>
      <ChevronDown size={12} className="muted" />
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={title}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
