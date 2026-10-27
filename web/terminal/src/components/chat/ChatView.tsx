import { useEffect, useRef, useState, type ReactNode } from "react";
import { sendMessage, startChat, useChat, type SendBody } from "../../lib/chat";
import { errText, toast } from "../../lib/util";
import { ErrorState, Loading } from "../ui";
import { Composer } from "./Composer";
import { Thinking, Turn } from "./Turn";

/**
 * A conversation and its input. Without a chat id it shows `empty` (the welcome) and the first message creates the
 * chat on the server, so refreshing or switching pages never loses it.
 */
export function ChatView({ chatId, onCreated, onTask, fixedAgent, empty, top, placeholder, draft, onDraftUsed }: {
  chatId?: string | null;
  onCreated: (id: string) => void;
  onTask: (id: string) => void;
  fixedAgent?: { id: string; name: string } | null;
  empty?: ReactNode;
  top?: ReactNode;
  placeholder?: string;
  draft?: string | null;
  onDraftUsed?: () => void;
}) {
  const q = useChat(chatId);
  const [sending, setSending] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const msgs = q.data?.messages || [];
  const lastSig = msgs.length + ":" + (msgs[msgs.length - 1]?.task?.status || "") + ":" + (sending ? 1 : 0);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    // Follow new output only when the reader is already near the bottom.
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 240 || sending) end.current?.scrollIntoView({ block: "end" });
  }, [lastSig, sending]);
  useEffect(() => {
    requestAnimationFrame(() => end.current?.scrollIntoView({ block: "end" }));
  }, [chatId, q.isSuccess]);

  const onSend = async (b: SendBody) => {
    setSending(b.text);
    try {
      if (chatId) {
        await sendMessage(chatId, fixedAgent ? { ...b, agent_id: fixedAgent.id } : b);
        await q.refetch();
      } else {
        const c = await startChat(fixedAgent ? { ...b, agent_id: fixedAgent.id } : b);
        onCreated(c.id);
      }
      return true;
    } catch (e) {
      toast(errText(e), true);
      return false;
    } finally {
      setSending(null);
    }
  };

  const hasMessages = !!chatId && msgs.length > 0;
  return (
    <div className="chat">
      <div className="chat-scroll" ref={scroller}>
        <div className="chat-col">
          {top}
          {chatId && q.isLoading ? <Loading rows={5} /> : chatId && q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : hasMessages ? (
            msgs.map((m) => <Turn key={m.id} m={m} chat={q.data!} onTask={onTask} />)
          ) : !sending && empty}
          {sending && (
            <div className="turn">
              <div className="msg user"><div className="bubble">{sending}</div></div>
              <Thinking />
            </div>
          )}
          <div ref={end} />
        </div>
      </div>
      <div className="chat-input">
        <div className="chat-col">
          <Composer onSend={onSend} fixedAgent={fixedAgent} defaultWorkspace={q.data?.workspace_id} busy={!!sending} placeholder={placeholder}
            autoFocus={!hasMessages} draft={draft} onDraftUsed={onDraftUsed}
            defaultAgent={q.data && !fixedAgent ? q.data.messages[q.data.messages.length - 1]?.agent_id : undefined} />
          <p className="chat-foot">Agent 只在授予的权限内工作；代码修改、部署和资金操作需要你确认。</p>
        </div>
      </div>
    </div>
  );
}
