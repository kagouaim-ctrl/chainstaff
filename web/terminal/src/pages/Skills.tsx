import { Play, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Dialog, EmptyState, ErrorState, Loading, Seg, Status } from "../components/ui";
import { post } from "../lib/api";
import { useAction, useAgentName, useSkills } from "../lib/queries";
import type { Skill } from "../lib/types";
import { useIsMobile } from "../lib/util";

export default function Skills() {
  const q = useSkills();
  const name = useAgentName();
  const mobile = useIsMobile();
  const [cat, setCat] = useState("all");
  const [state, setState] = useState("all");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Skill | null>(null);
  const act = useAction(({ id, action }: { id: string; action: string }) => post(`/api/wf/skills/${id}/${action}`), ["skills", "meta"], "已更新");
  const cats = useMemo(() => ["all", ...new Set((q.data || []).map((s) => s.category))], [q.data]);
  const rows = (q.data || []).filter(
    (s) =>
      (cat === "all" || s.category === cat) &&
      (state === "all" || (state === "installed" ? s.installed : state === "enabled" ? s.enabled : !s.installed)) &&
      (!search || `${s.name} ${s.description} ${s.id}`.toLowerCase().includes(search.toLowerCase())),
  );
  return (
    <div className="page">
      <div className="toolbar">
        <h1>Skills</h1>
        <span className="sub">Web3 skills your agents can be granted. Built in, read real chain data; third-party install is not built yet.</span>
      </div>
      <div className="qab">
        <div className="search" style={{ width: mobile ? "100%" : 240 }}>
          <Search size={13} />
          <input className="input" placeholder="搜索技能" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Seg value={state} onChange={setState} options={[["all", "All"], ["installed", "Installed"], ["enabled", "Enabled"], ["available", "Available"]]} />
        <select className="input" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="分类">
          {cats.map((c) => <option key={c} value={c}>{c === "all" ? "All categories" : c}</option>)}
        </select>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !rows.length ? <EmptyState title="没有符合条件的技能" /> : (
        <div className="table-wrap">
          <table className="dt">
            <thead>
              <tr><th>Skill</th>{!mobile && <th>Category</th>}{!mobile && <th>Version</th>}{!mobile && <th>Permissions</th>}{!mobile && <th>Used by</th>}<th>Status</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="click" onClick={() => setOpen(s)}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{s.name}</div>
                    <div className="muted ellipsis" style={{ maxWidth: mobile ? 200 : 380, fontSize: 11 }}>{s.description}</div>
                  </td>
                  {!mobile && <td><span className="tag">{s.category}</span></td>}
                  {!mobile && <td className="mono">{s.installed_version || s.version}{s.update_available && <span className="acc"> → {s.version}</span>}</td>}
                  {!mobile && <td className="mono muted" style={{ fontSize: 11 }}>{s.permissions.join(" ")}</td>}
                  {!mobile && <td className="muted">{s.agents.length ? s.agents.map(name).join(", ") : "—"}</td>}
                  <td>
                    {!s.installed ? <span className="badge">available</span> : s.enabled ? <Status status="completed" label="Enabled" /> : <Status status="cancelled" label="Disabled" />}
                    {s.missing.length > 0 && <span className="badge warn" title={`缺少：${s.missing.join(", ")}`}>needs config</span>}
                  </td>
                  <td onClick={(e) => e.stopPropagation()} className="r">
                    <SkillButtons s={s} busy={act.isPending} act={(action) => act.mutate({ id: s.id, action })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <SkillDialog s={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function SkillButtons({ s, act, busy }: { s: Skill; act: (a: string) => void; busy: boolean }) {
  if (!s.installed) return <button className="btn sm primary" disabled={busy} onClick={() => act("install")}>Install</button>;
  return (
    <div className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
      {s.update_available && <button className="btn sm" disabled={busy} onClick={() => act("update")}>Update</button>}
      <button className="btn sm" disabled={busy} onClick={() => act(s.enabled ? "disable" : "enable")}>{s.enabled ? "Disable" : "Enable"}</button>
      <button className="btn sm ghost" disabled={busy} onClick={() => confirm(`卸载 ${s.name}？使用它的员工将无法执行这个技能。`) && act("uninstall")}>Uninstall</button>
    </div>
  );
}

/** Details plus a try-run form built from the skill's input schema. */
function SkillDialog({ s, onClose }: { s: Skill; onClose: () => void }) {
  const props: Record<string, any> = s.input_schema?.properties || {};
  const required: string[] = s.input_schema?.required || [];
  const [input, setInput] = useState<Record<string, string>>({});
  const [out, setOut] = useState<any>(null);
  const run = useAction(() => {
    const body: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input)) {
      if (v === "") continue;
      body[k] = props[k]?.type === "integer" ? Number(v) : props[k]?.type === "array" ? v.split(",").map((x) => x.trim()).filter(Boolean) : v;
    }
    return post(`/api/wf/skills/${s.id}/run`, { input: body });
  }, ["activity"]);
  const runnable = s.installed && s.enabled && !s.missing.length && s.id !== "data_report";
  return (
    <Dialog wide title={s.name} onClose={onClose}>
      <p className="dim" style={{ marginTop: 0 }}>{s.description}</p>
      <dl className="kv">
        <dt>ID</dt><dd className="mono">{s.id}</dd>
        <dt>Developer</dt><dd>{s.developer}</dd>
        <dt>Version</dt><dd className="mono">{s.installed_version || "not installed"} (latest {s.version})</dd>
        <dt>Compatibility</dt><dd>{s.compatibility}</dd>
        <dt>Permissions</dt><dd className="mono">{s.permissions.join(", ") || "none"}</dd>
        <dt>Requires</dt><dd className="mono">{s.requires.join(", ") || "—"}{s.missing.length > 0 && <span className="warn"> · missing {s.missing.join(", ")}</span>}</dd>
      </dl>
      <div className="grid2">
        <div><div className="label">Input schema</div><pre className="report">{JSON.stringify(s.input_schema, null, 2)}</pre></div>
        <div><div className="label">Output schema</div><pre className="report">{JSON.stringify(s.output_schema, null, 2)}</pre></div>
      </div>
      {runnable ? (
        <div className="col">
          <div className="label">Try it</div>
          {Object.entries(props).map(([k, p]) => (
            <label key={k} className="field">
              <span>{k}{required.includes(k) ? " *" : ""} <span className="muted mono">{p.type}</span></span>
              <input className="input mono" value={input[k] || ""} placeholder={p.description || (p.type === "array" ? "comma separated" : "")} onChange={(e) => setInput({ ...input, [k]: e.target.value })} />
            </label>
          ))}
          <div><button className="btn primary" disabled={run.isPending || required.some((k) => !input[k])} onClick={() => run.mutate(undefined, { onSuccess: setOut })}><Play size={12} /> Run on {s.needs_chain ? "chain" : "server"}</button></div>
          {out && (
            <>
              <pre className="report">{JSON.stringify(out.output, null, 2)}</pre>
              <pre className="report muted">{out.logs.join("\n")}</pre>
            </>
          )}
        </div>
      ) : (
        <div className="note">{s.id === "data_report" ? "数据报告只在任务里汇总上游结果，不能单独运行。" : "安装并启用、补齐配置后才能试运行。"}</div>
      )}
    </Dialog>
  );
}
