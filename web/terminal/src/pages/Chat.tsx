import { Archive, Pencil } from "lucide-react";
import { useCallback, useState } from "react";
import { TaskDialog } from "../components/AtBits";
import { ChatView } from "../components/chat/ChatView";
import { Notices } from "../components/chat/Notices";
import { WorkerAvatar } from "../components/WorkerAvatar";
import { useAtAgents } from "../lib/at";
import { updateChat, useChat } from "../lib/chat";
import { DEMO } from "../lib/demo";
import { errText, go, setParams, toast, type Route } from "../lib/util";

// Prefill only: picking one puts the text in the input so it can be edited first; nothing is sent by itself.
const EXAMPLES: [string, string][] = [
  ["链上监控", "检查 BSC 上最近 24 小时与我们合约相关的大额转账，列出异常地址"],
  ["钱包分析", "分析这个钱包最近的资金流向和持仓变化：0x"],
  ["代码排查", "查看 pricing 服务最近的报错，找出原因并提出修复方案"],
  ["合约检查", "检查这个合约的 owner 和管理权限有没有风险：0x"],
];

/**
 * The default page: a conversation with KAGOU. With no chat open it is a welcome and an input; each message
 * becomes a real task, given to the agent you pick or the one KAGOU matches by responsibility and permissions.
 */
export default function Chat({ route }: { route: Route }) {
  const id = route.id || null;
  const task = route.params.get("task");
  const openTask = useCallback((t: string) => setParams(route, { task: t }), [route]);
  const [draft, setDraft] = useState<string | null>(null);
  const chat = useChat(id);
  return (
    <div className="page chat-page">
      {id && <ChatHeader id={id} />}
      <ChatView key={id || "new"} chatId={id} onCreated={(c) => go("chat", c)} onTask={openTask} draft={draft} onDraftUsed={() => setDraft(null)}
        top={!id ? <Notices /> : chat.data && <Notices approvals={false} />}
        empty={!id && <Welcome onPick={setDraft} />} />
      {task && <TaskDialog id={task} onClose={() => setParams(route, { task: null })} />}
    </div>
  );
}

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  const agents = useAtAgents();
  const ready = (agents.data || []).filter((a) => a.status === "active" && a.autonomous);
  return (
    <div className="welcome">
      <h1>今天要 KAGOU 处理什么？</h1>
      <p className="muted">描述任务，KAGOU 会交给合适的 Agent 执行。进度和结果都会出现在这里，需要你确认的事情会单独提醒。</p>
      <div className="examples">
        {EXAMPLES.map(([k, t]) => (
          <button key={k} className="example" onClick={() => onPick(t)} disabled={DEMO}>
            <span className="example-k">{k}</span>
            <span className="example-t">{t}</span>
          </button>
        ))}
      </div>
      {!!ready.length && (
        <div className="welcome-agents">
          <span className="label">或者直接和一个 Agent 对话</span>
          <div className="row gap6 wrap">
            {ready.slice(0, 6).map((a) => (
              <button key={a.id} className="agent-chip" onClick={() => go("agents", a.id)}>
                <WorkerAvatar id={a.id} appearance={a.appearance} size={20} /> {a.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ChatHeader({ id }: { id: string }) {
  const q = useChat(id);
  const c = q.data;
  const rename = async () => {
    const t = prompt("会话名称", c?.title || "");
    if (!t?.trim()) return;
    try {
      await updateChat(id, { title: t.trim() });
    } catch (e) {
      toast(errText(e), true);
    }
  };
  const archive = async () => {
    if (!confirm("归档这个会话？任务和结果仍然保留在 Missions 里。")) return;
    try {
      await updateChat(id, { archived: true });
      go("chat");
    } catch (e) {
      toast(errText(e), true);
    }
  };
  return (
    <div className="chat-head">
      <span className="chat-title ellipsis">{c?.title || "…"}</span>
      {c?.agent_id && <button className="agent-chip sm" onClick={() => go("agents", c.agent_id!)}>与 {c.agent_name} 对话</button>}
      <div className="grow" />
      <button className="btn ghost icon sm" onClick={rename} disabled={DEMO || !c} title="重命名" aria-label="重命名"><Pencil size={14} /></button>
      <button className="btn ghost icon sm" onClick={archive} disabled={DEMO || !c} title="归档" aria-label="归档"><Archive size={14} /></button>
    </div>
  );
}
