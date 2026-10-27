import { ArrowLeft, ArrowRight, Check, Zap } from "lucide-react";
import { useState } from "react";
import { ErrorState, Loading } from "../components/ui";
import { post } from "../lib/api";
import { MODE_LABEL, refreshAt, useAtMeta, useAtRuntime, useAtWorkspaces, type AtAgentRow, type Template, type WorkMode } from "../lib/at";
import { DEMO } from "../lib/demo";
import { errText, toast } from "../lib/util";
import { ModePicker, PermissionPicker, WorkspacePicker } from "./Agents";

const STEPS = ["Name", "Responsibility", "Workspace", "Tools & permissions", "Mode", "Budget & approval", "Activate"];

interface Form {
  template: string;
  name: string;
  responsibility: string;
  workspace_ids: string[];
  permissions: string[];
  skills: string[];
  work_mode: WorkMode;
  schedule_cron: string;
  wake_on: string[] | null;
  budget_usd: string;
  max_task_budget_usd: string;
  min_severity: string;
  on_failure: boolean;
  on_completion: boolean;
  provider: string;
  model: string;
}

/** New Agent in seven steps. Nothing is created until the last step; the agent is saved and activated in one call. */
export function AgentWizard({ onDone, onCancel }: { onDone: (id: string) => void; onCancel: () => void }) {
  const meta = useAtMeta();
  const ws = useAtWorkspaces();
  const rt = useAtRuntime();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState<Form>({
    template: "", name: "", responsibility: "", workspace_ids: [], permissions: ["delegate", "memory:write"], skills: [], work_mode: "event", schedule_cron: "",
    wake_on: null, budget_usd: "0", max_task_budget_usd: "", min_severity: "medium", on_failure: true, on_completion: false, provider: "", model: "",
  });
  if (meta.isLoading || ws.isLoading) return <div className="page"><Loading rows={8} /></div>;
  if (meta.error) return <div className="page"><ErrorState error={meta.error} /></div>;
  const m = meta.data!;
  const set = (p: Partial<Form>) => setF((x) => ({ ...x, ...p }));
  const pick = (t: Template | null) =>
    set(t ? { template: t.id, name: f.name || t.name, responsibility: t.responsibility, permissions: [...new Set([...t.permissions, ...(t.extra || [])])], skills: t.skills,
              work_mode: t.work_mode, schedule_cron: t.schedule_cron || "", wake_on: t.wake_on === undefined ? null : t.wake_on }
          : { template: "", permissions: [...m.default_permissions], skills: [], work_mode: "event", wake_on: null });
  const providers = m.models.providers;
  const provider = f.provider || (providers.find((p) => p.provider === "anthropic") ? "anthropic" : providers[0]?.provider || "anthropic");
  const can = [
    !!f.name.trim(),
    !!f.responsibility.trim(),
    f.work_mode === "on_demand" || f.workspace_ids.length > 0,
    true,
    f.work_mode !== "scheduled" || !!f.schedule_cron.trim(),
    true,
    true,
  ];
  const warnings = [
    !providers.some((p) => p.provider === provider) && `服务器没有配置 ${provider} 的 API Key：Agent 被唤醒时会执行失败并通知你，配置后自动恢复。`,
    rt.data && !rt.data.worker_alive && "现在没有在线的 Worker：任务会排队，Worker 启动后执行。",
    f.work_mode !== "on_demand" && !f.workspace_ids.length && "Agent 还没有工作区。",
  ].filter(Boolean) as string[];
  const create = async (activate: boolean) => {
    setBusy(true);
    try {
      const a = await post<AtAgentRow & { id: string }>("/api/at/agents", {
        template: f.template || undefined, name: f.name.trim(), responsibility: f.responsibility.trim(), workspace_ids: f.workspace_ids, permissions: f.permissions,
        skills: f.skills, work_mode: f.work_mode, schedule_cron: f.schedule_cron || null, wake_on: f.wake_on, budget_usd: f.budget_usd || "0",
        provider, model: f.model || undefined, activate,
        approval_rules: { max_task_budget_usd: f.max_task_budget_usd || null },
        notify_rules: { min_severity: f.min_severity, on_failure: f.on_failure, on_completion: f.on_completion },
      });
      toast(activate ? `${a.name} 已激活，开始工作` : `${a.name} 已保存（未激活）`);
      refreshAt();
      onDone(a.id);
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <div className="page">
      <div className="toolbar">
        <button className="btn ghost icon" onClick={onCancel} aria-label="返回"><ArrowLeft size={15} /></button>
        <h1>New agent</h1>
        <span className="sub">Create an agent. Give it a job. Let it work.</span>
      </div>
      <ol className="wiz-steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "on" : i < step ? "done" : ""}>
            <button className="plain-btn" disabled={i > step && !can.slice(0, i).every(Boolean)} onClick={() => setStep(i)}>
              <span className="n mono">{i < step ? <Check size={11} /> : i + 1}</span> {s}
            </button>
          </li>
        ))}
      </ol>
      <div className="section col gap12 wiz-body">
        {step === 0 && (
          <>
            <label className="field"><span>Name</span><input className="input" autoFocus value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="例如 Agent 1、Bug Hunter、KGA Token Watcher" /></label>
            <div className="field">
              <span>Start from a template (optional; it only fills the next steps, everything stays editable)</span>
              <div className="tpl-grid">
                <button type="button" className={`perm ${!f.template ? "on" : ""}`} onClick={() => pick(null)}><span className="col gap2"><span className="sm strong">Blank</span><span className="muted xs">从零开始写职责</span></span></button>
                {m.templates.map((t) => (
                  <button type="button" key={t.id} className={`perm ${f.template === t.id ? "on" : ""}`} onClick={() => pick(t)}>
                    <span className="col gap2"><span className="sm strong">{t.name} <span className="muted xs">{MODE_LABEL[t.work_mode]}</span></span><span className="muted xs clamp2">{t.responsibility}</span></span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
        {step === 1 && (
          <label className="field">
            <span>Responsibility: what this agent is in charge of, in your words</span>
            <textarea className="input" rows={7} autoFocus value={f.responsibility} onChange={(e) => set({ responsibility: e.target.value })}
              placeholder="例如：你负责持续检查这个项目是否存在 Bug。发现问题后主动调查，整理证据，并把需要修复的问题交给合适的 Agent。修复合并后验证问题是否消失。" />
            <small>这段话是 Agent 每次被唤醒时读到的长期职责。其他 Agent 交接任务时，系统也按它匹配谁来接。</small>
          </label>
        )}
        {step === 2 && (
          <>
            <WorkspacePicker value={f.workspace_ids} onChange={(v) => set({ workspace_ids: v })} options={(ws.data || []).map((w) => ({ id: w.id, name: w.name }))} />
            <small className="muted">Agent 只能看到和操作它被授权的工作区里的仓库、合约、代币、钱包和 API。<a className="link" href="#/workspace?new=1" target="_blank" rel="noreferrer">新建工作区</a>后回到这里刷新。</small>
          </>
        )}
        {step === 3 && (
          <>
            <PermissionPicker meta={m} value={f.permissions} onToggle={(p) => set({ permissions: toggle(f.permissions, p) })} />
            <div className="field">
              <span>Web3 skills</span>
              <div className="chips">{m.skills.map((s) => <button type="button" key={s.id} className="chip" aria-pressed={f.skills.includes(s.id)} onClick={() => set({ skills: toggle(f.skills, s.id) })} title={s.description}>{s.name}</button>)}</div>
            </div>
          </>
        )}
        {step === 4 && <ModePicker meta={m} mode={f.work_mode} cron={f.schedule_cron} wake={f.wake_on} onChange={(p) => set(p as Partial<Form>)} />}
        {step === 5 && (
          <div className="grid2 stack-m">
            <label className="field"><span>Agent budget (USD, 0 = no cap)</span><input className="input mono" value={f.budget_usd} onChange={(e) => set({ budget_usd: e.target.value })} /><small>累计花费到这个数就停止调用模型。</small></label>
            <label className="field"><span>Max budget per task (USD)</span><input className="input mono" value={f.max_task_budget_usd} onChange={(e) => set({ max_task_budget_usd: e.target.value })} placeholder={String(m.limits.default_task_budget)} /><small>每个任务的上限，默认 ${String(m.limits.default_task_budget)}。</small></label>
            <label className="field"><span>Model provider</span>
              <select className="input" value={provider} onChange={(e) => set({ provider: e.target.value })}>
                <option value="anthropic">Anthropic (Claude){providers.some((p) => p.provider === "anthropic") ? "" : " · key not set"}</option>
                <option value="openai_compat">OpenAI-compatible{providers.some((p) => p.provider === "openai_compat") ? ` · ${providers.find((p) => p.provider === "openai_compat")!.label}` : " · not configured"}</option>
              </select>
            </label>
            <label className="field"><span>Model (empty = server default)</span><input className="input mono" value={f.model} onChange={(e) => set({ model: e.target.value })} placeholder={m.models.default_model} /></label>
            <label className="field"><span>Notify me from severity</span><select className="input" value={f.min_severity} onChange={(e) => set({ min_severity: e.target.value })}>{m.severities.map((s) => <option key={s}>{s}</option>)}</select></label>
            <div className="col gap6" style={{ justifyContent: "flex-end" }}>
              <label className="check"><input type="checkbox" checked={f.on_failure} onChange={(e) => set({ on_failure: e.target.checked })} /> Notify when a task fails</label>
              <label className="check"><input type="checkbox" checked={f.on_completion} onChange={(e) => set({ on_completion: e.target.checked })} /> Notify when a task completes</label>
            </div>
            <div className="note" style={{ gridColumn: "1 / -1" }}>
              Approval rules: every patch and Draft PR waits for you. Merging, deploying, contract upgrades, admin changes, signing transactions and reading secrets are never given to an agent.
              Server limits: delegation depth {String(m.limits.max_depth)}, {String(m.limits.max_attempts)} attempts per task, {String(m.limits.max_model_calls)} model calls per run, {String(m.limits.fix_attempts_per_issue)} fix attempts per issue per day.
            </div>
          </div>
        )}
        {step === 6 && (
          <div className="col gap12">
            <dl className="kv">
              <dt>Name</dt><dd>{f.name}</dd>
              <dt>Responsibility</dt><dd className="pre">{f.responsibility}</dd>
              <dt>Workspaces</dt><dd>{f.workspace_ids.map((w) => ws.data?.find((x) => x.id === w)?.name || w).join(", ") || "—"}</dd>
              <dt>Permissions</dt><dd className="mono">{f.permissions.join(", ") || "read only"}</dd>
              <dt>Skills</dt><dd className="mono">{f.skills.join(", ") || "—"}</dd>
              <dt>Mode</dt><dd>{MODE_LABEL[f.work_mode]}{f.schedule_cron ? ` · ${f.schedule_cron} UTC` : ""}</dd>
              <dt>Model</dt><dd className="mono">{provider} · {f.model || m.models.default_model}</dd>
              <dt>Budget</dt><dd className="mono">{Number(f.budget_usd) > 0 ? `$${f.budget_usd}` : "no agent cap"} · per task {f.max_task_budget_usd ? `$${f.max_task_budget_usd}` : `$${String(m.limits.default_task_budget)}`}</dd>
            </dl>
            {warnings.map((w) => <div key={w} className="note acc">{w}</div>)}
            <div className="note">激活后，系统为每个工作区建立这个 Agent 的长期职责 Mission。它由事件、定时或其他 Agent 唤醒，在云端 Worker 里执行，关掉浏览器也会继续。</div>
          </div>
        )}
        <div className="row gap6">
          {step > 0 && <button className="btn" onClick={() => setStep(step - 1)}><ArrowLeft size={13} /> Back</button>}
          <div className="grow" />
          {step < 6 && <button className="btn primary" disabled={!can[step]} onClick={() => setStep(step + 1)}>Next <ArrowRight size={13} /></button>}
          {step === 6 && <button className="btn" disabled={busy || DEMO || !can.every(Boolean)} onClick={() => create(false)}>Save without activating</button>}
          {step === 6 && <button className="btn primary" disabled={busy || DEMO || !can.every(Boolean)} onClick={() => create(true)}><Zap size={13} /> Activate</button>}
        </div>
      </div>
    </div>
  );
}
