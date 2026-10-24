import { useQuery } from "@tanstack/react-query";
import { Blocks, Check, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, post } from "../lib/api";
import { useAction, useMeta } from "../lib/queries";
import type { ChainRecord, Proof, TaskDetail } from "../lib/types";
import { errText, fmtTime, num, short, weiToBnb } from "../lib/util";
import { Addr, Status } from "./ui";

/**
 * Three separate facts, never merged: the agents finished (off-chain), a transaction was broadcast,
 * and the transaction is in a block. Each step lights up only when it is true.
 */
export function TransactionStatus({ proof, compact }: { proof: Proof; compact?: boolean }) {
  const s = proof.state;
  const approved = s === "offchain_completed" || s === "onchain_pending" || s === "onchain_confirmed";
  const sent = s === "onchain_pending" || s === "onchain_confirmed";
  const conf = s === "onchain_confirmed";
  const step = (on: boolean, live: boolean, label: string, sub?: string, tone = "ok") => (
    <div className={`vstep ${on ? `on ${tone}` : live ? "live" : ""}`}>
      <span className="vbar" />
      <span className="vt">{label}</span>
      {!compact && <span className="vs">{sub || "—"}</span>}
    </div>
  );
  return (
    <div className="vsteps" aria-label="Verification">
      {step(approved, s === "awaiting_review", "Off-chain done", approved ? "human approved" : s === "awaiting_review" ? "awaiting review" : undefined, "info")}
      {step(sent, false, "Broadcast", proof.tx_hash ? short(proof.tx_hash, 6) : undefined, "info")}
      {step(conf, s === "onchain_pending", "Confirmed", proof.block ? `block ${num(proof.block)}` : undefined)}
    </div>
  );
}

/** On-chain tab for a mission: the verification trail, anchoring, and an independent re-read of the transaction. */
export function ChainPanel({ d, proof }: { d: TaskDetail; proof?: Proof | null }) {
  const meta = useMeta();
  const anchor = useAction(() => post(`/api/wf/tasks/${d.root.id}/anchor`), ["task", "records", "missions", "ledger"], "已广播到 BSC 测试网");
  const live = d.chain.some((r) => r.status === "confirmed" || r.status === "pending");
  return (
    <div className="col">
      {proof && <TransactionStatus proof={proof} />}
      <div className="note xs">任务审批通过 = <b>链下完成</b>。只有锚定交易被打包进区块，才算 <b>链上确认</b>。锚定的是整个 Mission 的结果哈希（calldata：CSTASK01 ‖ sha256 ‖ 任务号）。</div>
      {d.chain.map((r) => <RecordView key={r.id} r={r} />)}
      {d.root.status === "completed" && !live && (
        <button className="btn primary sm" style={{ alignSelf: "flex-start" }} disabled={!meta.data?.engine.anchor || anchor.isPending} onClick={() => anchor.mutate(undefined)}>
          <Blocks size={12} /> Anchor result on BSC testnet
        </button>
      )}
      {!meta.data?.engine.anchor && <div className="muted xs">没有配置测试网钱包（CHAINSTAFF_ANCHOR_WALLET），不能上链。</div>}
      {d.root.status !== "completed" && !d.chain.length && <div className="muted xs">Mission 审批通过后才能上链。</div>}
    </div>
  );
}

export function RecordView({ r }: { r: ChainRecord }) {
  return (
    <div className="col gap4" style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: 12 }}>
      <div className="row sp"><Status kind="chain" status={r.status} /><span className="muted xs mono">{fmtTime(r.confirmed_at || r.at)}</span></div>
      <dl className="kv">
        <dt>Network</dt><dd>{r.network} ({r.chain_id})</dd>
        <dt>Tx hash</dt><dd><Addr value={r.tx_hash} url={r.tx_url} n={10} /></dd>
        <dt>Block</dt><dd className="mono">{r.block ? num(r.block) : "—"}{r.confirmations ? ` · ${r.confirmations} conf.` : ""}</dd>
        <dt>Payload</dt><dd><Addr value={r.payload_hash} n={10} /></dd>
        <dt>Gas fee</dt><dd className="mono">{r.fee_wei ? `${weiToBnb(r.fee_wei)} tBNB` : "—"}</dd>
        {r.error && <><dt>Error</dt><dd className="bad">{r.error}</dd></>}
      </dl>
      {r.tx_hash && <VerifyButton tx={r.tx_hash} />}
    </div>
  );
}

/** Reads the transaction back from the RPC and compares its calldata with the stored result hash. */
export function VerifyButton({ tx }: { tx: string }) {
  const [on, setOn] = useState(false);
  const q = useQuery({ queryKey: ["verify", tx], queryFn: () => api<{ ok: boolean; reason?: string; block?: number | null; stored_hash?: string }>(`/api/wf/chain/verify/${tx}`), enabled: on, retry: false });
  return (
    <div className="row gap6 xs">
      <button className="btn sm" onClick={() => (on ? q.refetch() : setOn(true))} disabled={q.isFetching}><ShieldCheck size={12} /> Verify against chain</button>
      {q.data && (q.data.ok ? <span className="ok row gap4"><Check size={12} /> 链上哈希与本地结果一致{q.data.block ? ` · block ${num(q.data.block)}` : " · 尚未打包"}</span> : <span className="bad">{q.data.reason || "哈希不一致"}</span>)}
      {q.error && <span className="bad">{errText(q.error)}</span>}
    </div>
  );
}
