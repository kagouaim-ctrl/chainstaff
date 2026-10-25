import { useQuery } from "@tanstack/react-query";
import { Activity, Blocks, Plus, RefreshCw, Trash2, TriangleAlert } from "lucide-react";
import { memo, useState } from "react";
import { api, del, post } from "../lib/api";
import { useChainFeed, type ChainMsg } from "../lib/chainstream";
import { useAction, useWorkspaces } from "../lib/queries";
import { fmtTime, go, num, short } from "../lib/util";
import { Addr, Cell, EmptyState, ErrorState, Loading } from "./ui";

interface Monitor {
  id: string;
  name: string;
  kind: "address" | "contract" | "token_transfer";
  target: string;
  topics: string[];
  min_value: string;
  enabled: boolean;
  trigger: { workspace_id?: string; goal?: string; on?: string };
}

const KIND: Record<Monitor["kind"], string> = { address: "地址", contract: "合约事件", token_transfer: "代币转账" };
const TRANSPORT: Record<string, [string, string]> = {
  websocket: ["WebSocket", "ok"],
  "http-poll": ["HTTP 轮询", "warn"],
  stopped: ["已停止", "bad"],
};

const useChainStatus = () => useQuery({ queryKey: ["chain-live"], queryFn: () => api<any>("/api/chain/status"), refetchInterval: 5000 });
const useMonitors = () => useQuery({ queryKey: ["chain-monitors"], queryFn: () => api<{ monitors: Monitor[] }>("/api/chain/monitors"), retry: false });

/** BSC realtime layer: listener health, monitors, and the live block / event feed. Everything here only reads the chain. */
export function ChainLive() {
  const st = useChainStatus();
  const feed = useChainFeed();
  if (st.isLoading) return <Loading rows={6} />;
  if (st.error) return <ErrorState error={st.error} retry={() => st.refetch()} />;
  const s = st.data;
  if (!s?.enabled)
    return (
      <EmptyState title="实时链上监听没有运行" icon={<Activity size={18} />}>
        <span>{s?.reason || "需要配置链（CHAINSTAFF_CHAIN）。"} 配置后，新区块、监控事件和重组会实时推送到这里。</span>
      </EmptyState>
    );
  return (
    <div className="scroll">
      <ListenerStrip s={s} feedState={feed.state} />
      <div className="chain-live">
        <section className="msec">
          <Monitors />
        </section>
        <section className="msec">
          <div className="msec-h"><h3>Live feed</h3><span className="muted xs">新区块、匹配事件、确认与重组</span></div>
          <Feed items={feed.recent} />
        </section>
      </div>
    </div>
  );
}

function ListenerStrip({ s, feedState }: { s: any; feedState: string }) {
  const l = s.listener || {};
  const p = s.processor || {};
  const [tl, tone] = TRANSPORT[l.transport] || [l.transport, ""];
  const health = useAction(() => post<{ endpoints: any[] }>("/api/chain/health-check", {}), ["chain-live"], "节点检查完成");
  return (
    <>
      <div className="strip chain-strip">
        <Cell label="Listener" value={<span className={tone}>{tl}{l.transport !== "stopped" && !l.connected ? " · 重连中" : ""}</span>} sub={l.endpoint || "—"} title={l.last_error || undefined} />
        <Cell label="Head" value={p.cursor != null ? num(p.cursor) : "—"} sub={l.head_age_s != null ? `${l.head_age_s}s 前` : s.network} />
        <Cell label="Confirmations" value={p.confirmations ?? "—"} sub={`待确认 ${s.events?.pending || 0} · 已确认 ${s.events?.confirmed || 0}`} />
        <Cell label="Reorgs" value={p.reorgs ?? 0} tone={p.reorgs ? "warn" : ""} sub={p.reorged_events ? `${p.reorged_events} 个事件被移除` : "—"} />
        <Cell label="Block processing" value={p.samples ? `${p.block_ms_p95}ms` : "—"} sub="P95，不含节点往返" />
        <Cell label="Browser feed" value={<span className={feedState === "live" ? "ok" : "warn"}>{feedState}</span>} sub={`${s.hub?.subscribers ?? 0} 个连接`} />
      </div>
      {l.last_error && <div className="note bad xs" style={{ margin: "0 20px 8px" }}>{l.last_error}</div>}
      <div className="msec">
        <div className="msec-h">
          <h3>RPC endpoints</h3>
          <span className="muted xs">只读 · 自动切换</span>
          <div className="grow" />
          <button className="btn ghost xs" disabled={health.isPending} onClick={() => health.mutate(undefined)}><RefreshCw size={11} /> 检查节点</button>
        </div>
        <div className="tbl-x"><table className="dt inner">
          <thead><tr><th>Endpoint</th><th>状态</th><th className="r">延迟</th><th className="r">P95</th><th className="r">调用 / 错误</th><th>最近错误</th></tr></thead>
          <tbody>
            {(s.rpc?.endpoints || []).map((e: any) => (
              <tr key={e.url}>
                <td className="mono xs">{e.url}</td>
                <td><span className={`st ${e.healthy ? "ok" : "bad"}`}><i /><span className="t">{e.healthy ? "正常" : `冷却 ${e.cooldown_s}s`}</span></span></td>
                <td className="r mono">{e.latency_ms != null ? `${e.latency_ms}ms` : "—"}</td>
                <td className="r mono">{e.p95_ms != null ? `${e.p95_ms}ms` : "—"}</td>
                <td className="r mono">{e.calls} / {e.errors}</td>
                <td className="xs muted ellipsis" style={{ maxWidth: 240 }} title={e.last_error || ""}>{e.last_error || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
        {health.data ? (
          <div className="col gap2 xs" style={{ marginTop: 6 }}>
            {(health.data as { endpoints: any[] }).endpoints.map((r: any) => (
              <span key={r.url} className={r.ok ? "ok" : "bad"}>{r.url}：{r.ok ? `chain ${r.chain_id} · 区块 ${num(r.block)} · ${r.latency_ms}ms` : r.error}</span>
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}

function Monitors() {
  const q = useMonitors();
  const [adding, setAdding] = useState(false);
  const toggle = useAction((m: Monitor) => api(`/api/chain/monitors/${m.id}`, { method: "PATCH", json: { enabled: !m.enabled } }), ["chain-monitors"]);
  const remove = useAction((m: Monitor) => del(`/api/chain/monitors/${m.id}`), ["chain-monitors"], "监控已删除");
  return (
    <>
      <div className="msec-h">
        <h3>Monitors</h3>
        <span className="mono muted xs">{q.data?.monitors.length ?? 0}</span>
        <div className="grow" />
        <button className="btn ghost xs" onClick={() => setAdding(!adding)}><Plus size={11} /> 添加监控</button>
      </div>
      {adding && <MonitorForm onDone={() => setAdding(false)} />}
      {q.isLoading ? <Loading rows={3} /> : !q.data?.monitors.length ? (
        <div className="muted sm">还没有监控。添加一个地址、代币或合约，新事件会实时出现在右侧，也可以自动交给工作区分析。</div>
      ) : (
        <div className="tbl-x"><table className="dt inner">
          <tbody>
            {q.data.monitors.map((m) => (
              <tr key={m.id}>
                <td><b className="sm">{m.name}</b><div className="xs muted">{KIND[m.kind]}{m.min_value !== "0" ? ` · ≥ ${m.min_value}` : ""}{m.topics.length ? ` · ${m.topics.length} topic` : ""}</div></td>
                <td><Addr value={m.target} n={5} /></td>
                <td className="xs">{m.trigger?.workspace_id ? <span className="tag acc">自动任务 · {m.trigger.on || "confirmed"}</span> : <span className="muted">只记录</span>}</td>
                <td className="r nowrap">
                  <button className="btn ghost xs" onClick={() => toggle.mutate(m)}>{m.enabled ? "暂停" : "启用"}</button>
                  <button className="btn ghost xs icon" aria-label="删除监控" onClick={() => remove.mutate(m)}><Trash2 size={11} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </>
  );
}

function MonitorForm({ onDone }: { onDone: () => void }) {
  const ws = useWorkspaces();
  const [f, setF] = useState({ name: "", kind: "address" as Monitor["kind"], target: "", min_value: "", topic: "", workspace_id: "", on: "confirmed" });
  const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }));
  const save = useAction(
    () => post("/api/chain/monitors", {
      name: f.name, kind: f.kind, target: f.target.trim(), min_value: f.min_value || "0", topics: f.kind === "contract" && f.topic.trim() ? [f.topic.trim()] : [],
      trigger: f.workspace_id ? { workspace_id: f.workspace_id, on: f.on } : null,
    }),
    ["chain-monitors", "chain-live"],
    "监控已添加",
  );
  return (
    <div className="note col gap6" style={{ marginBottom: 10 }}>
      <div className="grid3">
        <label className="field"><span>类型</span>
          <select className="input" value={f.kind} onChange={(e) => set("kind", e.target.value)}>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field" style={{ gridColumn: "span 2" }}><span>{f.kind === "address" ? "钱包地址" : f.kind === "token_transfer" ? "代币合约" : "合约地址"}</span>
          <input className="input mono" placeholder="0x…" value={f.target} onChange={(e) => set("target", e.target.value)} />
        </label>
      </div>
      <div className="grid3">
        <label className="field"><span>名称</span><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
        <label className="field"><span>最小数量（原始单位）</span><input className="input mono" inputMode="numeric" placeholder="0" value={f.min_value} onChange={(e) => set("min_value", e.target.value.replace(/\D/g, ""))} /></label>
        {f.kind === "contract" && <label className="field"><span>事件 topic0（可选）</span><input className="input mono" placeholder="0x…" value={f.topic} onChange={(e) => set("topic", e.target.value)} /></label>}
      </div>
      <div className="grid3">
        <label className="field" style={{ gridColumn: "span 2" }}><span>新事件自动交给工作区分析（可选）</span>
          <select className="input" value={f.workspace_id} onChange={(e) => set("workspace_id", e.target.value)}>
            <option value="">不自动，只记录</option>
            {(ws.data || []).filter((w) => !w.archived).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        {f.workspace_id && (
          <label className="field"><span>触发时机</span>
            <select className="input" value={f.on} onChange={(e) => set("on", e.target.value)}>
              <option value="confirmed">确认后（推荐）</option>
              <option value="pending">一出现就触发（可能被重组撤销）</option>
            </select>
          </label>
        )}
      </div>
      <div className="row gap6">
        <div className="grow xs muted">只读监控：不会签名或发送任何交易。</div>
        <button className="btn sm" onClick={onDone}>取消</button>
        <button className="btn sm primary" disabled={!f.target.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onDone })}>添加</button>
      </div>
    </div>
  );
}

const Feed = memo(function Feed({ items }: { items: ChainMsg[] }) {
  if (!items.length) return <div className="muted sm">等待新区块…</div>;
  return (
    <div className="chain-feed">
      {items.slice(0, 120).map((m) => <FeedItem key={m.seq} m={m} />)}
    </div>
  );
});

function FeedItem({ m }: { m: ChainMsg }) {
  const t = <span className="when mono">{fmtTime(new Date(m.ts_ms).toISOString(), false)}</span>;
  switch (m.type) {
    case "block":
      return <div className="feed-line muted">{t}<Blocks size={12} /><span className="mono">#{num(m.number)}</span><span className="xs">{m.tx_count} tx</span>{m.events > 0 && <span className="tag acc">{m.events} 事件</span>}</div>;
    case "event": {
      const e = m.event;
      const d = e.data || {};
      return (
        <div className="feed-line info">
          {t}<Activity size={12} /><b className="sm">{m.monitor?.name}</b>
          <span className="xs">{e.kind === "native_transfer" ? `BNB ${d.value_bnb}` : e.kind === "token_transfer" ? `代币 ${short(d.token, 4)} ${d.value_raw}` : d.event}</span>
          {d.from && <span className="mono xs muted">{short(d.from, 4)} → {short(d.to, 4)}</span>}
          <span className="mono xs muted">#{num(e.block)}</span><span className="tag">待确认</span>
        </div>
      );
    }
    case "event.confirmed":
      return <div className="feed-line ok">{t}<Activity size={12} /><span className="xs">事件 {m.event.id} 已确认</span><span className="mono xs muted">{short(m.event.tx_hash, 6)}</span></div>;
    case "event.reorged":
      return <div className="feed-line bad">{t}<TriangleAlert size={12} /><span className="xs">事件 {m.event.id} 被重组移除</span><span className="mono xs muted">{short(m.event.tx_hash, 6)}</span></div>;
    case "reorg":
      return <div className="feed-line bad">{t}<TriangleAlert size={12} /><span className="xs">链重组：回退到 #{num(m.common_ancestor)}，深度 {m.depth}，{m.events} 个事件移除</span></div>;
    case "gap":
      return <div className="feed-line acc">{t}<TriangleAlert size={12} /><span className="xs">{m.text}</span></div>;
    case "trigger":
      return (
        <div className="feed-line acc">
          {t}<Activity size={12} /><span className="xs">{m.replayed ? "事件已有任务" : "已自动创建分析任务"}</span>
          <button className="btn ghost xs" onClick={() => go("workspace", undefined, { m: m.task_id })}>打开</button>
        </div>
      );
    case "trigger.error":
      return <div className="feed-line bad">{t}<TriangleAlert size={12} /><span className="xs">自动任务失败：{m.error}</span></div>;
    case "status":
      return <div className="feed-line muted">{t}<RefreshCw size={12} /><span className="xs">{TRANSPORT[m.transport]?.[0] || m.transport}{m.endpoint ? ` · ${m.endpoint}` : ""}{m.last_error ? ` · ${m.last_error}` : ""}</span></div>;
    default:
      return null;
  }
}

