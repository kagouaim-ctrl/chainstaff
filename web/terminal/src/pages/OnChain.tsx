import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Play, RefreshCw, Search, X } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { ExecutionSteps } from "../components/Execution";
import { OnChainLedger, PROOF_ORDER, proofCounts } from "../components/OnChainLedger";
import { ChainPanel, VerifyButton } from "../components/TransactionStatus";
import { useWallet } from "../components/WalletStatus";
import { Addr, Avatar, Cell, EmptyState, ErrorState, Json, Loading, PROOF, Sheet, Status, Tabs } from "../components/ui";
import { api, post } from "../lib/api";
import { useAction, useAgentName, useAgents, useChain, useLedger, useMission, useRecords, useTask } from "../lib/queries";
import type { LedgerRow, Proof, ProofState } from "../lib/types";
import { fmtTime, go, num, plural, setParams, short, useIsMobile, weiToBnb, weiToGwei, type Route } from "../lib/util";

type Tab = "ledger" | "tx" | "registry" | "events" | "wallet";

/** The trust layer: which agent did what, what it produced, and whether that result is provably on chain. */
export default function OnChain({ route }: { route: Route }) {
  const tab = (route.params.get("tab") as Tab) || "ledger";
  const mobile = useIsMobile();
  const selected = tab === "ledger" ? route.id : undefined;
  const close = () => go("onchain", undefined, Object.fromEntries(route.params));
  const insp = selected && <Verification id={selected} onClose={close} />;
  return (
    <div className={`term no-rail ${selected && !mobile ? "" : "no-insp"}`}>
      <section className="pane center">
        <NetworkStrip />
        <Tabs<Tab>
          value={tab}
          onChange={(t) => go("onchain", undefined, t === "ledger" ? {} : { tab: t })}
          options={[["ledger", "Execution ledger"], ["tx", "Transactions"], ["registry", "Agent registry"], ["events", "Contract events"], ["wallet", "Wallet lookup"]]}
        />
        {tab === "ledger" && <Ledger route={route} />}
        {tab === "tx" && <TxTable q={route.params.get("q") || ""} />}
        {tab === "registry" && <Registry />}
        {tab === "events" && <SkillRunner skill="contract_events" extra={{ blocks: 1000 }} label="Read events" />}
        {tab === "wallet" && <SkillRunner skill="wallet_analysis" label="Analyze wallet" initial={route.params.get("address") || ""} />}
      </section>
      {selected && !mobile && <aside className="pane insp" aria-label="Verification">{insp}</aside>}
      {selected && mobile && <Sheet onClose={close} tall>{insp}</Sheet>}
    </div>
  );
}

function NetworkStrip() {
  const chain = useChain();
  const wallet = useWallet();
  const rpc = chain.data?.rpc;
  const c = chain.data;
  return (
    <div className="strip">
      <Cell label="Network" value={<span className="v-sm">{c?.network || "—"}</span>} sub={c?.configured ? `chain id ${c.chain_id}` : "未配置 CHAINSTAFF_CHAIN"} />
      <Cell
        label="RPC health"
        value={rpc ? <span className={rpc.ok ? "ok" : "bad"}>{rpc.ok ? "Healthy" : "Down"}</span> : "—"}
        sub={rpc?.ok ? `${rpc.latency_ms}ms` : rpc?.error ? rpc.error.slice(0, 36) : c?.configured ? "checking…" : "no RPC"}
        title={rpc?.error}
      />
      <Cell label="Latest block" value={rpc?.ok ? num(rpc.block) : "—"} sub={rpc?.block_time ? fmtTime(new Date(rpc.block_time * 1000).toISOString()) : undefined} />
      <Cell label="Gas price" value={rpc?.ok ? weiToGwei(rpc.gas_price_wei) : "—"} sub="gwei" />
      <Cell label="Anchor wallet" value={<span className="v-sm">{c?.anchor ? short(c.anchor.address, 4) : "—"}</span>} sub={c?.anchor ? `${c.anchor.bnb ?? "?"} tBNB` : "未配置，不能上链"} />
      <Cell label="Your wallet" value={<span className="v-sm">{wallet.account ? short(wallet.account, 4) : "—"}</span>} sub={wallet.account ? (wallet.chainId === "0x61" ? "BSC testnet" : `chain ${parseInt(wallet.chainId || "0", 16)}`) : wallet.available ? "未连接" : "没有浏览器钱包"} />
      <div className="grow" />
      <div className="row" style={{ padding: "0 12px" }}>
        <button className="btn ghost sm icon" onClick={() => chain.refetch()} aria-label="刷新"><RefreshCw size={13} className={chain.isFetching ? "acc" : ""} /></button>
      </div>
    </div>
  );
}

function Ledger({ route }: { route: Route }) {
  const q = useLedger();
  const filter = (route.params.get("proof") as ProofState | null) || "all";
  const counts = useMemo(() => proofCounts(q.data || []), [q.data]);
  if (q.isLoading) return <Loading rows={8} />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const pick = (r: LedgerRow) => go("onchain", r.mission, Object.fromEntries(route.params));
  return (
    <>
      <div className="qab">
        <button className="chip" aria-pressed={filter === "all"} onClick={() => setParams(route, { proof: null })}>All <span className="n">{counts.all}</span></button>
        {PROOF_ORDER.map((s) => (
          <button key={s} className="chip" aria-pressed={filter === s} onClick={() => setParams(route, { proof: s })}>
            <span className={`ind ${PROOF[s][1] === "ok" ? "ok" : PROOF[s][1] === "acc" ? "warn" : PROOF[s][1] === "info" ? "info" : ""}`} />
            {PROOF[s][0]} <span className="n">{counts[s]}</span>
          </button>
        ))}
        <div className="grow" />
        <span className="muted xs hide-m2">任务完成 ≠ 交易确认：只有被打包进区块才是 On-chain confirmed</span>
      </div>
      <OnChainLedger rows={q.data || []} selected={route.id} onSelect={pick} filter={filter} />
    </>
  );
}

/** Right panel: the chain of custody for one mission result, from agents to block. */
function Verification({ id, onClose }: { id: string; onClose: () => void }) {
  const d = useTask(id);
  const m = useMission(id);
  const name = useAgentName();
  if (d.isLoading || m.isLoading) return <Loading rows={8} />;
  if (d.error) return <ErrorState error={d.error} />;
  const t = d.data!;
  const agents = [...new Set(t.subtasks.map((s) => s.assigned_agent))];
  return (
    <>
      <div className="insp-h">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="col gap4 grow">
            <div className="row gap6">{m.data && <Status kind="proof" status={m.data.proof.state} />}<span className="mono muted xs">{t.root.id}</span></div>
            <h2 className="clamp2">{t.root.title}</h2>
          </div>
          <button className="btn ghost sm icon" onClick={onClose} aria-label="关闭"><X size={14} /></button>
        </div>
        <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => go("workspace", t.root.workspace_id, { m: t.root.id })}>Open mission <ArrowRight size={12} /></button>
      </div>
      <div className="insp-b">
        <div className="insp-sec">
          <span className="label">Proof chain</span>
          <ProofChain agents={agents.length} tasks={t.subtasks} hash={t.root.result_hash} proof={m.data?.proof} />
        </div>
        <div className="insp-sec">
          <span className="label">Who</span>
          {agents.map((a) => (
            <div key={a} className="list-row"><Avatar name={name(a)} /><span className="grow">{name(a)}</span><span className="muted xs">{plural(t.subtasks.filter((s) => s.assigned_agent === a).length, "execution")}</span></div>
          ))}
        </div>
        <div className="insp-sec">
          <span className="label">What they ran</span>
          <ExecutionSteps d={t} onSelect={(sid) => go("tasks", sid)} />
        </div>
        <div className="insp-sec">
          <span className="label">Result</span>
          <dl className="kv"><dt>Mission hash</dt><dd><Addr value={t.root.result_hash} n={10} /></dd><dt>Approved</dt><dd>{t.root.status === "completed" ? `yes · ${fmtTime(t.root.finished_at)}` : t.root.status}</dd></dl>
        </div>
        <div className="insp-sec">
          <span className="label">On chain</span>
          <ChainPanel d={t} proof={m.data?.proof} />
        </div>
      </div>
    </>
  );
}

/**
 * Agent → Task → Result → Verification → Transaction, each link with its real state. Off-chain
 * completion, a pending transaction and a confirmed one stay three different end points.
 */
function ProofChain({ agents, tasks, hash, proof }: { agents: number; tasks: { status: string }[]; hash: string | null; proof?: Proof }) {
  const done = tasks.filter((x) => x.status === "completed").length;
  const st = proof?.state;
  const verify = st === "awaiting_review" ? ["wait", "awaiting review"] : st === "not_completed" ? ["bad", "not approved"] : hash && st !== "in_progress" ? ["ok", "approved"] : ["off", "—"];
  const tx = proof?.chain_status === "confirmed" ? ["ok", `#${proof.block}`] : proof?.chain_status === "pending" ? ["wait", "pending"] : proof?.chain_status === "failed" ? ["bad", "failed"] : st === "offchain_completed" ? ["off", "off-chain only"] : ["off", "not anchored"];
  const links: [string, string, string][] = [
    ["Agent", agents ? "ok" : "off", `${agents}`],
    ["Task", tasks.length && done === tasks.length ? "ok" : tasks.some((x) => x.status === "failed") ? "bad" : tasks.length ? "wait" : "off", `${done}/${tasks.length}`],
    ["Result", hash ? "ok" : "off", hash ? short(hash, 4) : "—"],
    ["Verification", verify[0], verify[1]],
    ["Transaction", tx[0], tx[1]],
  ];
  return (
    <div className="proof-chain">
      {links.map(([k, s, v], i) => (
        <Fragment key={k}>
          {i > 0 && <ArrowRight size={11} className="muted" />}
          <div className={`pc-step ${s}`}><span className="xs muted">{k}</span><span className="xs mono">{v}</span></div>
        </Fragment>
      ))}
    </div>
  );
}

function TxTable({ q: initialQ }: { q: string }) {
  const recs = useRecords();
  const name = useAgentName();
  const ledger = useLedger();
  const mobile = useIsMobile();
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState(initialQ);
  const owner = (task?: string | null) => ledger.data?.find((r) => r.mission === task);
  const rows = useMemo(() => (recs.data || []).filter((r) => (status === "all" || r.status === status) && (!q || (r.tx_hash || "").toLowerCase().includes(q.toLowerCase()) || (r.task_id || "").includes(q))), [recs.data, status, q]);
  if (recs.isLoading) return <Loading />;
  if (recs.error) return <ErrorState error={recs.error} retry={() => recs.refetch()} />;
  return (
    <>
      <div className="qab">
        <div className="search" style={{ width: mobile ? "100%" : 280 }}><Search size={13} /><input className="input" placeholder="交易哈希或 Mission ID" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        {!mobile && ["all", "pending", "confirmed", "failed"].map((s) => <button key={s} className="chip" aria-pressed={status === s} onClick={() => setStatus(s)}>{s === "all" ? "All" : s[0].toUpperCase() + s.slice(1)}</button>)}
      </div>
      {!rows.length ? (
        <EmptyState title="还没有链上交易">审批 Mission 时勾选「Anchor hash on BSC testnet」，结果哈希会作为一笔 0 金额交易上链。</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="dt">
            <thead><tr><th>Time</th><th>Status</th><th>Mission</th><th>Agents</th><th>Tx hash</th><th className="r">Block</th><th className="r">Conf.</th><th className="r">Gas</th><th className="r">Fee (tBNB)</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => {
                const l = owner(r.task_id);
                return (
                  <tr key={r.id}>
                    <td className="mono muted">{fmtTime(r.confirmed_at || r.at)}</td>
                    <td><Status kind="chain" status={r.status} />{r.error && <span className="bad" title={r.error}> !</span>}</td>
                    <td className="ellipsis" style={{ maxWidth: 220 }}>{r.task_id ? <a className="link" href={`#/onchain/${r.task_id}`}>{l?.name || r.task_id}</a> : "—"}</td>
                    <td><span className="avatars">{[...new Set(l?.executions.map((e) => e.agent) || [])].slice(0, 4).map((a) => <Avatar key={a} name={name(a)} />)}</span></td>
                    <td><Addr value={r.tx_hash} url={r.tx_url} /></td>
                    <td className="mono r">{r.block ? num(r.block) : "—"}</td>
                    <td className="mono r">{r.confirmations ?? "—"}</td>
                    <td className="mono r">{num(r.gas_used)}</td>
                    <td className="mono r">{weiToBnb(r.fee_wei)}</td>
                    <td>{r.tx_hash && <VerifyButton tx={r.tx_hash} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Registry() {
  const q = useQuery({ queryKey: ["registry"], queryFn: () => api<{ id: string; name: string; role: string; status: string; onchain: Record<string, string>; address_url: string | null }[]>("/api/wf/registry") });
  const agents = useAgents();
  const ledger = useLedger();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const stats = (id: string) => {
    const rows = (ledger.data || []).filter((r) => r.executions.some((e) => e.agent === id));
    return { runs: rows.reduce((n, r) => n + r.executions.filter((e) => e.agent === id).length, 0), confirmed: rows.filter((r) => r.proof.state === "onchain_confirmed").length };
  };
  return (
    <>
      <div className="note" style={{ margin: 12 }}>
        身份来自员工档案：ERC-8004 编号和地址由你填写（可以用 <span className="mono">scripts/register_agent.py</span> 在测试网注册）。终端还没有读 ERC-8004 合约做链上核验，所以这里不标「已验证」。
      </div>
      <div className="table-wrap">
        <table className="dt">
          <thead><tr><th>Agent</th><th>Role</th><th>Status</th><th>ERC-8004 ID</th><th>Address</th><th className="r">Executions</th><th className="r">Confirmed missions</th><th>Identity</th></tr></thead>
          <tbody>
            {q.data!.map((a) => {
              const s = stats(a.id);
              const live = agents.data?.find((x) => x.id === a.id);
              return (
                <tr key={a.id} className="click" onClick={() => go("terminal", a.id)}>
                  <td><span className="row gap6"><Avatar name={a.name} /><span className="strong">{a.name}</span></span></td>
                  <td className="dim">{a.role || "—"}</td>
                  <td><Status status={live?.live === "busy" ? "running" : a.status === "active" ? "completed" : "cancelled"} label={live?.live === "busy" ? "Running" : a.status === "active" ? "Active" : a.status} /></td>
                  <td className="mono">{a.onchain.agent_id || "—"}</td>
                  <td><Addr value={a.onchain.address} url={a.address_url} /></td>
                  <td className="mono r">{s.runs}</td>
                  <td className="mono r">{s.confirmed}</td>
                  <td className="muted xs">{a.onchain.agent_id ? "self-declared" : "not registered"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Run a read-only skill straight from the terminal, on real RPC data. */
function SkillRunner({ skill, extra, label, initial = "" }: { skill: string; extra?: Record<string, unknown>; label: string; initial?: string }) {
  const [address, setAddress] = useState(initial);
  const [out, setOut] = useState<any>(null);
  const run = useAction(() => post(`/api/wf/skills/${skill}/run`, { input: { address, ...extra } }), []);
  const valid = /^0x[0-9a-fA-F]{40}$/.test(address);
  return (
    <div className="scroll">
      <div className="section col">
        <div className="row wrap">
          <input className="input mono" style={{ width: 420, maxWidth: "100%" }} placeholder="0x… address" value={address} onChange={(e) => setAddress(e.target.value.trim())} />
          <button className="btn primary sm" disabled={!valid || run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (r: any) => setOut(r) })}><Play size={12} /> {label}</button>
          <span className="muted xs">直接读 RPC，不经过任何员工，不写链。</span>
        </div>
        {out && skill === "contract_events" && (
          <>
            <div className="muted sm">blocks {out.output.from_block}–{out.output.to_block} · {out.output.count} logs · {Object.entries(out.output.by_event).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}</div>
            <table className="dt inner">
              <thead><tr><th className="r">Block</th><th>Event</th><th>From</th><th>To</th><th>Value (raw)</th><th>Tx</th></tr></thead>
              <tbody>
                {out.output.recent.map((e: any, i: number) => (
                  <tr key={i}><td className="mono r">{e.block}</td><td><span className="tag">{e.event}</span></td><td><Addr value={e.from} n={4} /></td><td><Addr value={e.to} n={4} /></td><td className="mono">{e.value_raw ?? "—"}</td><td><Addr value={e.tx_hash} n={6} /></td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {out && skill === "wallet_analysis" && (
          <dl className="kv">
            <dt>Network</dt><dd>{out.output.network} · block {num(out.output.block)}</dd>
            <dt>BNB</dt><dd className="mono">{out.output.bnb}</dd>
            <dt>Nonce</dt><dd className="mono">{out.output.nonce}</dd>
            <dt>Type</dt><dd>{out.output.is_contract ? `Contract · ${out.output.code_size} bytes` : "EOA"}</dd>
            {Object.entries(out.output.tokens || {}).map(([s, v]) => <span key={s} style={{ display: "contents" }}><dt>{s}</dt><dd className="mono">{String(v)}</dd></span>)}
          </dl>
        )}
        {out && (
          <details className="log">
            <summary className="muted">Execution log</summary>
            <pre>{out.logs.join("\n")}</pre>
          </details>
        )}
        {out && skill !== "contract_events" && skill !== "wallet_analysis" && <Json value={out.output} />}
      </div>
    </div>
  );
}
