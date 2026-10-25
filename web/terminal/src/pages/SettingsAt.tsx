import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { Fragment, lazy, Suspense, useState } from "react";
import { EmptyState, ErrorState, Loading, Seg } from "../components/ui";
import { api, post } from "../lib/api";
import { refreshAt, useAtAgents, useAtMeta, useAtMissions, useAtNotifications, useAtRuntime, useAtUsage } from "../lib/at";
import { DEMO } from "../lib/demo";
import { useSystem } from "../lib/queries";
import { ago, fmtTime, setParams, type Route } from "../lib/util";
import { MemoryAdmin, Schedules, System } from "./SettingsLegacy";

const Skills = lazy(() => import("./Skills"));

const SECTIONS: [string, string][] = [
  ["models", "AI Models"],
  ["keys", "API Keys"],
  ["tools", "Tool Library"],
  ["permissions", "Tool Permissions"],
  ["notifications", "Notifications"],
  ["runtime", "Runtime"],
  ["billing", "Billing"],
  ["audit", "Audit Logs"],
  ["legacy", "Legacy"],
];

export default function Settings({ route }: { route: Route }) {
  const s = route.params.get("s") || "models";
  return (
    <div className="term two-pane">
      <aside className="pane rail" aria-label="Settings">
        <div className="rail-group">
          <span className="label">Settings</span>
          {SECTIONS.map(([id, l]) => <button key={id} className="rail-item" aria-current={s === id} onClick={() => setParams(route, { s: id === "models" ? null : id })}>{l}</button>)}
        </div>
      </aside>
      <section className="pane center page">
        <div className="toolbar"><h1>{SECTIONS.find(([id]) => id === s)?.[1] || "Settings"}</h1></div>
        {s === "models" && <Models />}
        {s === "keys" && <Keys />}
        {s === "tools" && <Suspense fallback={<Loading />}><Skills /></Suspense>}
        {s === "permissions" && <Permissions />}
        {s === "notifications" && <Notifications />}
        {s === "runtime" && <Runtime />}
        {s === "billing" && <Billing />}
        {s === "audit" && <Audit />}
        {s === "legacy" && <Legacy />}
      </section>
    </div>
  );
}

const Yes = ({ ok, on, off }: { ok: boolean; on: string; off: string }) => (
  <span className={`badge ${ok ? "ok" : "warn"}`}>{ok ? <Check size={11} /> : <X size={11} />}{ok ? on : off}</span>
);

function Models() {
  const rt = useAtRuntime();
  const [range, setRange] = useState("24h");
  const u = useAtUsage(range);
  const agents = useAtAgents(true);
  const missions = useAtMissions({ status: "" });
  if (rt.isLoading) return <Loading />;
  if (rt.error) return <ErrorState error={rt.error} />;
  const r = rt.data!;
  const name = (id: string | null) => agents.data?.find((a) => a.id === id)?.name || missions.data?.find((m) => m.id === id)?.name || id || "—";
  return (
    <div className="section col gap16" style={{ maxWidth: 1100 }}>
      <div className="col gap8">
        <div className="label">Model gateway</div>
        <dl className="kv">
          <dt>Providers</dt>
          <dd>{r.models.providers.length ? r.models.providers.map((p) => <span key={p.provider} className="badge ok" style={{ marginRight: 6 }}><Check size={11} />{p.provider} · {p.label} · {p.model}</span>)
            : <span className="badge warn"><X size={11} />没有配置任何模型：设置 ANTHROPIC_API_KEY（Claude），或 AI_PROVIDER + AI_API_KEY + AI_MODEL（Qwen、Gemini、本地模型）</span>}</dd>
          <dt>Default model</dt><dd className="mono">{r.models.default_model}</dd>
          <dt>Spend</dt><dd className="mono">today ${Number(r.gateway.spent_today_usd).toFixed(4)}{r.gateway.daily_budget_usd ? ` / $${r.gateway.daily_budget_usd}` : ""} · month ${Number(r.gateway.spent_month_usd).toFixed(4)}{r.gateway.monthly_budget_usd ? ` / $${r.gateway.monthly_budget_usd}` : ""}</dd>
          <dt>Limits</dt><dd className="mono">{r.gateway.max_concurrent} concurrent calls · {String(r.limits.max_model_calls)} model calls and {String(r.limits.max_tool_calls)} tool calls per run · task budget ${String(r.limits.default_task_budget)}</dd>
        </dl>
        <small className="muted">模型由服务器环境变量配置，不在浏览器里保存密钥。每个 Agent 在自己的设置里选择 Provider 和模型；没有配置的 Provider 会让该 Agent 执行失败并通知你，不会假装成功。</small>
      </div>
      <div className="row gap6"><div className="label grow">Usage (autonomous runs)</div><Seg value={range} onChange={setRange} options={[["1h", "1h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"]]} /></div>
      {u.isLoading ? <Loading /> : u.error ? <ErrorState error={u.error} /> : (
        <>
          <div className="metrics">
            <div><span className="label">Runs</span><span className="v">{u.data!.totals.runs}</span></div>
            <div><span className="label">Model calls</span><span className="v">{u.data!.totals.model_calls}</span></div>
            <div><span className="label">Tool calls</span><span className="v">{u.data!.totals.tool_calls}</span></div>
            <div><span className="label">Tokens</span><span className="v">{u.data!.totals.tokens_in + u.data!.totals.tokens_out}</span></div>
            <div><span className="label">Cost</span><span className="v">${Number(u.data!.totals.cost_usd).toFixed(4)}</span></div>
            <div><span className="label">Avg latency</span><span className="v">{u.data!.totals.model_latency_avg_s ?? "—"}s</span></div>
            <div><span className="label">Model failures</span><span className="v">{u.data!.totals.model_failures}</span></div>
            <div><span className="label">Failed runs</span><span className="v">{u.data!.totals.failed_runs}</span></div>
          </div>
          <div className="grid2 stack-m">
            <CostTable title="By agent" rows={u.data!.by_agent} name={name} />
            <CostTable title="By mission" rows={u.data!.by_mission} name={name} />
          </div>
          <div className="label">Recent model calls</div>
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
        </>
      )}
    </div>
  );
}

function CostTable({ title, rows, name }: { title: string; rows: { id: string | null; runs: number; cost_usd: number; tokens: number }[]; name: (id: string | null) => string }) {
  return (
    <div className="col gap6">
      <div className="label">{title}</div>
      {rows.length ? (
        <div className="table-wrap boxed">
          <table className="dt"><thead><tr><th>{title.replace("By ", "")}</th><th>Runs</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.id || "-"}><td>{name(r.id)}</td><td className="mono">{r.runs}</td><td className="mono">{r.tokens}</td><td className="mono">${r.cost_usd.toFixed(4)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : <span className="muted sm">—</span>}
    </div>
  );
}

function Keys() {
  const rt = useAtRuntime();
  const sys = useSystem();
  const r = rt.data;
  return (
    <div className="section col gap12" style={{ maxWidth: 900 }}>
      <div className="note">所有密钥都是服务器的环境变量（Railway / Docker），浏览器里不显示、也不能输入密钥。这里只显示每一项有没有配置，以及缺了它什么功能不能用。</div>
      {r && sys.data && (
        <dl className="kv">
          <dt>Claude</dt><dd><Yes ok={r.models.providers.some((p) => p.provider === "anthropic")} on="ANTHROPIC_API_KEY set" off="ANTHROPIC_API_KEY not set: Claude agents cannot run" /></dd>
          <dt>OpenAI-compatible</dt><dd><Yes ok={r.models.providers.some((p) => p.provider === "openai_compat")} on="AI_PROVIDER / AI_API_KEY set" off="not set: Qwen / Gemini / local models unavailable" /></dd>
          <dt>GitHub</dt><dd><Yes ok={r.github_token} on="KAGOU_GITHUB_TOKEN set: fix agents can open Draft PRs" off="KAGOU_GITHUB_TOKEN not set: patches stay in KAGOU for review, no Draft PR" /></dd>
          <dt>BSC node</dt><dd><Yes ok={sys.data.chain.configured} on={`CHAINSTAFF_CHAIN: ${sys.data.chain.network} (${sys.data.chain.chain_id})${sys.data.chain.listener ? " · listener running" : " · listener off"}`} off="CHAINSTAFF_CHAIN not set: BSC watchers and chain skills unavailable" /></dd>
          <dt>Explorer API</dt><dd><Yes ok={sys.data.explorer_api} on="ETHERSCAN_API_KEY set" off="ETHERSCAN_API_KEY not set: transaction history skill unavailable" /></dd>
          <dt>Watcher secrets</dt><dd className="muted">每个 Watcher 有自己的令牌或 Webhook secret，在 Workspace → Watchers 里生成和更换，只显示一次。</dd>
          <dt>Private keys</dt><dd className="muted">Agent 不持有私钥，也不会把私钥放进模型上下文；KAGOU 不替 Agent 签名或转账。</dd>
        </dl>
      )}
      <System />
    </div>
  );
}

function Permissions() {
  const meta = useAtMeta();
  if (meta.isLoading) return <Loading />;
  if (meta.error) return <ErrorState error={meta.error} />;
  const m = meta.data!;
  return (
    <div className="section col gap16" style={{ maxWidth: 1000 }}>
      <div className="col gap6">
        <div className="label">Permissions an agent can be given</div>
        <div className="table-wrap boxed">
          <table className="dt"><thead><tr><th>Permission</th><th>Risk</th><th>What it allows</th><th>Tools</th></tr></thead>
            <tbody>{Object.entries(m.permissions).map(([p, [d, r]]) => (
              <tr key={p}><td className="mono">{p}</td><td className={r === "high" ? "bad" : r === "medium" ? "acc" : "muted"}>{r}</td><td>{d}</td><td className="mono muted ellipsis" style={{ maxWidth: 320 }}>{m.tools.filter((t) => t.permission === p).map((t) => t.name).join(", ") || "—"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      <div className="col gap6">
        <div className="label">Never given to an agent</div>
        <div className="table-wrap boxed">
          <table className="dt"><thead><tr><th>Action</th><th>Who</th></tr></thead>
            <tbody>{Object.entries(m.human_only).map(([p, d]) => <tr key={p}><td>{d} <span className="mono muted xs">{p}</span></td><td className="acc">Human only</td></tr>)}</tbody>
          </table>
        </div>
      </div>
      <div className="col gap6">
        <div className="label">Loop guards (server-wide)</div>
        <dl className="kv">
          {Object.entries(m.limits).map(([k, v]) => <Fragment key={k}><dt className="mono">{k}</dt><dd className="mono">{String(v)}</dd></Fragment>)}
        </dl>
        <small className="muted">每次跨 Agent 交接都在服务器上检查：接收方的权限和工作区、交接深度、每小时交接次数、去重、循环交接、任务和 Mission 预算、同一问题每天的修复次数。</small>
      </div>
      <div className="col gap6">
        <div className="label">Isolated environment for code work</div>
        <dl className="kv">
          <dt>Kind</dt><dd>{m.sandbox.kind}: a fresh clone per task, git hooks disabled</dd>
          <dt>Network</dt><dd><Yes ok={m.sandbox.network_isolated} on="tests run without network (unshare -rn)" off="network isolation unavailable on this host" /></dd>
          <dt>Environment</dt><dd>scrubbed: no server secrets in the test process</dd>
          <dt>Limits</dt><dd className="mono">{Object.entries(m.sandbox.limits).map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
        </dl>
      </div>
    </div>
  );
}

function Notifications() {
  const n = useAtNotifications();
  const agents = useAtAgents();
  return (
    <div className="section col gap16" style={{ maxWidth: 1000 }}>
      <div className="note">通知在控制台里（右上角铃铛）。每个 Agent 在 Settings 里设置从哪个级别开始通知、失败和完成时是否通知；需要人工审批、无人负责的高优先级事件和任务失败总会通知。外部渠道（Telegram、邮件、Webhook 推送）还没有实现。</div>
      <div className="col gap6">
        <div className="label">Per agent</div>
        <div className="table-wrap boxed">
          <table className="dt"><thead><tr><th>Agent</th><th>Status</th><th /></tr></thead>
            <tbody>{(agents.data || []).map((a) => <tr key={a.id}><td>{a.name}</td><td className="muted">{a.state}</td><td className="r"><a className="btn xs" href={`#/agents/${a.id}?tab=settings`}>Edit rules</a></td></tr>)}</tbody>
          </table>
        </div>
      </div>
      <div className="row gap6"><div className="label grow">Recent notifications</div>
        <button className="btn xs" disabled={DEMO || !n.data?.unread} onClick={() => post("/api/at/notifications/read", {}).then(refreshAt)}>Mark all read</button></div>
      {n.isLoading ? <Loading /> : !n.data?.items.length ? <EmptyState small title="没有通知" /> : (
        <div className="col">{n.data.items.map((x) => (
          <div key={x.id} className="list-row"><span className={`badge ${x.severity === "high" || x.severity === "critical" ? "bad" : x.severity === "medium" ? "warn" : ""}`}>{x.severity}</span>
            <span className={`grow ${x.read_at ? "muted" : ""}`}><b>{x.title}</b>{x.body ? <span className="muted"> · {x.body}</span> : null}</span><span className="mono muted xs">{fmtTime(x.at)}</span></div>
        ))}</div>
      )}
    </div>
  );
}

function Runtime() {
  const rt = useAtRuntime();
  if (rt.isLoading) return <Loading />;
  if (rt.error) return <ErrorState error={rt.error} />;
  const r = rt.data!;
  return (
    <div className="section col gap16" style={{ maxWidth: 1100 }}>
      <dl className="kv">
        <dt>Health</dt><dd><span className={`badge ${r.level === "ok" ? "ok" : r.level === "degraded" ? "warn" : "bad"}`}>{r.level}</span> {r.problems.join("；")}</dd>
        <dt>Worker</dt><dd>{r.worker_alive ? "online" : "offline: no worker has sent a heartbeat recently. Start it with `python -m agent.autonomy.worker` (the Docker image runs it under the supervisor)."}</dd>
        <dt>Chain listener</dt><dd>{r.chain ? <span className="mono">{r.chain.network} · cursor {String(r.chain.processor?.cursor ?? "—")} · {r.chain.listener?.mode || r.chain.listener?.state || ""} · reorgs {String(r.chain.processor?.reorgs ?? 0)}</span> : <span className="muted">not running in this server</span>}</dd>
      </dl>
      <div className="col gap6">
        <div className="label">Workers</div>
        {r.workers.length ? (
          <div className="table-wrap boxed">
            <table className="dt"><thead><tr><th>Worker</th><th>Host</th><th>Status</th><th>Heartbeat</th><th>Running</th><th>Concurrency</th><th>Started</th><th>Stats</th></tr></thead>
              <tbody>{r.workers.map((w) => (
                <tr key={w.id}><td className="mono">{w.id}</td><td className="mono muted">{w.host} · pid {w.pid}</td><td>{w.status}</td><td className="mono">{ago(w.heartbeat_at)}</td>
                  <td className="mono">{w.running.length}</td><td className="mono">{w.concurrency}</td><td className="mono muted">{fmtTime(w.started_at)}</td>
                  <td className="mono muted xs">{Object.entries(w.stats || {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</td></tr>
              ))}</tbody>
            </table>
          </div>
        ) : <span className="muted sm">还没有 Worker 运行过。</span>}
      </div>
      <div className="col gap6">
        <div className="label">How work survives restarts</div>
        <div className="note">任务、事件、交接和检查点都在数据库里。Worker 每 {String(r.limits.heartbeat_s)} 秒心跳并续租；一个 Worker 停止续租 {String(r.limits.lease_s)} 秒后，它的任务回到队列，从最后一个检查点继续（最多 {String(r.limits.max_attempts)} 次）。没有任务时 Worker 不调用模型。</div>
      </div>
    </div>
  );
}

function Billing() {
  const rt = useAtRuntime();
  const u = useAtUsage("30d");
  return (
    <div className="section col gap12" style={{ maxWidth: 900 }}>
      <div className="note">KAGOU 现在没有付费套餐或账单系统。这里显示的是模型花费（按 Agent 运行记录和模型网关计算）和你设置的预算上限。</div>
      {rt.data && (
        <dl className="kv">
          <dt>Today</dt><dd className="mono">${Number(rt.data.gateway.spent_today_usd).toFixed(4)}{rt.data.gateway.daily_budget_usd ? ` of $${rt.data.gateway.daily_budget_usd} (AI_DAILY_BUDGET_USD)` : " · no daily cap (AI_DAILY_BUDGET_USD)"}</dd>
          <dt>This month</dt><dd className="mono">${Number(rt.data.gateway.spent_month_usd).toFixed(4)}{rt.data.gateway.monthly_budget_usd ? ` of $${rt.data.gateway.monthly_budget_usd} (AI_MONTHLY_BUDGET_USD)` : " · no monthly cap (AI_MONTHLY_BUDGET_USD)"}</dd>
          <dt>Autonomous runs, 30d</dt><dd className="mono">{u.data ? `$${Number(u.data.totals.cost_usd).toFixed(4)} · ${u.data.totals.runs} runs · ${u.data.totals.model_calls} model calls` : "…"}</dd>
          <dt>Caps</dt><dd>Global (gateway), per agent (agent budget), per mission (mission budget), per task (task budget). The first one reached stops the run and notifies you.</dd>
        </dl>
      )}
    </div>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["at", "audit"], queryFn: () => api<{ id: number; at: string; kind: string; actor: string; text: string; employee: string }[]>("/api/events?kind=audit&limit=300") });
  if (q.isLoading) return <div className="section"><Loading /></div>;
  if (q.error) return <div className="section"><ErrorState error={q.error} /></div>;
  if (!q.data!.length) return <div className="section"><EmptyState small title="没有审计记录" /></div>;
  return (
    <div className="table-wrap">
      <table className="dt"><thead><tr><th>When</th><th>Area</th><th>Who</th><th>What</th></tr></thead>
        <tbody>{q.data!.map((e) => <tr key={e.id}><td className="mono muted">{fmtTime(e.at)}</td><td className="muted">{e.employee || "—"}</td><td className="mono">{e.actor}</td><td className="ellipsis" style={{ maxWidth: 640 }} title={e.text}>{e.text}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function Legacy() {
  const links: [string, string, string][] = [
    ["#/squads", "Squads", "旧的团队 / 主管模式（数据保留，新流程不需要 AI 主管）"],
    ["#/tasks", "Goal pipeline", "旧的目标 → 主管拆分 → 子任务流程和审批"],
    ["#/board", "Mission board", "旧流程的 Mission 工作台"],
    ["#/terminal", "Agent terminal", "旧的员工终端：直接运行技能"],
    ["#/team", "Employee roster", "旧的员工列表和编辑"],
    ["#/onchain", "BSC live data", "实时区块、链上事件、执行账本、Agent 注册"],
    ["#/log", "Legacy activity", "旧流程的消息和时间线"],
  ];
  return (
    <div className="section col gap16" style={{ maxWidth: 900 }}>
      <div className="note">这些页面属于之前的工作流，仍然连着真实后端，数据都保留。新的自主 Agent 不依赖它们。</div>
      <div className="m-list">{links.map(([h, l, d]) => <a key={h} className="m-row" href={h}><span className="col gap2 grow"><span className="strong">{l}</span><span className="muted xs">{d}</span></span></a>)}</div>
      <div className="label">Scheduled goals (legacy)</div>
      <Schedules />
      <div className="label">Memory (all scopes)</div>
      <MemoryAdmin />
    </div>
  );
}
