import { useEffect, useSyncExternalStore } from "react";
import { queryClient, token } from "./api";
import { DEMO, demoChainFeed } from "./demo";

/**
 * The BSC listener's live feed (/api/chain/ws): new blocks, monitor events, confirmations, reorgs.
 * The token goes in the first message, never in the URL. After a drop it reconnects with backoff and
 * resumes from the last sequence number, so nothing published meanwhile is missed.
 * Only connected while a page that shows chain data is open.
 */

export interface ChainMsg {
  type: string;
  seq: number;
  ts_ms: number;
  recv_ms?: number; // when this browser received it, for the latency readout
  [k: string]: any;
}

type State = "off" | "connecting" | "live" | "retrying" | "disabled";

let sock: WebSocket | null = null;
let users = 0;
let state: State = "off";
let lastSeq: number | undefined;
let delay = 1000;
let timer: number | undefined;
let recent: ChainMsg[] = [];
let snapshot: { state: State; recent: ChainMsg[]; hello: any } = { state, recent, hello: null };
const subs = new Set<() => void>();
const MAX = 300;

function emit() {
  snapshot = { state, recent, hello: snapshot.hello };
  subs.forEach((f) => f());
}

// Static demo: the recorded feed, shown once, newest first. No socket.
function replay() {
  void demoChainFeed().then((msgs) => {
    const ms = msgs as ChainMsg[];
    snapshot.hello = ms.find((m) => m.type === "hello") ?? null;
    recent = ms.filter((m) => m.type !== "hello").slice(0, MAX);
    state = "live";
    emit();
  });
}

function connect() {
  if (DEMO) return replay();
  if (sock || !users) return;
  state = lastSeq === undefined ? "connecting" : "retrying";
  emit();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/chain/ws`);
  sock = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: token(), since: lastSeq }));
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data) as ChainMsg;
    if (m.type === "hello") {
      state = "live";
      delay = 1000;
      snapshot.hello = m;
      if (lastSeq === undefined) lastSeq = m.seq;
      emit();
      return;
    }
    if (m.type === "status" && m.enabled === false) {
      state = "disabled";
      snapshot.hello = m;
      emit();
      return;
    }
    if (typeof m.seq === "number") lastSeq = Math.max(lastSeq ?? 0, m.seq);
    m.recv_ms = Date.now();
    recent = [m, ...recent].slice(0, MAX);
    if (m.type.startsWith("event") || m.type === "reorg" || m.type === "trigger") queryClient.invalidateQueries({ queryKey: ["chain-events"] });
    if (m.type === "trigger") queryClient.invalidateQueries({ queryKey: ["missions"] });
    emit();
  };
  ws.onclose = (e) => {
    sock = null;
    if (e.code === 4401 || e.code === 4409) {
      state = e.code === 4409 ? "disabled" : "off";
      emit();
      return;
    }
    if (!users) {
      state = "off";
      emit();
      return;
    }
    state = "retrying";
    emit();
    timer = window.setTimeout(connect, delay);
    delay = Math.min(delay * 2, 15000);
  };
}

function retain() {
  users += 1;
  if (users === 1) connect();
  return () => {
    users -= 1;
    if (!users) {
      window.clearTimeout(timer);
      sock?.close();
      sock = null;
    }
  };
}

export function useChainFeed() {
  useEffect(retain, []);
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => snapshot,
  );
}
