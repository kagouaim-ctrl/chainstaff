import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { decideApproval, useAction, useAgentName, useMeta } from "../lib/queries";
import type { Approval, Task } from "../lib/types";
import { ago, short, usd } from "../lib/util";

/** A human decision, inline where the work is. No modal: the evidence is on the same screen. */
export function ApprovalPanel({ approval, root }: { approval: Approval; root?: Task }) {
  const meta = useMeta();
  const name = useAgentName();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const canAnchor = approval.kind === "task_result" && !!meta.data?.engine.anchor;
  const [anchor, setAnchor] = useState(canAnchor);
  const decide = useAction((approve: boolean) => decideApproval(approval.id, approve, note, approve && anchor && canAnchor), ["approvals", "task", "tasks", "missions", "records", "ledger"]);
  const after = () => qc.invalidateQueries({ queryKey: ["overview"] });
  const d = approval.detail || {};
  const budget = approval.kind === "over_budget";
  const report: string | undefined = root?.result?.report_md;
  const findings: string[] = root?.result?.findings || [];
  return (
    <div className="approval" role="region" aria-label={`审批 #${approval.id}`}>
      <div className="approval-h">
        <ShieldCheck size={14} className="acc" />
        <span className="strong sm grow ellipsis">{budget ? "Budget exhausted — continue?" : "Result ready for your review"}</span>
        <span className="muted xs">#{approval.id} · {name(approval.requested_by)} · {ago(approval.at)}</span>
      </div>
      <div className="approval-b">
        <div className="sm">{approval.title}</div>
        <div className="row wrap gap12 xs">
          {budget ? (
            <>
              <span><span className="muted">spent</span> <span className="mono">{usd(d.cost_usd)}</span></span>
              <span><span className="muted">budget</span> <span className="mono">{usd(d.budget_usd)}</span></span>
              <span className="muted">通过 = 追加同等预算继续；驳回 = 结束 Mission</span>
            </>
          ) : (
            <>
              <span><span className="muted">tasks done</span> <span className="mono ok">{d.completed ?? "—"}</span></span>
              <span><span className="muted">not done</span> <span className={`mono ${d.not_completed ? "bad" : ""}`}>{d.not_completed ?? 0}</span></span>
              <span><span className="muted">cost</span> <span className="mono">{usd(d.cost_usd)}</span></span>
              {d.result_hash && <span><span className="muted">hash</span> <span className="mono">{short(d.result_hash, 6)}</span></span>}
            </>
          )}
        </div>
        {findings.length > 0 && <div className="col gap2 xs">{findings.slice(0, 5).map((f, i) => <span key={i}>• {f}</span>)}</div>}
        {report && (
          <details className="log">
            <summary className="xs muted">Report preview</summary>
            <pre style={{ maxHeight: 220 }}>{report}</pre>
          </details>
        )}
      </div>
      <div className="approval-f">
        <input className="input sm grow" placeholder="备注（写进审批记录）" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 160 }} />
        {approval.kind === "task_result" && (
          <label className="check xs" title={canAnchor ? "" : "没有配置测试网钱包"}>
            <input type="checkbox" checked={anchor && canAnchor} disabled={!canAnchor} onChange={(e) => setAnchor(e.target.checked)} />
            Anchor hash on BSC testnet
          </label>
        )}
        <button className="btn sm danger" disabled={decide.isPending} onClick={() => decide.mutate(false, { onSuccess: after })}>Reject</button>
        <button className="btn sm primary" disabled={decide.isPending} onClick={() => decide.mutate(true, { onSuccess: after })}>
          {budget ? "Approve budget" : anchor && canAnchor ? "Approve & anchor" : "Approve"}
        </button>
      </div>
    </div>
  );
}
