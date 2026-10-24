import { useEffect, useSyncExternalStore } from "react";
import { queryClient, token } from "./api";
import type { Message } from "./types";

/**
 * One live connection per tab: Server-Sent Events read through fetch() so the token stays
 * in a header. Resumes from the last id after a drop, with backoff. Messages are pushed to
 * subscribers incrementally; list queries are invalidated in one debounced batch.
 */

type Listener = (m: Message) => void;
type State = "connecting" | "live" | "retrying" | "off";

const listeners = new Set<Listener>();
const stateListeners = new Set<() => void>();
let state: State = "off";
let lastId = 0;
let ctl: AbortController | null = null;
let invalidateTimer: number | undefined;
const touched = new Set<string>();

function setState(s: State) {
  state = s;
  stateListeners.forEach((f) => f());
}

function scheduleInvalidate(m: Message) {
  touched.add("overview");
  touched.add("activity");
  if (m.kind === "task" || m.kind === "agent" || m.kind === "tool") ["tasks", "agents", "task", "workspaces", "missions", "squads"].forEach((k) => touched.add(k));
  if (m.event.startsWith("squad.") || m.event.startsWith("agent.")) ["squads", "agents", "workspaces", "preflight"].forEach((k) => touched.add(k));
  if (m.event.startsWith("approval")) ["approvals", "missions", "task", "ledger"].forEach((k) => touched.add(k));
  if (m.event === "task.completed" || m.event === "task.failed" || m.event === "goal.review") touched.add("ledger");
  if (m.kind === "chain") ["chain", "records", "ledger", "missions", "task"].forEach((k) => touched.add(k));
  if (invalidateTimer) return;
  invalidateTimer = window.setTimeout(() => {
    invalidateTimer = undefined;
    const keys = [...touched];
    touched.clear();
    keys.forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
  }, 400);
}

async function run(startAfter: number) {
  lastId = startAfter;
  let delay = 1000;
  while (ctl && !ctl.signal.aborted) {
    setState(lastId ? "retrying" : "connecting");
    try {
      const r = await fetch(`/api/wf/stream?after=${lastId}`, { headers: { Authorization: "Bearer " + token() }, signal: ctl.signal });
      if (!r.ok || !r.body) throw new Error(String(r.status));
      setState("live");
      delay = 1000;
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const data = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!data) continue;
          const m = JSON.parse(data.slice(6)) as Message;
          lastId = Math.max(lastId, m.id);
          listeners.forEach((f) => f(m));
          scheduleInvalidate(m);
        }
      }
    } catch (e) {
      if (ctl?.signal.aborted) break;
    }
    setState("retrying");
    await new Promise((res) => setTimeout(res, delay));
    delay = Math.min(delay * 2, 15000);
  }
  setState("off");
}

export function startStream(after: number) {
  if (ctl) return;
  ctl = new AbortController();
  void run(after);
}

export function stopStream() {
  ctl?.abort();
  ctl = null;
}

export function useStreamState(): State {
  return useSyncExternalStore(
    (f) => {
      stateListeners.add(f);
      return () => stateListeners.delete(f);
    },
    () => state,
  );
}

export function useLiveMessages(fn: Listener) {
  useEffect(() => {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, [fn]);
}
