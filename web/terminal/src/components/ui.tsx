import { Copy, ExternalLink, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { TaskStatus } from "../lib/types";
import { copy, short, useToasts } from "../lib/util";

const TASK: Record<TaskStatus, [string, string, boolean?]> = {
  pending: ["Pending", ""],
  assigned: ["Assigned", "info"],
  running: ["Running", "acc", true],
  waiting: ["Waiting", "info", true],
  review: ["Review", "warn"],
  completed: ["Completed", "ok"],
  failed: ["Failed", "bad"],
  cancelled: ["Cancelled", ""],
};
const AGENT: Record<string, [string, string, boolean?]> = {
  busy: ["Busy", "acc", true],
  idle: ["Idle", "ok"],
  error: ["Error", "bad"],
  paused: ["Paused", "warn"],
  archived: ["Archived", ""],
  active: ["Active", "ok"],
};
const CHAIN: Record<string, [string, string, boolean?]> = {
  pending: ["Pending", "warn", true],
  confirmed: ["Confirmed", "ok"],
  failed: ["Failed", "bad"],
};
const APPROVAL: Record<string, [string, string]> = { pending: ["Pending", "warn"], approved: ["Approved", "ok"], rejected: ["Rejected", "bad"] };

export function StatusBadge({ status, kind = "task" }: { status: string; kind?: "task" | "agent" | "chain" | "approval" }) {
  const map = (kind === "agent" ? AGENT : kind === "chain" ? CHAIN : kind === "approval" ? APPROVAL : TASK) as Record<string, [string, string, boolean?]>;
  const [label, cls, live] = map[status] || [status, ""];
  return (
    <span className={`badge ${cls} ${live ? "pulse" : ""}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

export function Priority({ p }: { p: number }) {
  const [l, c] = [["P0", "bad"], ["P1", "warn"], ["P2", ""], ["P3", "muted"]][p] || ["P?", ""];
  return <span className={`tag ${c}`}>{l}</span>;
}

export function Addr({ value, url, n = 6 }: { value?: string | null; url?: string | null; n?: number }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <span className="row" style={{ gap: 4 }}>
      <span className="mono" title={value}>
        {short(value, n)}
      </span>
      <button className="btn ghost sm icon" title="复制完整地址" aria-label="复制" onClick={(e) => (e.stopPropagation(), copy(value))}>
        <Copy size={12} />
      </button>
      {url && (
        <a className="btn ghost sm icon" href={url} target="_blank" rel="noreferrer" title="在区块浏览器打开" aria-label="区块浏览器" onClick={(e) => e.stopPropagation()}>
          <ExternalLink size={12} />
        </a>
      )}
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div style={{ color: "var(--text-2)", fontWeight: 500 }}>{title}</div>
      {children}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="error-box row sp">
      <span>{error instanceof Error ? error.message : String(error)}</span>
      {retry && (
        <button className="btn sm" onClick={retry}>
          重试
        </button>
      )}
    </div>
  );
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="col" style={{ padding: 16 }} aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function Dialog({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" style={wide ? { width: "min(760px, calc(100vw - 32px))" } : undefined}>
        <div className="hd">
          <h2 className="grow">{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </button>
        </div>
        <div className="bd">{children}</div>
        {footer && <div className="ft">{footer}</div>}
      </div>
    </>
  );
}

export function Sheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true">
        <div className="grab" />
        <div className="sheet-body">{children}</div>
      </div>
    </>
  );
}

export function Toasts() {
  const list = useToasts();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {list.map((t) => (
        <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, l]) => (
        <button key={v} aria-pressed={v === value} onClick={() => onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/** Stacked horizontal bar for status mixes: [value, cssColorVar][] */
export function MixBar({ parts, height = 4 }: { parts: [number, string][]; height?: number }) {
  const total = parts.reduce((s, [v]) => s + v, 0);
  return (
    <div className="bar" style={{ height }}>
      {total > 0 && parts.map(([v, c], i) => <i key={i} style={{ width: `${(v / total) * 100}%`, background: c }} />)}
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`v ${tone || ""}`}>{value}</div>
      {sub !== undefined && <div className="s">{sub}</div>}
    </div>
  );
}
