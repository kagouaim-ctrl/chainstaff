import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { useAgentName } from "../lib/queries";
import type { LedgerRow, ProofState } from "../lib/types";
import { ago, fmtTime, go, num, plural, short } from "../lib/util";
import { Addr, Avatar, EmptyState, PROOF, Status } from "./ui";

export const PROOF_ORDER: ProofState[] = ["onchain_confirmed", "onchain_pending", "offchain_completed", "awaiting_review", "not_completed"];

/**
 * Execution ledger: one row per mission result, expandable to the agent executions inside it.
 * Columns answer, left to right: who did the work, what it was, what it produced, whether it went on chain, and where.
 */
export function OnChainLedger({ rows, selected, onSelect, filter }: { rows: LedgerRow[]; selected?: string | null; onSelect: (r: LedgerRow) => void; filter: ProofState | "all" }) {
  const name = useAgentName();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const visible = useMemo(() => rows.filter((r) => filter === "all" || r.proof.state === filter), [rows, filter]);
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  if (!visible.length)
    return <EmptyState title={rows.length ? "这个状态下没有记录" : "还没有执行记录"}>Mission 结束后，它的每次执行、结果哈希和上链状态都会出现在这里。</EmptyState>;
  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr>
            <th style={{ width: 28 }} />
            <th>Mission / execution</th>
            <th className="hide-m">Agent</th>
            <th className="hide-m">Result hash</th>
            <th>Verification</th>
            <th>Transaction</th>
            <th className="r hide-m">Block</th>
            <th className="r hide-m">When</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((r) => {
            const isOpen = open.has(r.mission);
            return (
              <Fragment key={r.mission}>
                <tr className="click group" aria-selected={selected === r.mission} onClick={() => onSelect(r)}>
                  <td onClick={(e) => (e.stopPropagation(), toggle(r.mission))} className="c">
                    <button className="btn ghost xs icon" aria-label={isOpen ? "收起" : "展开"} aria-expanded={isOpen}>{isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
                  </td>
                  <td>
                    <span className="col" style={{ gap: 0, maxWidth: "min(340px, 46vw)" }}>
                      <span className="strong ellipsis" title={r.name}>{r.name}</span>
                      <span className="sub">{r.mission} · {plural(r.executions.length, "execution")}</span>
                    </span>
                  </td>
                  <td className="hide-m">
                    <span className="avatars">{[...new Set(r.executions.map((e) => e.agent))].slice(0, 5).map((a) => <Avatar key={a} name={name(a)} title={name(a)} />)}</span>
                  </td>
                  <td className="hide-m"><Addr value={r.result_hash} n={6} /></td>
                  <td><Status kind="proof" status={r.proof.state} /></td>
                  <td>{r.proof.tx_hash ? <Addr value={r.proof.tx_hash} url={r.proof.tx_url} n={6} /> : <span className="muted">{r.proof.error ? <span className="bad" title={r.proof.error}>anchor failed</span> : "not submitted"}</span>}</td>
                  <td className="r mono hide-m">{r.proof.block ? num(r.proof.block) : "—"}</td>
                  <td className="r mono muted hide-m" title={fmtTime(r.finished_at)}>{ago(r.finished_at)}</td>
                </tr>
                {isOpen && r.executions.map((e) => (
                  <tr key={e.task} className="click child" onClick={() => go("tasks", e.task)}>
                    <td />
                    <td>
                      <span className="col" style={{ gap: 0, maxWidth: "min(320px, 42vw)" }}>
                        <span className="ellipsis" title={e.title}>{e.title}</span>
                        <span className="sub">{e.task} · {e.skill}</span>
                      </span>
                    </td>
                    <td className="hide-m"><span className="row gap6"><Avatar name={name(e.agent)} /><span className="ellipsis">{name(e.agent)}</span></span></td>
                    <td className="hide-m">{e.result_hash ? <span className="mono dim" title={e.result_hash}>{short(e.result_hash, 6)}</span> : <span className="bad xs ellipsis" title={e.error || ""}>{e.error ? "no result" : "—"}</span>}</td>
                    <td><Status status={e.status} label={e.status === "completed" ? "Off-chain done" : undefined} /></td>
                    <td className="muted xs">{e.status === "completed" ? `feeds the mission result${r.proof.state === "onchain_confirmed" ? " (anchored)" : ""}` : "no output"}</td>
                    <td className="hide-m" />
                    <td className="r mono muted hide-m">{ago(e.finished_at)}</td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function proofCounts(rows: LedgerRow[]): Record<ProofState | "all", number> {
  const out = { all: rows.length } as Record<ProofState | "all", number>;
  for (const s of Object.keys(PROOF) as ProofState[]) out[s] = rows.filter((r) => r.proof.state === s).length;
  return out;
}
