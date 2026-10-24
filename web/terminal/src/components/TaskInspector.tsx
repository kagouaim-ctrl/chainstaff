import { useQueryClient } from "@tanstack/react-query";
import { Ban, Blocks, RotateCcw, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { post } from "../lib/api";
import { decideApproval, useAction, useAgentName, useMeta, useTask } from "../lib/queries";
import type { Approval, ChainRecord, Task, TaskDetail } from "../lib/types";
import { duration, fmtTime, short, usd } from "../lib/util";
import { Addr, Dialog, ErrorState, Loading, Priority, Seg, StatusBadge } from "./ui";

type Tab = "overview" | "graph" | "messages" | "logs" | "result" | "chain";

export function TaskInspector({ id, onClose }: { id: string; onClose?: () => void }) {
  const q = useTask(id);
  const [tab, setTab] = useState<Tab>("overview");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const root = d.root;
  return (
    <div className="col" style={{ gap: 0 }}>
      <div className="ctx-head">
        <div className="col grow" style={{ gap: 2 }}>
          <div className="row">
            <StatusBadge status={root.status} />
            <Priority p={root.priority} />
            <span className="mono muted">{root.id}</span>
          </div>
          <h2 className="ellipsis" title={root.title}>{root.title}</h2>
        </div>
        {onClose && (
          <button className="btn ghost sm" onClick={onClose} aria-label="收起">
            ✕
          </button>
        )}
      </div>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)", overflowX: "auto" }}>
        <Seg<Tab>
          value={tab}
          onChange={setTab}
          options={[["overview", "Overview"], ["graph", "Graph"], ["messages", "Messages"], ["logs", "Logs"], ["result", "Result"], ["chain", "On-chain"]]}
        />
      </div>
      <div style={{ padding: 12 }} className="col">
        {tab === "overview" && <OverviewTab d={d} />}
        {tab === "graph" && <GraphTab d={d} />}
        {tab === "messages" && <MessagesTab d={d} />}
        {tab === "logs" && <LogsTab d={d} />}
        {tab === "result" && <ResultTab root={root} chain={d.chain} />}
        {tab === "chain" && <ChainTab d={d} />}
      </div>
    </div>
  );
}

function OverviewTab({ d }: { d: TaskDetail }) {
  const name = useAgentName();
  const root = d.root;
  const [ap, setAp] = useState<Approval | null>(null);
  const cancel = useAction(() => post(`/api/wf/tasks/${root.id}/cancel`, {}), ["task", "tasks"], "已取消");
  const retry = useAction((tid: string) => post(`/api/wf/tasks/${tid}/retry`), ["task", "tasks"], "已重新排队");
  const pending = d.approvals.filter((a) => a.status === "pending");
  const waitingOn = (s: Task) => s.depends_on.filter((x) => d.subtasks.find((y) => y.id === x && !["completed", "failed", "cancelled"].includes(y.status)));
  const open = !["completed", "failed", "cancelled"].includes(root.status);
  return (
    <>
      {pending.map((a) => (
        <div key={a.id} className="note row sp" style={{ borderColor: "rgba(217,164,65,.5)" }}>
          <span className="row">
            <ShieldCheck size={14} className="warn" />
            {a.title}
          </span>
          <button className="btn sm primary" onClick={() => setAp(a)}>
            审批
          </button>
        </div>
      ))}
      <dl className="kv">
        <dt>Manager</dt>
        <dd>{name(root.assigned_agent)}</dd>
        <dt>Planner</dt>
        <dd>{root.planner === "model" ? "Model" : root.planner === "rules" ? "Rules (no model)" : "—"}</dd>
        <dt>Steps</dt>
        <dd className="mono">
          {root.steps} / {root.max_steps}
        </dd>
        <dt>Cost / budget</dt>
        <dd className="mono">
          {usd(root.cost_usd)} / {usd(root.budget_usd)} · {root.tokens} tok
        </dd>
        <dt>Created</dt>
        <dd>{fmtTime(root.created_at)}</dd>
        <dt>Duration</dt>
        <dd className="mono">{duration(root.created_at, root.finished_at)}</dd>
        <dt>Deadline</dt>
        <dd>{fmtTime(root.deadline_at)}</dd>
        {root.error && (
          <>
            <dt>Error</dt>
            <dd className="bad">{root.error}</dd>
          </>
        )}
      </dl>
      <h3>Execution steps · {d.subtasks.length}</h3>
      <div className="steps">
        {d.subtasks.map((s) => {
          const w = waitingOn(s);
          return (
            <div key={s.id} className="step">
              <span className={`dot ${s.status === "completed" ? "ok" : s.status === "failed" ? "bad" : s.status === "running" || s.status === "assigned" ? "acc" : "muted"}`} style={{ width: 8, height: 8, borderRadius: 4, background: "currentColor" }} />
              <div className="col" style={{ gap: 0 }}>
                <span className="ellipsis">{s.title}</span>
                <span className="muted" style={{ fontSize: 11 }}>
                  {name(s.assigned_agent)} · <span className="mono">{s.skill}</span>
                  {s.attempts > 1 && ` · attempt ${s.attempts}`}
                  {w.length > 0 && s.status === "pending" && ` · waiting on ${w.length}`}
                </span>
                {s.error && <span className="bad" style={{ fontSize: 11 }}>{s.error}</span>}
              </div>
              <div className="row">
                <StatusBadge status={s.status} />
                {s.status === "failed" && open !== undefined && (
                  <button className="btn ghost sm icon" title="重试" aria-label="重试" onClick={() => retry.mutate(s.id)}>
                    <RotateCcw size={12} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {open && (
        <button className="btn danger" onClick={() => confirm("取消这个任务和它所有未完成的子任务？") && cancel.mutate(undefined)}>
          <Ban size={14} />
          取消任务
        </button>
      )}
      {ap && <ApprovalDialog approval={ap} root={root} onClose={() => setAp(null)} />}
    </>
  );
}

function GraphTab({ d }: { d: TaskDetail }) {
  const name = useAgentName();
  // Lay out by dependency depth: one column per level, so parallel work sits side by side.
  const lanes = useMemo(() => {
    const depth: Record<string, number> = {};
    const byId = Object.fromEntries(d.subtasks.map((s) => [s.id, s]));
    const f = (id: string, seen = new Set<string>()): number => {
      if (depth[id] !== undefined) return depth[id];
      if (seen.has(id)) return 0;
      seen.add(id);
      const deps = byId[id]?.depends_on.filter((x) => byId[x]) || [];
      return (depth[id] = deps.length ? 1 + Math.max(...deps.map((x) => f(x, seen))) : 0);
    };
    d.subtasks.forEach((s) => f(s.id));
    const out: Task[][] = [];
    d.subtasks.forEach((s) => (out[depth[s.id]] ||= []).push(s));
    return out;
  }, [d.subtasks]);
  if (!d.subtasks.length) return <div className="muted">还没有子任务</div>;
  return (
    <div className="graph">
      {lanes.map((lane, i) => (
        <div key={i} className="col" style={{ gap: 4 }}>
          {i > 0 && <span className="arrow">↓</span>}
          <span className="label">{i === 0 ? `Level 0 · ${lane.length} in parallel` : `Level ${i} · after level ${i - 1}`}</span>
          <div className="lane">
            {lane.map((s) => (
              <div key={s.id} className={`node ${s.status}`} title={s.depends_on.length ? `depends on ${s.depends_on.join(", ")}` : "no dependencies"}>
                <div className="ellipsis" style={{ fontWeight: 500 }}>{s.title}</div>
                <div className="row sp">
                  <span className="muted ellipsis">{name(s.assigned_agent)}</span>
                  <StatusBadge status={s.status} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function MessagesTab({ d }: { d: TaskDetail }) {
  const name = useAgentName();
  const rows = d.messages.filter((m) => m.recipient);
  return (
    <table className="dt">
      <thead>
        <tr>
          <th>#</th>
          <th>Type</th>
          <th>From → To</th>
          <th>Status</th>
          <th className="r">Tries</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.id} title={m.text}>
            <td className="mono muted">{m.id}</td>
            <td><span className="tag">{m.event}</span></td>
            <td className="ellipsis">
              {name(m.sender)} → {name(m.recipient)}
            </td>
            <td><span className={`badge ${m.status === "processed" ? "ok" : m.status === "expired" ? "bad" : "warn"}`}>{m.status}</span></td>
            <td className="r mono">{m.attempts}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LogsTab({ d }: { d: TaskDetail }) {
  const tools = d.messages.filter((m) => m.kind === "tool" && m.event !== "tool.start");
  if (!tools.length) return <div className="muted">还没有工具执行</div>;
  return (
    <div className="col">
      {tools.map((m) => (
        <details key={m.id} className="ev" style={{ padding: 0, display: "block" }}>
          <summary className="row" style={{ padding: "4px 8px", cursor: "pointer" }}>
            <span className={m.event === "tool.error" ? "bad" : "ok"}>●</span>
            <span className="mono">{m.data?.skill}</span>
            <span className="muted ellipsis">{m.text}</span>
          </summary>
          <pre style={{ margin: 0, padding: 8, font: "11px/1.5 var(--mono)", whiteSpace: "pre-wrap", color: "var(--text-2)", borderTop: "1px solid var(--line)" }}>
            {(m.data?.logs || []).join("\n")}
            {m.data?.error ? `\n✕ ${m.data.error}` : ""}
          </pre>
        </details>
      ))}
    </div>
  );
}

function ResultTab({ root, chain }: { root: Task; chain: ChainRecord[] }) {
  const onchain = chain.find((r) => r.status === "confirmed") || chain.find((r) => r.status === "pending");
  if (!root.result) return <div className="muted">任务还没有汇总结果</div>;
  return (
    <>
      <dl className="kv">
        <dt>Result hash</dt>
        <dd>
          <Addr value={root.result_hash} n={10} />
        </dd>
        <dt>Status</dt>
        <dd>
          {root.status === "completed" ? "已审批（链下完成）" : root.status === "review" ? "等待审批" : root.status}
          {onchain && (onchain.status === "confirmed" ? ` · 链上已确认，区块 ${onchain.block}` : " · 链上交易等待打包")}
        </dd>
      </dl>
      {root.result.findings?.length > 0 && (
        <div className="note">
          {root.result.findings.map((f: string, i: number) => (
            <div key={i}>• {f}</div>
          ))}
        </div>
      )}
      <div className="report">{root.result.report_md || JSON.stringify(root.result, null, 1)}</div>
    </>
  );
}

function ChainTab({ d }: { d: TaskDetail }) {
  const meta = useMeta();
  const anchor = useAction(() => post(`/api/wf/tasks/${d.root.id}/anchor`), ["task", "records"], "已广播到 BSC 测试网");
  const confirmed = d.chain.some((r) => r.status === "confirmed" || r.status === "pending");
  return (
    <>
      <div className="note">
        链下完成和链上确认是两件事：任务审批通过是<b>链下完成</b>；只有交易被打包进区块，才算<b>链上确认</b>。
      </div>
      {d.chain.map((r) => (
        <dl key={r.id} className="kv" style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
          <dt>Status</dt>
          <dd><StatusBadge kind="chain" status={r.status} /></dd>
          <dt>Network</dt>
          <dd>{r.network} ({r.chain_id})</dd>
          <dt>Tx hash</dt>
          <dd><Addr value={r.tx_hash} url={r.tx_url} n={10} /></dd>
          <dt>Block</dt>
          <dd className="mono">{r.block ?? "—"}</dd>
          <dt>Gas fee</dt>
          <dd className="mono">{r.fee_wei ? `${(Number(r.fee_wei) / 1e18).toFixed(8)} tBNB` : "—"}</dd>
          <dt>Payload</dt>
          <dd className="mono">{short(r.payload_hash, 10)}</dd>
          <dt>Time</dt>
          <dd>{fmtTime(r.confirmed_at || r.at)}</dd>
          {r.error && (
            <>
              <dt>Error</dt>
              <dd className="bad">{r.error}</dd>
            </>
          )}
        </dl>
      ))}
      {d.root.status === "completed" && !confirmed && (
        <button className="btn primary" disabled={!meta.data?.engine.anchor || anchor.isPending} onClick={() => anchor.mutate(undefined)}>
          <Blocks size={14} />
          写入 BSC 测试网
        </button>
      )}
      {!meta.data?.engine.anchor && <div className="muted" style={{ fontSize: 12 }}>没有配置测试网钱包（CHAINSTAFF_ANCHOR_WALLET），不能上链。</div>}
    </>
  );
}

export function ApprovalDialog({ approval, root, onClose }: { approval: Approval; root?: Task; onClose: () => void }) {
  const meta = useMeta();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [anchor, setAnchor] = useState(approval.kind === "task_result" && !!meta.data?.engine.anchor);
  const decide = useAction((approve: boolean) => decideApproval(approval.id, approve, note, approve && anchor), ["approvals", "task", "tasks", "records"]);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["overview"] });
    onClose();
  };
  return (
    <Dialog
      title={`审批 #${approval.id}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn danger" disabled={decide.isPending} onClick={() => decide.mutate(false, { onSuccess: done })}>
            驳回
          </button>
          <button className="btn primary" disabled={decide.isPending} onClick={() => decide.mutate(true, { onSuccess: done })}>
            {anchor ? "通过并上链" : "通过"}
          </button>
        </>
      }
    >
      <div>{approval.title}</div>
      <dl className="kv">
        <dt>Type</dt>
        <dd>{approval.kind}</dd>
        {Object.entries(approval.detail).map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd className="mono">{String(v)}</dd>
          </span>
        ))}
      </dl>
      {root?.result?.report_md && <div className="report" style={{ maxHeight: 240 }}>{root.result.report_md}</div>}
      <label className="field">
        <span>备注（写进审批历史）</span>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {approval.kind === "task_result" && (
        <label className="check">
          <input type="checkbox" checked={anchor} disabled={!meta.data?.engine.anchor} onChange={(e) => setAnchor(e.target.checked)} />
          同时把结果哈希写入 BSC 测试网（0 金额交易，需要测试网钱包）
        </label>
      )}
    </Dialog>
  );
}
