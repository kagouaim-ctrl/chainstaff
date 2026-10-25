import { useCallback, useMemo, useState } from "react";
import { useMeta } from "./queries";
import { useLiveMessages } from "./stream";
import type { Message, TaskDetail } from "./types";

/**
 * What each task of a mission is doing right now, from its engine events:
 * the model is reasoning, a tool is running, a chain tool is waiting on the RPC node, or it finished.
 * Model time and chain time are shown apart, so a slow answer can be traced to the right side.
 */
export type Phase = "queued" | "reasoning" | "tool" | "rpc" | "completed" | "failed" | "cancelled";

export interface LiveState {
  phase: Phase;
  tool?: string;
  detail?: string; // the model's latest streamed text, or the tool's last line
  since: string;
  modelCalls: number;
  toolCalls: number;
}

export const PHASE_LABEL: Record<Phase, string> = {
  queued: "排队中",
  reasoning: "AI 推理中",
  tool: "调用工具",
  rpc: "等待链上 RPC",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const PHASE_TONE: Record<Phase, string> = {
  queued: "mute",
  reasoning: "info",
  tool: "acc",
  rpc: "warn",
  completed: "ok",
  failed: "bad",
  cancelled: "mute",
};

const WATCHED = new Set([
  "task.assign", "agent.working", "model.start", "agent.delta", "model.done", "tool.call", "tool.result", "tool.cancelled",
  "tool.start", "tool.done", "tool.error", "task.completed", "task.failed", "task.cancelled", "task.retry",
]);

export function phasesOf(messages: Message[], chainTools: Set<string>): Map<string, LiveState> {
  const out = new Map<string, LiveState>();
  const sorted = [...messages].sort((a, b) => a.id - b.id);
  for (const m of sorted) {
    if (!m.task_id || !WATCHED.has(m.event)) continue;
    const prev = out.get(m.task_id) || { phase: "queued" as Phase, since: m.at, modelCalls: 0, toolCalls: 0 };
    const s: LiveState = { ...prev };
    const set = (phase: Phase, extra: Partial<LiveState> = {}) => {
      if (phase !== s.phase) s.since = m.at;
      Object.assign(s, { phase, ...extra });
    };
    const toolPhase = (name?: string) => (name && chainTools.has(name) ? "rpc" : "tool");
    switch (m.event) {
      case "task.assign":
      case "task.retry":
        set("queued", { tool: undefined, detail: undefined });
        break;
      case "agent.working":
        set("reasoning", { detail: undefined });
        break;
      case "model.start":
        s.modelCalls = m.data?.call ?? s.modelCalls + 1;
        set("reasoning", { tool: undefined, detail: undefined });
        break;
      case "agent.delta":
        set("reasoning", { detail: (m.text || "").slice(-160) });
        break;
      case "model.done":
        set(m.data?.tool_uses?.length ? "tool" : "reasoning", { detail: (m.text || "").slice(0, 160) });
        break;
      case "tool.call":
        s.toolCalls += 1;
        set(toolPhase(m.data?.tool), { tool: m.data?.tool, detail: undefined });
        break;
      case "tool.start":
        set(toolPhase(m.data?.skill), { tool: m.data?.skill, detail: undefined });
        break;
      case "tool.result":
        set("reasoning", { tool: undefined, detail: m.data?.ok === false ? `${m.data?.tool} 失败：${m.data?.error}` : `${m.data?.tool} 返回，用时 ${m.data?.secs ?? "?"}s` });
        break;
      case "tool.done":
      case "tool.error":
        set(m.event === "tool.error" ? "failed" : s.phase, { detail: m.text });
        break;
      case "tool.cancelled":
        set("cancelled", { detail: m.text });
        break;
      case "task.completed":
        set("completed", { tool: undefined });
        break;
      case "task.failed":
        set("failed", { tool: undefined, detail: m.text });
        break;
      case "task.cancelled":
        set("cancelled", { tool: undefined });
        break;
    }
    out.set(m.task_id, s);
  }
  return out;
}

/** Live phases for a mission: the loaded detail plus every streamed event for its tasks, without waiting for a refetch. */
export function useMissionPhases(d: TaskDetail | undefined): Map<string, LiveState> {
  const meta = useMeta();
  const [extra, setExtra] = useState<Message[]>([]);
  const ids = useMemo(() => new Set([d?.root.id, ...(d?.subtasks || []).map((s) => s.id)].filter(Boolean) as string[]), [d]);
  const chainTools = useMemo(
    () => new Set((meta.data?.skills || []).filter((s) => s.permissions.includes("chain:read") || s.permissions.includes("net:etherscan")).map((s) => s.id)),
    [meta.data],
  );
  const on = useCallback(
    (m: Message) => {
      if (m.task_id && ids.has(m.task_id) && WATCHED.has(m.event)) setExtra((x) => (x.length > 400 ? [...x.slice(-200), m] : [...x, m]));
    },
    [ids],
  );
  useLiveMessages(on);
  return useMemo(() => {
    if (!d) return new Map();
    const seen = new Set(d.messages.map((m) => m.id));
    return phasesOf([...d.messages, ...extra.filter((m) => !seen.has(m.id))], chainTools);
  }, [d, extra, chainTools]);
}
