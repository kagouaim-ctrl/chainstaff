import { Play, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Addr, EmptyState, ErrorState, Loading, Seg, Stat, StatusBadge } from "../components/ui";
import { api, post } from "../lib/api";
import { useAction, useChain, useMeta, useRecords } from "../lib/queries";
import type { ChainRecord } from "../lib/types";
import { fmtTime, go, num, short, useIsMobile, weiToBnb, weiToGwei, type Route } from "../lib/util";
import { useWallet } from "../components/WalletStatus";
import { useQuery } from "@tanstack/react-query";

type Tab = "tx" | "registry" | "events" | "wallet";

export default function OnChain({ route }: { route: Route }) {
  const chain = useChain();
  const meta = useMeta();
  const wallet = useWallet();
  const [tab, setTab] = useState<Tab>("tx");
  const rpc = chain.data?.rpc;
  return (
    <div className="page">
      <div className="page-head">
        <h1>On-Chain</h1>
        <span className="badge acc">{chain.data?.network || "no chain"}{chain.data?.chain_id ? ` · ${chain.data.chain_id}` : ""}</span>
        <div className="grow" />
        <button className="btn icon" onClick={() => chain.refetch()} aria-label="刷新"><RefreshCw size={14} className={chain.isFetching ? "acc" : ""} /></button>
      </div>
      <div className="stats">
        <Stat label="Network" value={<span style={{ fontSize: 14 }}>{chain.data?.network || "—"}</span>} sub={chain.data?.configured ? `chain id ${chain.data.chain_id}` : "未配置 CHAINSTAFF_CHAIN"} />
        <Stat label="RPC" value={rpc ? (rpc.ok ? <span className="ok">OK</span> : <span className="bad">Down</span>) : "—"} sub={rpc?.ok ? `${rpc.latency_ms}ms` : rpc?.error?.slice(0, 40)} />
        <Stat label="Latest block" value={rpc?.ok ? num(rpc.block) : "—"} sub={rpc?.block_time ? fmtTime(new Date(rpc.block_time * 1000).toISOString()) : undefined} />
        <Stat label="Gas price" value={rpc?.ok ? `${weiToGwei(rpc.gas_price_wei)}` : "—"} sub="gwei" />
        <Stat label="Anchor wallet" value={<span style={{ fontSize: 13 }}>{chain.data?.anchor ? short(chain.data.anchor.address, 4) : "—"}</span>} sub={chain.data?.anchor ? `${chain.data.anchor.bnb ?? "?"} tBNB` : "未配置，不能上链"} />
        <Stat label="Your wallet" value={<span style={{ fontSize: 13 }}>{wallet.account ? short(wallet.account, 4) : "—"}</span>} sub={wallet.account ? (wallet.chainId === "0x61" ? "BSC testnet" : `chain ${parseInt(wallet.chainId || "0", 16)}`) : wallet.available ? "未连接" : "没有浏览器钱包"} />
      </div>
      <div className="filters">
        <Seg value={tab} onChange={setTab} options={[["tx", "Task transactions"], ["registry", "Agent registry"], ["events", "Contract events"], ["wallet", "Wallet analytics"]]} />
      </div>
      {tab === "tx" && <TxTable q={route.params.get("q") || ""} />}
      {tab === "registry" && <Registry explorer={meta.data?.explorer || null} />}
      {tab === "events" && <SkillRunner skill="contract_events" extra={{ blocks: 1000 }} label="Read events" />}
      {tab === "wallet" && <SkillRunner skill="wallet_analysis" label="Analyze wallet" />}
    </div>
  );
}

function TxTable({ q: initialQ }: { q: string }) {
  const recs = useRecords();
  const mobile = useIsMobile();
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState(initialQ);
  const rows = useMemo(() => (recs.data || []).filter((r) => (status === "all" || r.status === status) && (!q || (r.tx_hash || "").includes(q.toLowerCase()) || (r.task_id || "").includes(q))), [recs.data, status, q]);
  if (recs.isLoading) return <Loading />;
  if (recs.error) return <ErrorState error={recs.error} retry={() => recs.refetch()} />;
  return (
    <>
      <div className="filters">
        <input className="input" placeholder="交易哈希或任务 ID" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: mobile ? "100%" : 280 }} />
        <Seg value={status} onChange={setStatus} options={[["all", "All"], ["pending", "Pending"], ["confirmed", "Confirmed"], ["failed", "Failed"]]} />
        <span className="muted" style={{ fontSize: 12 }}>只有被打包进区块的交易才是「链上确认」；任务审批通过只代表链下完成。</span>
      </div>
      {!rows.length ? (
        <EmptyState title="还没有链上记录">任务审批时勾选「写入 BSC 测试网」，结果哈希会作为一笔 0 金额交易上链。</EmptyState>
      ) : mobile ? (
        <div className="m-list">
          {rows.map((r) => <MobileTx key={r.id} r={r} />)}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="dt">
            <thead>
              <tr><th>Time</th><th className="r">Block</th><th>Type</th><th>Task</th><th>From</th><th>Tx hash</th><th className="r">Gas</th><th className="r">Fee (tBNB)</th><th className="r">Conf.</th><th>Status</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="mono muted">{fmtTime(r.confirmed_at || r.at)}</td>
                  <td className="mono r">{r.block ?? "—"}</td>
                  <td><span className="tag">{r.kind}</span></td>
                  <td>{r.task_id ? <a className="mono" href={`#/tasks/${r.task_id}`} onClick={(e) => (e.preventDefault(), go("tasks", r.task_id!))}>{r.task_id}</a> : "—"}</td>
                  <td><Addr value={r.from_addr} n={4} /></td>
                  <td><Addr value={r.tx_hash} url={r.tx_url} /></td>
                  <td className="mono r">{num(r.gas_used)}</td>
                  <td className="mono r">{weiToBnb(r.fee_wei)}</td>
                  <td className="mono r">{r.confirmations ?? "—"}</td>
                  <td><StatusBadge kind="chain" status={r.status} />{r.error && <span className="bad" title={r.error}> !</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function MobileTx({ r }: { r: ChainRecord }) {
  return (
    <div className="m-card">
      <div className="row sp"><StatusBadge kind="chain" status={r.status} /><span className="mono muted" style={{ fontSize: 11 }}>{fmtTime(r.at)}</span></div>
      <Addr value={r.tx_hash} url={r.tx_url} />
      <div className="row sp muted mono" style={{ fontSize: 11 }}><span>block {r.block ?? "—"}</span><span>{weiToBnb(r.fee_wei)} tBNB</span></div>
      {r.task_id && <a className="mono" style={{ fontSize: 11 }} href={`#/tasks/${r.task_id}`}>{r.task_id}</a>}
    </div>
  );
}

function Registry({ explorer }: { explorer: string | null }) {
  const q = useQuery({ queryKey: ["registry"], queryFn: () => api<{ id: string; name: string; role: string; status: string; onchain: Record<string, string>; address_url: string | null }[]>("/api/wf/registry") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  return (
    <>
      <div className="note" style={{ margin: "8px 16px" }}>
        身份记录来自员工档案（ERC-8004 编号和地址由你填写，可用 <span className="mono">scripts/register_agent.py</span> 在测试网注册）。这里还没有读取 ERC-8004 合约做链上核验。
      </div>
      <table className="dt">
        <thead><tr><th>Agent</th><th>Role</th><th>Status</th><th>ERC-8004 ID</th><th>Address</th></tr></thead>
        <tbody>
          {q.data!.map((a) => (
            <tr key={a.id} className="click" onClick={() => go("workforce", a.id)}>
              <td style={{ fontWeight: 500 }}>{a.name}</td>
              <td className="dim">{a.role || "—"}</td>
              <td><StatusBadge kind="agent" status={a.status} /></td>
              <td className="mono">{a.onchain.agent_id || "—"}</td>
              <td><Addr value={a.onchain.address} url={a.address_url || (a.onchain.address && explorer ? explorer.replace("/tx/", "/address/") + a.onchain.address : null)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** Run a read-only skill straight from the terminal, on real RPC data. */
function SkillRunner({ skill, extra, label }: { skill: string; extra?: Record<string, unknown>; label: string }) {
  const [address, setAddress] = useState("");
  const [out, setOut] = useState<any>(null);
  const run = useAction(() => post(`/api/wf/skills/${skill}/run`, { input: { address, ...extra } }), []);
  const valid = /^0x[0-9a-fA-F]{40}$/.test(address);
  return (
    <div className="section col">
      <div className="row wrap">
        <input className="input mono" style={{ width: 420, maxWidth: "100%" }} placeholder="0x… address" value={address} onChange={(e) => setAddress(e.target.value.trim())} />
        <button className="btn primary" disabled={!valid || run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (r: any) => setOut(r) })}>
          <Play size={12} /> {label}
        </button>
      </div>
      {out && skill === "contract_events" && (
        <>
          <div className="muted">blocks {out.output.from_block}–{out.output.to_block} · {out.output.count} logs · {Object.entries(out.output.by_event).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}</div>
          <table className="dt">
            <thead><tr><th className="r">Block</th><th>Event</th><th>From</th><th>To</th><th>Value (raw)</th><th>Tx</th></tr></thead>
            <tbody>
              {out.output.recent.map((e: any, i: number) => (
                <tr key={i}>
                  <td className="mono r">{e.block}</td>
                  <td><span className="tag">{e.event}</span></td>
                  <td><Addr value={e.from} n={4} /></td>
                  <td><Addr value={e.to} n={4} /></td>
                  <td className="mono">{e.value_raw ?? "—"}</td>
                  <td><Addr value={e.tx_hash} n={6} /></td>
                </tr>
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
          {Object.entries(out.output.tokens || {}).map(([s, v]) => (
            <span key={s} style={{ display: "contents" }}><dt>{s}</dt><dd className="mono">{String(v)}</dd></span>
          ))}
        </dl>
      )}
      {out && (
        <details>
          <summary className="muted">Execution log</summary>
          <pre className="report">{out.logs.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}
