import { ArrowRight } from "lucide-react";
import { memo } from "react";
import { describe } from "../lib/events";
import type { Message } from "../lib/types";
import { fmtTime, go } from "../lib/util";

const KIND_TAG: Record<string, string> = { user: "USER", agent: "AGENT", system: "SYS", task: "TASK", tool: "TOOL", chain: "CHAIN" };

/** One event as a single dense row: time, kind, who → whom, what, task link. Used by Activity and inspectors. */
export const ActivityRow = memo(function ActivityRow({ m, name, compact, onOpen }: {
  m: Message;
  name: (id?: string | null) => string;
  compact?: boolean;
  onOpen?: (m: Message) => void;
}) {
  const v = describe(m);
  const tone = v.tone === "ok" ? "ok" : v.tone === "bad" ? "bad" : v.tone === "acc" ? "acc" : v.tone === "info" ? "info" : "muted";
  const actor = (
    <span className="row gap4 ellipsis">
      <span className="strong ellipsis">{name(m.sender)}</span>
      {m.recipient && v.showTarget && (
        <>
          <ArrowRight size={11} className="muted" style={{ flex: "none" }} />
          <span className="ellipsis">{name(m.recipient)}</span>
        </>
      )}
    </span>
  );
  const text = m.event === "tool.done" || m.event === "tool.error" ? `${m.data?.skill || ""} · ${m.text}` : m.text.split("\n")[0];
  if (compact)
    return (
      <div className="as-row compact" onDoubleClick={() => onOpen?.(m)}>
        <span className="when">{fmtTime(m.at, false)}</span>
        <span className="col gap2" style={{ minWidth: 0 }}>
          <span className="row gap6 sm" style={{ minWidth: 0 }}>
            <span className={`${tone} xs nowrap`} style={{ fontWeight: 600 }}>{v.verb}</span>
            <span className="dim ellipsis">{text}</span>
          </span>
        </span>
      </div>
    );
  return (
    <div className="as-row" onDoubleClick={() => onOpen?.(m)}>
      <span className="when">{fmtTime(m.at, false)}</span>
      <span className="hide-m"><span className="tag">{KIND_TAG[m.kind]}</span></span>
      <span className="hide-m" style={{ minWidth: 0 }}>{actor}</span>
      <span className="row gap6" style={{ minWidth: 0 }}>
        <span className={`${tone} xs nowrap`} style={{ fontWeight: 600 }}>{v.verb}</span>
        <span className="dim ellipsis">{text}</span>
      </span>
      {m.task_id ? (
        <button className="btn ghost xs mono hide-m" onClick={() => go("tasks", m.task_id!)} title="打开任务">{m.task_id}</button>
      ) : <span />}
    </div>
  );
});

/** A list of events, newest first. The rows are memoised, so a live stream re-renders only what is new. */
export function ActivityStream({ rows, name, compact, onOpen, empty }: {
  rows: Message[];
  name: (id?: string | null) => string;
  compact?: boolean;
  onOpen?: (m: Message) => void;
  empty?: React.ReactNode;
}) {
  if (!rows.length) return <>{empty ?? null}</>;
  return <>{rows.map((m) => <ActivityRow key={m.id} m={m} name={name} compact={compact} onOpen={onOpen} />)}</>;
}
