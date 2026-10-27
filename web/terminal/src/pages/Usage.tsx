import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { budgetUse } from "../components/chat/Notices";
import { EmptyState, ErrorState, Loading, Seg } from "../components/ui";
import { api } from "../lib/api";
import { useAtAgents, useAtMissions, useAtRuntime, useAtUsage, type CostRow } from "../lib/at";
import type { AiUsage } from "../lib/types";
import { ago, go, num, usd, type Route } from "../lib/util";

type Day = { day: string; provider: string; model: string; calls: number; failures: number; unpriced: number; input_tokens: number; output_tokens: number; cost_usd: string };

/**
 * Usage & Billing: what the model calls cost, from the server's model gateway (every call, priced by the gateway)
 * and from the autonomous runs (per agent and mission). KAGOU has no paid plan; this is model spend only, never
 * wallet balances or on-chain amounts.
 */
export default function Usage({ route }: { route: Route }) {
  const rt = useAtRuntime();
  const days = useQuery({ queryKey: ["at", "ai-usage", 31], queryFn: () => api<AiUsage & { days: Day[] }>("/api/wf/ai-usage?days=31") });
  const [range, setRange] = useState(route.params.get("range") || "30d");
  const u = useAtUsage(range);
  const agents = useAtAgents(true);
  const missions = useAtMissions({ status: "" });
  const g = rt.data?.gateway;
  const name = (id: string | null) => agents.data?.find((a) => a.id === id)?.name || missions.data?.find((m) => m.id === id)?.name || id || "—";
  return (
    <div className="page">
      <div className="toolbar">
        <div className="col gap4">
          <h1>Usage & Billing</h1>
          <span className="sub">模型调用的花费和预算。只统计 AI 模型费用，不包括钱包资产、链上交易金额或 Gas。</span>
        </div>
      </div>
      <div className="section col gap24" style={{ maxWidth: 1100 }}>
        {rt.error ? <ErrorState error={rt.error} retry={() => rt.refetch()} /> : (
          <div className="usage-top">
            <Spend label="今日消耗" loading={rt.isLoading} spent={g?.spent_today_usd} budget={g?.daily_budget_usd} env="AI_DAILY_BUDGET_USD" />
            <Spend label="本月消耗" loading={rt.isLoading} spent={g?.spent_month_usd} budget={g?.monthly_budget_usd} env="AI_MONTHLY_BUDGET_USD" />
            {days.data && days.data.unpriced_calls_this_month > 0 && (
              <div className="note sm">本月有 {days.data.unpriced_calls_this_month} 次调用的模型没有价格表，没有计入花费。</div>
            )}
          </div>
        )}

        <section className="col gap12">
          <div className="row gap8"><h2 className="grow">按模型</h2><span className="muted xs">最近 31 天 · 来自模型网关</span></div>
          {days.isLoading ? <Loading rows={3} /> : days.error ? <ErrorState error={days.error} /> : <ByModel rows={days.data!.days} />}
        </section>

        <section className="col gap12">
          <div className="row gap8"><h2 className="grow">每日消耗</h2><span className="muted xs">最近 31 天 · 来自模型网关</span></div>
          {days.isLoading ? <Loading rows={3} /> : days.data && <History rows={days.data.days} budget={g?.daily_budget_usd} />}
        </section>

        <section className="col gap12">
          <div className="row gap8 wrap">
            <h2 className="grow">按 Agent 和 Mission</h2>
            <span className="muted xs">自主运行的花费</span>
            <Seg value={range} onChange={setRange} options={[["24h", "24h"], ["7d", "7d"], ["30d", "30d"]]} />
          </div>
          {u.isLoading ? <Loading rows={4} /> : u.error ? <ErrorState error={u.error} /> : (
            <>
              <div className="usage-facts">
                <span><b>{num(u.data!.totals.runs)}</b> 次运行</span>
                <span><b>{num(u.data!.totals.model_calls)}</b> 次模型调用</span>
                <span><b>{num(u.data!.totals.tokens_in + u.data!.totals.tokens_out)}</b> tokens</span>
                <span><b>{usd(u.data!.totals.cost_usd)}</b></span>
                {u.data!.totals.failed_runs > 0 && <span className="bad"><b>{u.data!.totals.failed_runs}</b> 次运行失败</span>}
              </div>
              <div className="grid2 stack-m">
                <CostTable title="Agent" rows={u.data!.by_agent} name={name} onRow={(id) => id && go("agents", id)} />
                <CostTable title="Mission" rows={u.data!.by_mission} name={(id) => (id ? name(id) : "直接交给 Agent 的任务")} onRow={(id) => id && go("missions", id)} />
              </div>
              <details className="more-box">
                <summary>最近的模型调用（{u.data!.calls.length}）</summary>
                {u.data!.calls.length ? (
                  <div className="table-wrap boxed">
                    <table className="dt">
                      <thead><tr><th>When</th><th>Agent</th><th>Task</th><th>Model</th><th>Tokens</th><th>Latency</th><th>Cost</th><th>OK</th></tr></thead>
                      <tbody>{u.data!.calls.slice(0, 50).map((c) => (
                        <tr key={c.id}><td className="mono muted">{ago(c.at)}</td><td>{name(c.agent_id)}</td><td className="mono muted">{c.task_id}</td><td className="mono">{c.provider}/{c.name}</td>
                          <td className="mono">{c.tokens_in}+{c.tokens_out}</td><td className="mono">{c.secs ?? "—"}s</td><td className="mono">${Number(c.cost_usd).toFixed(5)}</td><td className={c.ok ? "ok" : "bad"}>{c.ok ? "ok" : "failed"}</td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                ) : <span className="muted sm">这段时间没有模型调用。</span>}
              </details>
            </>
          )}
        </section>

        <p className="muted xs">预算上限有四层：全局（模型网关）、每个 Agent、每个 Mission、每个任务。先到达的那一层会停止运行并通知你。Agent 和任务的预算在 Agent 的设置里调整。</p>
      </div>
    </div>
  );
}

function Spend({ label, spent, budget, env, loading }: { label: string; spent?: string; budget?: string | null; env: string; loading: boolean }) {
  const use = budgetUse(spent, budget);
  return (
    <div className="spend">
      <span className="label">{label}</span>
      {loading ? <i className="skeleton" style={{ height: 30, width: 120 }} /> : <span className="spend-v">{usd(spent)}</span>}
      {use !== null ? (
        <>
          <div className={`ubar ${use >= 1 ? "bad" : use >= 0.8 ? "warn" : ""}`}><i style={{ width: `${Math.min(100, use * 100)}%` }} /></div>
          <span className="muted xs">预算 ${budget} · 已用 {Math.round(use * 100)}%</span>
        </>
      ) : <span className="muted xs">没有设置上限（{env}）</span>}
    </div>
  );
}

function ByModel({ rows }: { rows: Day[] }) {
  const m = new Map<string, { provider: string; model: string; calls: number; failures: number; tokens: number; cost: number }>();
  for (const r of rows) {
    const k = `${r.provider}/${r.model}`;
    const x = m.get(k) || { provider: r.provider, model: r.model, calls: 0, failures: 0, tokens: 0, cost: 0 };
    x.calls += r.calls;
    x.failures += r.failures;
    x.tokens += r.input_tokens + r.output_tokens;
    x.cost += Number(r.cost_usd || 0);
    m.set(k, x);
  }
  const list = [...m.values()].sort((a, b) => b.cost - a.cost);
  if (!list.length) return <EmptyState small title="还没有模型调用" />;
  return (
    <div className="table-wrap boxed">
      <table className="dt">
        <thead><tr><th>Model</th><th>Calls</th><th>Failures</th><th>Tokens</th><th>Cost</th></tr></thead>
        <tbody>{list.map((x) => (
          <tr key={x.provider + x.model}><td className="mono">{x.provider}/{x.model}</td><td className="mono">{num(x.calls)}</td><td className={`mono ${x.failures ? "bad" : "muted"}`}>{x.failures}</td><td className="mono">{num(x.tokens)}</td><td className="mono">{usd(x.cost)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function History({ rows, budget }: { rows: Day[]; budget?: string | null }) {
  const by = new Map<string, number>();
  for (const r of rows) by.set(r.day, (by.get(r.day) || 0) + Number(r.cost_usd || 0));
  const list = [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  if (!list.length) return <EmptyState small title="还没有消耗记录" />;
  const max = Math.max(...list.map(([, v]) => v), Number(budget || 0), 0.0001);
  return (
    <div className="hist" role="img" aria-label="每日模型花费">
      {list.map(([d, v]) => (
        <div key={d} className="hist-col" title={`${d} · ${usd(v)}`}>
          <i style={{ height: `${Math.max(2, (v / max) * 100)}%` }} />
          <span>{d.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

function CostTable({ title, rows, name, onRow }: { title: string; rows: CostRow[]; name: (id: string | null) => string; onRow: (id: string | null) => void }) {
  if (!rows.length) return <div className="col gap6"><span className="label">{title}</span><span className="muted sm">这段时间没有花费。</span></div>;
  return (
    <div className="col gap6">
      <span className="label">{title}</span>
      <div className="table-wrap boxed">
        <table className="dt"><thead><tr><th>{title}</th><th>Runs</th><th>Tokens</th><th>Cost</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id || "-"} className={r.id ? "click" : ""} onClick={() => onRow(r.id)}><td>{name(r.id)}</td><td className="mono">{r.runs}</td><td className="mono">{num(r.tokens)}</td><td className="mono">{usd(r.cost_usd)}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}
