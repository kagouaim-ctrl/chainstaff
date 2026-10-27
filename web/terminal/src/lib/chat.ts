/**
 * Chats (/api/at/chats). A chat keeps what the person typed and which task each message became; the answer shown
 * under a message is read from that task (its status, result, hand-offs and approvals), never written by the console.
 */
import { useQuery } from "@tanstack/react-query";
import { api, post, put } from "./api";
import { OPEN_TASK, refreshAt, type AtApproval, type AtTask, type TaskStatusAt } from "./at";

export interface ChatRow {
  id: string;
  title: string;
  agent_id: string | null;
  agent_name: string | null;
  workspace_id: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  turns?: number;
  last_status?: TaskStatusAt | "error" | null;
}

export interface Considered {
  agent_id: string;
  name: string;
  eligible: boolean;
  reason: string;
  relevance: number;
  load: number;
}

export interface SubTask {
  id: string;
  kind: string;
  objective: string;
  status: TaskStatusAt;
  from_agent_id: string | null;
  to_agent_id: string | null;
  depth: number;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  summary: string | null;
}

export interface ChatMessage {
  id: number;
  at: string;
  text: string;
  agent_id: string | null;
  kind: string;
  error: string | null;
  routing: { auto: boolean; agent_id: string | null; considered?: Considered[] } | null;
  task: (AtTask & { deduplicated?: boolean }) | null;
  tree: SubTask[];
  approvals: AtApproval[];
  steps: { kind: string; name: string; ok: boolean; at: string }[];
}

export interface ChatDetail extends ChatRow {
  messages: ChatMessage[];
  names: Record<string, string>;
}

export const isOpen = (m: ChatMessage) => !!m.task && OPEN_TASK.includes(m.task.status);

export const useChats = (agentId?: string) =>
  useQuery({
    queryKey: ["at", "chats", agentId || ""],
    queryFn: () => api<ChatRow[]>(`/api/at/chats${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`),
    refetchInterval: 30_000,
  });

/** Polls quickly while a message is still being worked on, slowly otherwise; the activity stream also refreshes it. */
export const useChat = (id?: string | null) =>
  useQuery({
    queryKey: ["at", "chat", id],
    queryFn: () => api<ChatDetail>(`/api/at/chats/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.messages.some(isOpen) ? 4_000 : 30_000),
  });

export interface SendBody {
  text: string;
  agent_id?: string | null;
  workspace_id?: string | null;
  kind?: string;
}

export async function startChat(body: SendBody): Promise<ChatDetail> {
  const c = await post<ChatDetail>("/api/at/chats", body);
  refreshAt();
  return c;
}

export async function sendMessage(chatId: string, body: SendBody): Promise<ChatDetail> {
  const c = await post<ChatDetail>(`/api/at/chats/${chatId}/messages`, body);
  refreshAt();
  return c;
}

export async function updateChat(chatId: string, body: { title?: string; archived?: boolean }) {
  await put(`/api/at/chats/${chatId}`, body);
  refreshAt();
}
