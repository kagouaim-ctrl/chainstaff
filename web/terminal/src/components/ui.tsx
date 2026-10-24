import { Copy, ExternalLink, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { ProofState, TaskStatus } from "../lib/types";
import { copy, initials, short, useToasts } from "../lib/util";

// ---------- status vocabulary: one map per object, the same words and colours on every page ----------

type Tone = "ok" | "bad" | "acc" | "info" | "mute" | "";
const TASK: Record<TaskStatus, [string, Tone, boolean?]> = {
  pending: ["Pending", "mute"],
  assigned: ["Assigned", "info"],
  running: ["Running", "acc", true],
  waiting: ["Running", "acc", true], // a goal waiting on its tasks is running from the user's point of view
  review: ["Review", "acc"],
  completed: ["Completed", "ok"],
  failed: ["Failed", "bad"],
  cancelled: ["Cancelled", "mute"],
};
const CHAIN: Record<string, [string, Tone, boolean?]> = { pending: ["Pending", "acc", true], confirmed: ["Confirmed", "ok"], failed: ["Failed", "bad"] };
const APPROVAL: Record<string, [string, Tone]> = { pending: ["Pending", "acc"], approved: ["Approved", "ok"], rejected: ["Rejected", "bad"] };
export const PROOF: Record<ProofState, [string, Tone, boolean?]> = {
  in_progress: ["In progress", "mute"],
  awaiting_review: ["Awaiting review", "acc"],
  offchain_completed: ["Off-chain completed", "info"],
  onchain_pending: ["On-chain pending", "acc", true],
  onchain_confirmed: ["On-chain confirmed", "ok"],
  not_completed: ["Not completed", "mute"],
};

export function Status({ status, kind = "task", label }: { status: string; kind?: "task" | "chain" | "approval" | "proof"; label?: string }) {
  const map = (kind === "chain" ? CHAIN : kind === "approval" ? APPROVAL : kind === "proof" ? PROOF : TASK) as Record<string, [string, Tone, boolean?]>;
  const [l, tone, live] = map[status] || [status, ""];
  return (
    <span className={`st ${tone} ${live ? "live" : ""}`}>
      <i />
      <span className="t">{label ?? l}</span>
    </span>
  );
}
/** Kept for pages that still say StatusBadge. */
export const StatusBadge = Status;

export function Priority({ p }: { p: number }) {
  return <span className={`prio p${p}`} title={["Urgent", "High", "Normal", "Low"][p]}>P{p}</span>;
}

/** queued → assigned → running → done, for one task. Not a percentage: the engine has no partial progress. */
export function Pipe({ status }: { status: TaskStatus }) {
  const n = { pending: 1, assigned: 2, running: 3, waiting: 3, review: 4, completed: 4, failed: 4, cancelled: 1 }[status];
  const tone = status === "completed" || status === "review" ? "completed" : status === "failed" ? "failed" : status === "running" || status === "assigned" || status === "waiting" ? "running" : "";
  return (
    <span className={`pipe ${tone}`} title={`queued → assigned → running → done · ${status}`}>
      {[1, 2, 3, 4].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}
    </span>
  );
}

/** Settled share of a mission's tasks, split into completed and failed. */
export function Progress({ done, failed = 0, total, compact }: { done: number; failed?: number; total: number; compact?: boolean }) {
  const w = (n: number) => (total ? `${(n / total) * 100}%` : "0%");
  return (
    <span className="progress" title={`${done} completed · ${failed} failed · ${total} tasks`}>
      <span className="bar">
        <i style={{ width: w(done), background: "var(--ok)" }} />
        <i style={{ width: w(failed), background: "var(--bad)" }} />
      </span>
      {!compact && <span className="mono">{done + failed}/{total}</span>}
    </span>
  );
}

export function Avatar({ name, mgr, lg, title }: { name?: string | null; mgr?: boolean; lg?: boolean; title?: string }) {
  return <span className={`avatar ${mgr ? "mgr" : ""} ${lg ? "lg" : ""}`} title={title ?? name ?? ""}>{initials(name)}</span>;
}

export function Addr({ value, url, n = 6, copyable = true }: { value?: string | null; url?: string | null; n?: number; copyable?: boolean }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <span className="row gap2" style={{ display: "inline-flex" }}>
      <span className="mono" title={value}>{short(value, n)}</span>
      {copyable && (
        <button className="btn ghost xs icon" title="复制" aria-label="复制" onClick={(e) => (e.stopPropagation(), copy(value))}>
          <Copy size={11} />
        </button>
      )}
      {url && (
        <a className="btn ghost xs icon" href={url} target="_blank" rel="noreferrer" title="在区块浏览器打开" aria-label="区块浏览器" onClick={(e) => e.stopPropagation()}>
          <ExternalLink size={11} />
        </a>
      )}
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="t">{title}</div>
      {children}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="error-box row sp">
      <span>{error instanceof Error ? error.message : String(error)}</span>
      {retry && <button className="btn sm" onClick={retry}>重试</button>}
    </div>
  );
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="col" style={{ padding: 14, gap: 10 }} aria-busy="true">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" style={{ width: `${88 - (i % 4) * 14}%` }} />)}
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
}

export function Dialog({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" style={wide ? { width: "min(760px, calc(100vw - 32px))" } : undefined}>
        <div className="hd">
          <h2 className="grow" style={{ fontSize: 14 }}>{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="关闭"><X size={15} /></button>
        </div>
        <div className="bd">{children}</div>
        {footer && <div className="ft">{footer}</div>}
      </div>
    </>
  );
}

/** Bottom panel on phones (or a left drawer). Content scrolls inside; the page under it does not move. */
export function Sheet({ onClose, children, tall, side }: { onClose: () => void; children: ReactNode; tall?: boolean; side?: "left" }) {
  useEscape(onClose);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className={`sheet ${tall ? "tall" : ""} ${side || ""}`} role="dialog" aria-modal="true">
        {!side && <div className="grab" />}
        <div className="sheet-body">{children}</div>
      </div>
    </>
  );
}

export function Toasts() {
  const list = useToasts();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {list.map((t) => <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>{t.text}</div>)}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, l]) => <button key={v} aria-pressed={v === value} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  );
}

/** Underlined tabs. Counts are optional and shown in mono next to the label. */
export function Tabs<T extends string>({ value, options, onChange, label }: { value: T; options: [T, ReactNode, number?][]; onChange: (v: T) => void; label?: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {options.map(([v, l, n]) => (
        <button key={v} role="tab" aria-selected={v === value} onClick={() => onChange(v)}>
          {l}
          {n !== undefined && <span className="n">{n}</span>}
        </button>
      ))}
    </div>
  );
}

export function Cell({ label, value, sub, tone, onClick, title }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; onClick?: () => void; title?: string }) {
  const body = (
    <>
      <span className="label">{label}</span>
      <span className={`v ${tone || ""}`}>{value}</span>
      {sub !== undefined && <span className="s">{sub}</span>}
    </>
  );
  return onClick ? <button className="cell" onClick={onClick} title={title}>{body}</button> : <div className="cell" title={title}>{body}</div>;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="code">{JSON.stringify(value, null, 2)}</pre>;
}
