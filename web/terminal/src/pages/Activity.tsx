import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityRow } from "../components/ActivityStream";
import { Dialog, ErrorState, Loading } from "../components/ui";
import { api } from "../lib/api";
import { useAgentName, useAgents } from "../lib/queries";
import { useLiveMessages } from "../lib/stream";
import type { Message, MsgKind } from "../lib/types";
import { usePref } from "../lib/util";

const KINDS: MsgKind[] = ["user", "agent", "system", "task", "tool", "chain"];
const CAP = 5000;

/** Every event across workspaces. Virtualised so thousands of rows stay cheap; new events stream in. */
export default function Activity() {
  const agents = useAgents();
  const name = useAgentName();
  const [kinds, setKinds] = usePref<string[]>("activity-kinds", KINDS);
  const [agent, setAgent] = useState("");
  const [task, setTask] = useState("");
  const [paused, setPaused] = useState(false);
  const [detail, setDetail] = useState<Message | null>(null);
  const qs = new URLSearchParams({ kinds: kinds.join(","), agent, task, limit: "1000" }).toString();
  const base = useQuery({ queryKey: ["activity-log", qs], queryFn: () => api<Message[]>(`/api/wf/activity?${qs}`), staleTime: Infinity });
  const [live, setLive] = useState<Message[]>([]);
  useEffect(() => setLive([]), [qs]);
  const match = useCallback((m: Message) => kinds.includes(m.kind) && (!agent || m.sender === agent) && (!task || m.task_id === task || m.data?.root_id === task), [kinds, agent, task]);
  useLiveMessages(useCallback((m: Message) => match(m) && setLive((p) => (p.length >= CAP ? [...p.slice(-CAP + 1), m] : [...p, m])), [match]));
  // Oldest first, like a terminal.
  const rows = useMemo(() => {
    const seen = new Set<number>();
    return [...(base.data || []).slice().reverse(), ...live].filter((m) => !seen.has(m.id) && (seen.add(m.id), true));
  }, [base.data, live]);

  const parent = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 28, overscan: 12 });
  useEffect(() => {
    if (!paused && rows.length) v.scrollToIndex(rows.length - 1, { align: "end" });
  }, [rows.length, paused, v]);

  const toggle = (k: string) => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k]);
  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="toolbar">
        <h1>Activity</h1>
        <span className="muted xs"><span className="mono">{rows.length}</span> events · 双击一行查看原始 JSON</span>
        <div className="grow" />
        <button className={`btn sm ${paused ? "primary" : ""}`} onClick={() => setPaused(!paused)}>
          {paused ? <Play size={12} /> : <Pause size={12} />} {paused ? "Resume autoscroll" : "Pause autoscroll"}
        </button>
      </div>
      <div className="qab">
        {KINDS.map((k) => (
          <button key={k} className="chip" aria-pressed={kinds.includes(k)} onClick={() => toggle(k)}>{k[0].toUpperCase() + k.slice(1)}</button>
        ))}
        <select className="input sm" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="员工">
          <option value="">All senders</option>
          <option value="user">You</option>
          <option value="system">System</option>
          {agents.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <input className="input sm mono" style={{ width: 160 }} placeholder="Mission ID" value={task} onChange={(e) => setTask(e.target.value.trim())} />
      </div>
      {base.isLoading ? <Loading rows={10} /> : base.error ? <ErrorState error={base.error} retry={() => base.refetch()} /> : (
        <div
          ref={parent}
          style={{ flex: 1, minHeight: 300, overflow: "auto" }}
          onWheel={(e) => e.deltaY < 0 && !paused && setPaused(true)}
        >
          <div style={{ height: v.getTotalSize(), position: "relative" }}>
            {v.getVirtualItems().map((it) => {
              const m = rows[it.index];
              return (
                <div key={m.id} data-index={it.index} ref={v.measureElement} style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${it.start}px)` }}>
                  <ActivityRow m={m} name={name} onOpen={setDetail} />
                </div>
              );
            })}
          </div>
        </div>
      )}
      {detail && (
        <Dialog title={`#${detail.id} · ${detail.event}`} onClose={() => setDetail(null)} wide>
          <pre className="report">{JSON.stringify(detail, null, 2)}</pre>
        </Dialog>
      )}
    </div>
  );
}
