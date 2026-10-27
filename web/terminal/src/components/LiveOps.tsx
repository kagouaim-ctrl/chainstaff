import { AlertTriangle, ArrowRight, Bell, Pin, PinOff, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { post } from "../lib/api";
import { refreshAt, useAtActivity, useAtApprovals, useAtLive, useAtNotifications, useAtOverview, type AtActivity } from "../lib/at";
import { DEMO } from "../lib/demo";
import { useApprovals } from "../lib/queries";
import { closeOps, openOps, type OpsTab } from "../lib/shell";
import { ago, fmtTime, go } from "../lib/util";
import { ApprovalMessage } from "./Approval";
import { Sev } from "./AtBits";
import { EmptyState, Loading } from "./ui";

/** What the header's Activity button counts: approvals waiting on a human plus unread notifications. */
export function useOpsBadge() {
  const approvals = useAtApprovals("pending");
  const legacy = useApprovals("pending");
  const notes = useAtNotifications();
  const pending = (approvals.data?.length || 0) + (legacy.data?.length || 0);
  return { pending, unread: notes.data?.unread || 0, total: pending + (notes.data?.unread || 0) };
}

const openTask = (id: string) => (go("missions", undefined, { task: id }), closeOpsIfFloating());
let floating = true;
const closeOpsIfFloating = () => floating && closeOps();

/**
 * Live Operations: activity, alerts and approvals in one panel. Closed by default and opened from the header;
 * pinned, it becomes a column next to the page for when a run needs watching.
 */
export function LiveOps({ tab, pinned, onPin }: { tab: OpsTab; pinned: boolean; onPin: (v: boolean) => void }) {
  floating = !pinned;
  const badge = useOpsBadge();
  const notes = useAtNotifications();
  const o = useAtOverview();
  const problems = o.data?.health.problems || [];
  const alerts = (notes.data?.unread || 0) + problems.length;
  useEffect(() => {
    if (tab === "alerts" && notes.data?.unread && !DEMO) {
      post("/api/at/notifications/read", {}).then(refreshAt, () => undefined);
    }
  }, [tab, notes.data?.unread]);
  return (
    <aside className={`ops ${pinned ? "pinned" : "floating"}`} aria-label="Live operations">
      <div className="ops-h">
        <div className="col gap2 grow">
          <span className="ops-title">Live Operations</span>
          <span className="muted xs">{DEMO ? "示例数据快照，不是实时" : "Updates as it happens"}</span>
        </div>
        <button className="btn ghost icon sm" onClick={() => onPin(!pinned)} aria-pressed={pinned} title={pinned ? "Unpin panel" : "Pin next to the page"} aria-label={pinned ? "Unpin" : "Pin"}>
          {pinned ? <PinOff size={15} /> : <Pin size={15} />}
        </button>
        <button className="btn ghost icon sm" onClick={closeOps} aria-label="Close"><X size={16} /></button>
      </div>
      <div className="ops-tabs" role="tablist">
        {([["activity", "Activity", 0], ["alerts", "Alerts", alerts], ["approvals", "Approvals", badge.pending]] as [OpsTab, string, number][]).map(([id, l, n]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => openOps(id)}>
            {l}{n > 0 && <span className={`ops-n ${id === "approvals" ? "acc" : ""}`}>{n > 99 ? "99+" : n}</span>}
          </button>
        ))}
      </div>
      <div className="ops-b">
        {tab === "activity" && <ActivityTab />}
        {tab === "alerts" && <AlertsTab problems={problems} />}
        {tab === "approvals" && <ApprovalsTab />}
      </div>
    </aside>
  );
}

function ActivityTab() {
  const q = useAtActivity({ limit: 50 });
  const [live, setLive] = useState<AtActivity[]>([]);
  useAtLive(useCallback((a: AtActivity) => setLive((l) => [a, ...l.filter((x) => x.id !== a.id)].slice(0, 50)), []));
  const seen = new Set(live.map((a) => a.id));
  const rows = [...live, ...(q.data || []).filter((a) => !seen.has(a.id))].sort((a, b) => b.id - a.id).slice(0, 50);
  if (q.isLoading) return <Loading rows={6} />;
  if (!rows.length) return <EmptyState small title="No activity yet">Worker wake-ups, hand-offs and results appear here as they happen.</EmptyState>;
  return (
    <div className="ops-list">
      {rows.map((a) => (
        <button key={a.id} className={`ops-item ${a.needs_human ? "human" : ""}`} onClick={() => (a.task_id ? openTask(a.task_id) : (go("activity"), closeOpsIfFloating()))}>
          <span className="ops-item-h">
            <Sev s={a.severity} />
            <span className="ops-who ellipsis">{a.agent_name || a.actor_name || a.actor}</span>
            <span className="ops-when" title={fmtTime(a.at)}>{ago(a.at)}</span>
          </span>
          <span className="ops-text">{a.title}</span>
          {a.result && <span className="ops-sub ellipsis">{a.result}</span>}
        </button>
      ))}
      <button className="ops-more" onClick={() => (go("activity"), closeOpsIfFloating())}>View full history <ArrowRight size={12} /></button>
    </div>
  );
}

function AlertsTab({ problems }: { problems: string[] }) {
  const notes = useAtNotifications();
  const items = notes.data?.items || [];
  if (notes.isLoading) return <Loading rows={5} />;
  return (
    <div className="ops-list">
      {problems.map((p) => (
        <button key={p} className="ops-item alert" onClick={() => (go("settings", undefined, { s: "runtime" }), closeOpsIfFloating())}>
          <span className="ops-item-h"><AlertTriangle size={13} className="acc" /><span className="ops-who">System</span></span>
          <span className="ops-text">{p}</span>
        </button>
      ))}
      {items.slice(0, 40).map((n) => (
        <button key={n.id} className={`ops-item ${n.read_at ? "read" : ""}`} onClick={() => (n.task_id ? openTask(n.task_id) : (go("activity"), closeOpsIfFloating()))}>
          <span className="ops-item-h">
            <Sev s={n.severity} />
            <span className="ops-who ellipsis">{n.title}</span>
            <span className="ops-when" title={fmtTime(n.at)}>{ago(n.at)}</span>
          </span>
          {n.body && <span className="ops-sub clamp2">{n.body}</span>}
        </button>
      ))}
      {!problems.length && !items.length && <EmptyState small icon={<Bell size={18} />} title="All quiet">Failures, high-severity findings and system problems show up here.</EmptyState>}
    </div>
  );
}

function ApprovalsTab() {
  const approvals = useAtApprovals("pending");
  const legacy = useApprovals("pending");
  if (approvals.isLoading) return <Loading rows={4} />;
  const a = approvals.data || [];
  const l = legacy.data || [];
  if (!a.length && !l.length) return <EmptyState small icon={<ShieldCheck size={18} />} title="Nothing waiting for you">Patches and Draft PRs that need a human decision appear here.</EmptyState>;
  return (
    <div className="ops-list gap8">
      {a.map((x) => <ApprovalMessage key={x.id} a={x} />)}
      {l.map((x) => (
        <button key={`l${x.id}`} className="ops-item" onClick={() => (x.task_id && go("tasks", x.task_id), closeOpsIfFloating())}>
          <span className="ops-item-h"><ShieldCheck size={13} className="acc" /><span className="ops-who">Earlier workflow</span><span className="ops-when">{ago(x.at)}</span></span>
          <span className="ops-text">{x.title}</span>
        </button>
      ))}
    </div>
  );
}
