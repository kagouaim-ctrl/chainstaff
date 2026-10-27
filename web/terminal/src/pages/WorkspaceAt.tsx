import { Boxes, Copy, Pause, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentState, Sev } from "../components/AtBits";
import { Dialog, EmptyState, ErrorState, Loading, Tabs } from "../components/ui";
import { API_BASE, del, post, put } from "../lib/api";
import {
  MISSION_TYPE_LABEL, WATCHER_LABEL, refreshAt, useAtEvents, useAtMeta, useAtWorkspace, useAtWorkspaces,
  type AtWatcher, type AtWorkspace, type Resources,
} from "../lib/at";
import { DEMO } from "../lib/demo";
import { ago, copy, errText, fmtTime, go, setParams, toast, type Route } from "../lib/util";

type Tab = "resources" | "watchers" | "events" | "agents";

export default function Workspace({ route }: { route: Route }) {
  const list = useAtWorkspaces();
  const [creating, setCreating] = useState(!!route.params.get("new"));
  const id = route.id || list.data?.[0]?.id;
  if (list.isLoading) return <div className="page"><Loading rows={6} /></div>;
  if (list.error) return <div className="page"><ErrorState error={list.error} /></div>;
  return (
    <div className="term two-pane">
      <aside className="pane rail" aria-label="Workspaces">
        <div className="rail-group">
          <span className="label">Workspaces</span>
          {list.data!.map((w) => (
            <button key={w.id} className="rail-item" aria-current={w.id === id} onClick={() => go("workspace", w.id)}>
              <Boxes size={13} /> <span className="ellipsis">{w.name}</span> <span className="n">{w.open_tasks || 0}</span>
            </button>
          ))}
          <button className="rail-item" disabled={DEMO} onClick={() => setCreating(true)}><Plus size={13} /> New workspace</button>
        </div>
        <div className="rail-group">
          <span className="label">Chain data</span>
          <a className="rail-item" href="#/onchain">BSC live data</a>
        </div>
      </aside>
      <section className="pane center page">
        {id ? <WorkspaceDetail id={id} route={route} /> : (
          <EmptyState title="Create a workspace" icon={<Boxes size={18} />}>
            <span className="muted sm">工作区是 Agent 负责的项目：代码仓库、BSC 合约、代币、钱包、文档、API 和服务，以及监控它们的 Watcher。</span>
            {!DEMO && <button className="btn primary sm" onClick={() => setCreating(true)}><Plus size={12} /> New workspace</button>}
          </EmptyState>
        )}
      </section>
      {creating && <NewWorkspace onClose={() => (setCreating(false), route.params.get("new") && setParams(route, { new: null }))} />}
    </div>
  );
}

function NewWorkspace({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const w = await post<AtWorkspace>("/api/at/workspaces", { name, description: desc, resources: {} });
      toast("工作区已创建");
      refreshAt();
      onClose();
      go("workspace", w.id);
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="New workspace" onClose={onClose} footer={<><div className="grow" /><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!name.trim() || busy} onClick={save}>Create</button></>}>
      <label className="field"><span>Name</span><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 KGA Token、Pricing service" /></label>
      <label className="field"><span>Description</span><textarea className="input" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
      <small className="muted">创建后添加仓库、合约、代币、钱包和 API，再连接 Watcher。</small>
    </Dialog>
  );
}

function WorkspaceDetail({ id, route }: { id: string; route: Route }) {
  const q = useAtWorkspace(id);
  const tab = (route.params.get("tab") as Tab) || "resources";
  if (q.isLoading) return <Loading rows={8} />;
  if (q.error) return <ErrorState error={q.error} />;
  const w = q.data!;
  const watchers = Array.isArray(w.watchers) ? w.watchers : [];
  const agents = Array.isArray(w.agents) ? w.agents : [];
  return (
    <>
      <div className="toolbar"><h1>{w.name}</h1><span className="muted sm ellipsis">{w.description}</span></div>
      <div className="tabs-pad">
        <Tabs<Tab> value={tab} onChange={(v) => setParams(route, { tab: v === "resources" ? null : v }, id)}
          options={[["resources", "Resources"], ["watchers", "Watchers", watchers.length], ["events", "Events"], ["agents", "Agents & missions", agents.length]]} />
      </div>
      {tab === "resources" && <ResourcesEditor w={w} />}
      {tab === "watchers" && <WatcherList w={w} watchers={watchers} />}
      {tab === "events" && <EventList id={id} />}
      {tab === "agents" && (
        <div className="section col gap16">
          <div className="col gap8">
            <div className="label">Agents with access</div>
            {agents.length ? agents.map((a) => (
              <div key={a.id} className="list-row click" onClick={() => go("agents", a.id)}>
                <b>{a.name}</b><AgentState state={a.state} detail={a.state_detail} /><span className="muted ellipsis grow">{a.responsibility}</span>
              </div>
            )) : <span className="muted sm">还没有 Agent 绑定这个工作区。在 Agent 的设置里选择它。</span>}
          </div>
          <div className="col gap8">
            <div className="label">Missions</div>
            {w.missions?.length ? w.missions.map((m) => (
              <div key={m.id} className="list-row click" onClick={() => go("missions", m.id)}>
                <b>{m.name}</b><span className="tag">{MISSION_TYPE_LABEL[m.type]}</span><span className="muted">{m.status}</span><span className="muted mono xs grow" style={{ textAlign: "right" }}>{m.run_count} runs</span>
              </div>
            )) : <span className="muted sm">没有 Mission</span>}
          </div>
        </div>
      )}
    </>
  );
}

const KINDS: { k: keyof Resources; label: string; fields: [string, string, string?][] }[] = [
  { k: "repos", label: "Repositories", fields: [["url", "GitHub URL (or local path)", "https://github.com/owner/repo"], ["branch", "Branch", "main"], ["test_command", "Test command", "python -m pytest -q"], ["name", "Name"]] },
  { k: "contracts", label: "BSC contracts", fields: [["address", "Address", "0x…"], ["label", "Label"]] },
  { k: "tokens", label: "Tokens", fields: [["address", "Address", "0x…"], ["symbol", "Symbol"], ["label", "Label"]] },
  { k: "wallets", label: "Wallets", fields: [["address", "Address", "0x…"], ["label", "Label"]] },
  { k: "endpoints", label: "API endpoints", fields: [["name", "Name"], ["url", "URL", "https://api.example.com/health"]] },
  { k: "docs", label: "Documents", fields: [["title", "Title"], ["url", "URL"], ["note", "Note"]] },
  { k: "services", label: "Services", fields: [["name", "Name"], ["kind", "Kind", "database, queue, …"], ["note", "Note"]] },
];

type Row = Record<string, string>;

function ResourcesEditor({ w }: { w: AtWorkspace }) {
  const toRows = () => Object.fromEntries(KINDS.map(({ k }) => [k, ((w.resources?.[k] as Row[] | undefined) || []).map((x) => ({ ...x, url: k === "repos" && x.provider === "local" ? x.path : x.url }))])) as Record<string, Row[]>;
  const [rows, setRows] = useState<Record<string, Row[]>>(toRows);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!edit) setRows(toRows()); }, [w, edit]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    setBusy(true);
    try {
      const resources: Record<string, unknown[]> = {};
      for (const { k } of KINDS) {
        resources[k] = (rows[k] || []).filter((r) => Object.values(r).some((v) => String(v || "").trim())).map((r) => {
          if (k !== "repos") return r;
          const local = !/^https?:\/\//.test(r.url || "");
          return { name: r.name, branch: r.branch || "main", test_command: r.test_command, ...(local ? { provider: "local", path: r.url } : { provider: "github", url: r.url }) };
        });
      }
      await put(`/api/at/workspaces/${w.id}`, { resources });
      toast("资源已保存");
      setEdit(false);
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="section col gap16">
      <div className="row gap6">
        <span className="muted sm grow">Agent 只能看到这些资源；被唤醒时写进它的提示里。地址和 URL 是公开信息，这里不放任何密钥。</span>
        {!edit ? <button className="btn" disabled={DEMO} onClick={() => setEdit(true)}>Edit resources</button> : (
          <><button className="btn" onClick={() => setEdit(false)}>Cancel</button><button className="btn primary" disabled={busy} onClick={save}>Save</button></>
        )}
      </div>
      {KINDS.map(({ k, label, fields }) => {
        const list = rows[k] || [];
        if (!edit && !list.length) return null;
        return (
          <div key={k} className="col gap6">
            <div className="label">{label} <span className="mono muted">{list.length}</span></div>
            <div className="table-wrap boxed">
              <table className="dt">
                <thead><tr>{fields.map(([f, l]) => <th key={f}>{l}</th>)}{edit && <th />}</tr></thead>
                <tbody>
                  {list.map((r, i) => (
                    <tr key={i}>
                      {fields.map(([f, , ph]) => (
                        <td key={f} className={["address", "url", "test_command", "branch"].includes(f) ? "mono" : ""}>
                          {edit ? <input className="input sm" style={{ width: "100%" }} value={r[f] || ""} placeholder={ph} onChange={(e) => setRows({ ...rows, [k]: list.map((x, j) => (j === i ? { ...x, [f]: e.target.value } : x)) })} />
                            : r[f] || <span className="muted">—</span>}
                        </td>
                      ))}
                      {edit && <td className="r"><button className="btn ghost xs icon" onClick={() => setRows({ ...rows, [k]: list.filter((_, j) => j !== i) })} aria-label="删除"><X size={11} /></button></td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {edit && <div><button className="btn xs" onClick={() => setRows({ ...rows, [k]: [...list, {}] })}><Plus size={11} /> Add</button></div>}
          </div>
        );
      })}
      {!edit && KINDS.every(({ k }) => !(rows[k] || []).length) && <EmptyState small title="还没有资源">添加 GitHub 仓库（含测试命令）、BSC 合约和代币地址、钱包、API 地址，Agent 才知道自己负责什么。</EmptyState>}
    </div>
  );
}

function WatcherList({ w, watchers }: { w: AtWorkspace; watchers: (AtWatcher & { healthy: boolean; health: string })[] }) {
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<{ watcher: AtWatcher; secret: string | null; ingest_path: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (x: AtWatcher, a: "pause" | "resume" | "rotate" | "delete") => {
    if (a === "delete" && !confirm(`删除 Watcher ${x.name}？`)) return;
    if (a === "rotate" && !confirm("更换密钥后，旧的令牌或 Webhook secret 立即失效。继续？")) return;
    setBusy(x.id);
    try {
      if (a === "delete") await del(`/api/at/watchers/${x.id}`);
      else {
        const r = await post(`/api/at/watchers/${x.id}/${a}`);
        if (a === "rotate") setSecret(r);
      }
      refreshAt();
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="section col gap12">
      <div className="row gap6">
        <span className="muted sm grow">Watcher 观察工作区，把看到的东西变成事件；事件匹配 Agent 的职责时唤醒它。Watcher 本身不调用模型。</span>
        <button className="btn primary" disabled={DEMO} onClick={() => setAdding(true)}><Plus size={13} /> Add watcher</button>
      </div>
      {watchers.length ? (
        <div className="table-wrap boxed">
          <table className="dt">
            <thead><tr><th>Watcher</th><th>Kind</th><th>Health</th><th>Target</th><th>Last event</th><th>Last heartbeat</th><th /></tr></thead>
            <tbody>
              {watchers.map((x) => (
                <tr key={x.id}>
                  <td><b>{x.name}</b> <span className="muted mono xs">{x.id}</span></td>
                  <td className="muted">{WATCHER_LABEL[x.kind]}</td>
                  <td><span className={`st ${x.healthy ? "ok live" : x.status === "paused" ? "mute" : "bad"}`} title={x.health}><i /><span className="t">{x.healthy ? "Healthy" : x.status === "paused" ? "Paused" : "Not healthy"}</span></span> <span className="muted xs">{x.health}</span></td>
                  <td className="mono muted ellipsis" style={{ maxWidth: 260 }}>{x.config.url || x.config.address || x.config.repo || x.config.service || x.config.pipeline || "—"}</td>
                  <td className="mono muted">{x.last_event_at ? ago(x.last_event_at) : "—"}</td>
                  <td className="mono muted">{x.last_heartbeat_at ? ago(x.last_heartbeat_at) : "—"}</td>
                  <td className="r">
                    <span className="row gap2" style={{ justifyContent: "flex-end" }}>
                      {x.status === "active" ? <button className="btn xs" disabled={busy === x.id || DEMO} onClick={() => act(x, "pause")} title="Pause"><Pause size={11} /></button>
                        : <button className="btn xs" disabled={busy === x.id || DEMO} onClick={() => act(x, "resume")} title="Resume"><Play size={11} /></button>}
                      {x.has_secret && <button className="btn xs" disabled={busy === x.id || DEMO} onClick={() => act(x, "rotate")} title="更换密钥"><RefreshCw size={11} /></button>}
                      <button className="btn xs ghost" disabled={busy === x.id || DEMO} onClick={() => act(x, "delete")} aria-label="删除"><Trash2 size={11} /></button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <EmptyState small title="没有 Watcher">连接错误日志、CI、GitHub Webhook、API 健康检查或 BSC 合约，Agent 才会被真实事件唤醒。</EmptyState>}
      {adding && <AddWatcher w={w} onClose={() => setAdding(false)} onCreated={(r) => (setAdding(false), setSecret(r))} />}
      {secret && <SecretDialog r={secret} onClose={() => setSecret(null)} />}
    </div>
  );
}

function AddWatcher({ w, onClose, onCreated }: { w: AtWorkspace; onClose: () => void; onCreated: (r: { watcher: AtWatcher; secret: string | null; ingest_path: string | null }) => void }) {
  const meta = useAtMeta();
  const tokens = w.resources?.tokens || [];
  const contracts = w.resources?.contracts || [];
  const endpoints = w.resources?.endpoints || [];
  const [kind, setKind] = useState<AtWatcher["kind"]>("app_logs");
  const [name, setName] = useState("");
  const [c, setC] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await post("/api/at/watchers", { workspace_id: w.id, kind, name, config: c });
      refreshAt();
      onCreated(r);
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const field = (k: string, label: string, ph = "", mono = true) => (
    <label className="field"><span>{label}</span><input className={`input ${mono ? "mono" : ""}`} value={c[k] ?? ""} placeholder={ph} onChange={(e) => setC({ ...c, [k]: e.target.value })} /></label>
  );
  return (
    <Dialog wide title="Add watcher" onClose={onClose} footer={<><div className="grow" /><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={submit}>Create</button></>}>
      <div className="mode-grid">
        {Object.entries(meta.data?.watcher_kinds || {}).map(([k, v]) => (
          <label key={k} className={`perm ${kind === k ? "on" : ""}`}>
            <input type="radio" checked={kind === k} onChange={() => (setKind(k as AtWatcher["kind"]), setC({}))} />
            <span className="col gap2"><span className="sm strong">{WATCHER_LABEL[k]}</span><span className="muted xs">{v}</span></span>
          </label>
        ))}
      </div>
      <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={WATCHER_LABEL[kind]} /></label>
      {kind === "app_logs" && <div className="grid2">{field("service", "Service name", "pricing-api", false)}
        <label className="field"><span>Minimum level</span><select className="input" value={c.min_level || "error"} onChange={(e) => setC({ ...c, min_level: e.target.value })}><option>warning</option><option>error</option><option>critical</option></select></label></div>}
      {kind === "ci_failure" && field("pipeline", "Pipeline name", "tests", false)}
      {kind === "github_webhook" && field("repo", "Repository (owner/name, optional filter)", "owner/repo")}
      {kind === "api_health" && (
        <div className="grid2">
          <label className="field"><span>URL</span><input className="input mono" list="ws-endpoints" value={c.url ?? ""} onChange={(e) => setC({ ...c, url: e.target.value })} placeholder="https://api.example.com/health" />
            <datalist id="ws-endpoints">{endpoints.map((e) => <option key={e.url} value={e.url}>{e.name}</option>)}</datalist></label>
          {field("interval_s", "Interval (seconds, ≥ 30)", "60")}
          {field("expect_status", "Expected status", "200")}
          {field("fail_after", "Failures before alert", "2")}
        </div>
      )}
      {kind === "bsc_events" && (
        <div className="grid2">
          <label className="field"><span>Contract / token address</span><input className="input mono" list="ws-addrs" value={c.address ?? ""} onChange={(e) => setC({ ...c, address: e.target.value })} placeholder="0x…" />
            <datalist id="ws-addrs">{[...tokens, ...contracts].map((t) => <option key={t.address} value={t.address}>{(t as { symbol?: string }).symbol || t.label}</option>)}</datalist></label>
          <label className="field"><span>Watch</span><select className="input" value={c.monitor || "token_transfer"} onChange={(e) => setC({ ...c, monitor: e.target.value })}>
            <option value="token_transfer">Token transfers (BEP-20 Transfer)</option><option value="contract">Contract events (all or by topic)</option><option value="address">Address activity</option></select></label>
          {field("symbol", "Symbol", "KGA")}
          {field("large_transfer_raw", "Large transfer threshold (raw units)", "1000000000000000000000")}
          <label className="check"><input type="checkbox" checked={c.track_holders ?? true} onChange={(e) => setC({ ...c, track_holders: e.target.checked })} /> Track holders (balanceOf at each transfer)</label>
          <label className="check"><input type="checkbox" checked={!!c.alert_new_holders} onChange={(e) => setC({ ...c, alert_new_holders: e.target.checked })} /> Alert on new holders</label>
          <small className="muted" style={{ gridColumn: "1 / -1" }}>需要服务器配置 CHAINSTAFF_CHAIN（BSC 节点）。只有确认后的事件才唤醒 Agent；链重组会撤销还没执行的任务。</small>
        </div>
      )}
    </Dialog>
  );
}

function SecretDialog({ r, onClose }: { r: { watcher: AtWatcher; secret: string | null; ingest_path: string | null }; onClose: () => void }) {
  const base = API_BASE || location.origin;
  const url = r.ingest_path ? base + r.ingest_path : null;
  const k = r.watcher.kind;
  const example = k === "app_logs" ? `curl -X POST ${url} -H "Authorization: Bearer ${r.secret}" -H "Content-Type: application/json" \\\n  -d '{"level":"error","service":"api","message":"ZeroDivisionError: division by zero","stack":"..."}'`
    : k === "ci_failure" ? `curl -X POST ${url} -H "Authorization: Bearer ${r.secret}" -H "Content-Type: application/json" \\\n  -d '{"status":"failed","pipeline":"tests","branch":"main","commit":"abc123","failed_tests":["test_x"],"log_tail":"..."}'` : null;
  return (
    <Dialog wide title={`Watcher ${r.watcher.name} is ready`} onClose={onClose} footer={<><div className="grow" /><button className="btn primary" onClick={onClose}>Done</button></>}>
      {r.secret ? <div className="note acc">这个密钥只显示这一次，KAGOU 只保存{k === "github_webhook" ? "用来验证签名的 secret" : "它的哈希"}。现在复制保存。</div> : null}
      {url && <div className="field"><span>Endpoint</span><div className="row gap6"><code className="mono sm grow ellipsis">{url}</code><button className="btn xs" onClick={() => copy(url)}><Copy size={11} /></button></div></div>}
      {r.secret && <div className="field"><span>{k === "github_webhook" ? "Webhook secret" : "Token"}</span><div className="row gap6"><code className="mono sm grow ellipsis">{r.secret}</code><button className="btn xs" onClick={() => copy(r.secret!)}><Copy size={11} /></button></div></div>}
      {k === "github_webhook" && <div className="note">在 GitHub 仓库 Settings → Webhooks 添加：Payload URL 填上面的地址，Content type 选 application/json，Secret 填上面的 secret，事件选 push、pull requests、workflow runs、check runs、issues。</div>}
      {example && <pre className="code">{example}</pre>}
      {k === "api_health" && <div className="note">Worker 会按间隔请求这个地址；连续失败达到阈值时产生一次 health.down 事件，恢复时一次 health.recovered。</div>}
      {k === "bsc_events" && <div className="note">{r.watcher.status === "active" ? "已连接 BSC 监听：新区块里匹配的事件会被记录、确认后唤醒负责的 Agent。" : `没有连接：${r.watcher.last_error || "服务器没有 BSC 监听"}`}</div>}
    </Dialog>
  );
}

function EventList({ id }: { id: string }) {
  const q = useAtEvents({ workspace_id: id, limit: 200 });
  if (q.isLoading) return <div className="section"><Loading rows={6} /></div>;
  if (q.error) return <div className="section"><ErrorState error={q.error} /></div>;
  if (!q.data!.length) return <div className="section"><EmptyState small title="还没有事件">Watcher 收到的每个事件都记录在这里：去重、重试、确认状态和它创建的任务。</EmptyState></div>;
  return (
    <div className="table-wrap">
      <table className="dt">
        <thead><tr><th>Received</th><th>Type</th><th>Severity</th><th>Title</th><th>Status</th><th>Chain</th><th>Seen</th><th>Tasks</th></tr></thead>
        <tbody>
          {q.data!.map((e) => (
            <tr key={e.id}>
              <td className="mono muted" title={fmtTime(e.received_at)}>{ago(e.received_at)}</td>
              <td className="mono">{e.type}</td>
              <td><Sev s={e.severity} /></td>
              <td className="ellipsis" style={{ maxWidth: 380 }} title={e.title}>{e.title}</td>
              <td className={e.processing_status === "failed" ? "bad" : "muted"} title={e.error || ""}>{e.processing_status}</td>
              <td className="muted">{e.chain_status || "—"}</td>
              <td className="mono">{e.occurrences}×</td>
              <td>{e.task_ids.map((t) => <a key={t} className="link mono xs" href={`#/missions?task=${t}`}>{t} </a>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
