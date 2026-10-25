import { useState } from "react";
import { post, put } from "../lib/api";
import { useAction, useMeta } from "../lib/queries";
import type { Agent } from "../lib/types";
import { Dialog } from "./ui";

export function AgentForm({ agent, onClose, onSaved }: { agent?: Agent; onClose: () => void; onSaved?: (a: Agent) => void }) {
  const meta = useMeta();
  const [f, setF] = useState({
    name: agent?.name || "",
    role: agent?.role || "",
    description: agent?.description || "",
    system_prompt: agent?.system_prompt || "",
    provider: agent?.provider || "none",
    model: agent?.model || "",
    effort: agent?.model_config?.effort || "",
    max_tokens: agent?.model_config?.max_tokens ? String(agent.model_config.max_tokens) : "",
    max_tool_calls: agent?.model_config?.max_tool_calls ? String(agent.model_config.max_tool_calls) : "",
    max_model_calls: agent?.model_config?.max_model_calls ? String(agent.model_config.max_model_calls) : "",
    call_timeout_s: agent?.model_config?.call_timeout_s ? String(agent.model_config.call_timeout_s) : "",
    skills: agent?.skills || [],
    permissions: agent?.permissions || [],
    is_manager: agent?.is_manager || false,
    budget_usd: agent?.budget_usd || "0",
    onchain_id: agent?.onchain?.agent_id || "",
    onchain_address: agent?.onchain?.address || "",
  });
  const set = (k: keyof typeof f, v: unknown) => setF((p) => ({ ...p, [k]: v }));
  const toggle = (k: "skills" | "permissions", v: string) => set(k, f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v]);
  const save = useAction(
    () => {
      const body = {
        name: f.name, role: f.role, description: f.description, system_prompt: f.system_prompt, provider: f.provider,
        model: f.provider === "anthropic" ? f.model : "", is_manager: f.is_manager, budget_usd: f.budget_usd || "0",
        model_config: {
          ...(f.effort ? { effort: f.effort } : {}),
          ...Object.fromEntries((["max_tokens", "max_tool_calls", "max_model_calls", "call_timeout_s"] as const).filter((k) => f[k]).map((k) => [k, Number(f[k])])),
        },
        skills: f.skills, permissions: f.permissions, onchain: { agent_id: f.onchain_id, address: f.onchain_address },
      };
      return agent ? put(`/api/wf/agents/${agent.id}`, body) : post("/api/wf/agents", body);
    },
    ["agents", "overview"],
    agent ? "已保存" : "员工已创建",
  );
  // Granting a skill also offers the permissions it needs, so a new agent works on the first try.
  const needed = new Set((meta.data?.skills || []).filter((s) => f.skills.includes(s.id)).flatMap((s) => s.permissions));
  const missing = [...needed].filter((p) => !f.permissions.includes(p));
  return (
    <Dialog
      wide
      title={agent ? `编辑 ${agent.name}` : "创建 AI 员工"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!f.name.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: (a) => (onSaved?.(a as Agent), onClose()) })}>
            {agent ? "保存" : "创建"}
          </button>
        </>
      }
    >
      <div className="grid2">
        <label className="field">
          <span>Agent name *</span>
          <input className="input" value={f.name} maxLength={40} onChange={(e) => set("name", e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Role</span>
          <input className="input" value={f.role} maxLength={60} placeholder="Contract Analyst" onChange={(e) => set("role", e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Description</span>
        <input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={f.is_manager} onChange={(e) => set("is_manager", e.target.checked)} />
        AI Manager（接收用户目标、拆分任务、分配员工、汇总结果）
      </label>
      <div className="grid2">
        <label className="field">
          <span>Model provider</span>
          <select className="input" value={f.provider} onChange={(e) => set("provider", e.target.value)}>
            <option value="none">None（只执行技能，不调用模型）</option>
            <option value="anthropic">Anthropic</option>
          </select>
          {f.provider === "anthropic" && !meta.data?.engine.llm && <small className="warn">服务器没有 ANTHROPIC_API_KEY，模型调用不会发生</small>}
        </label>
        {f.provider === "anthropic" && (
          <label className="field">
            <span>Model</span>
            <input className="input mono" value={f.model} placeholder={meta.data?.default_model} onChange={(e) => set("model", e.target.value)} />
          </label>
        )}
      </div>
      {f.provider === "anthropic" && (
        <>
          <div className="grid2">
            <label className="field">
              <span>Effort</span>
              <select className="input" value={f.effort} onChange={(e) => set("effort", e.target.value)}>
                <option value="">default (medium)</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="xhigh">xhigh</option>
                <option value="max">max</option>
              </select>
            </label>
            <label className="field">
              <span>Max tokens</span>
              <input className="input mono" inputMode="numeric" value={f.max_tokens} onChange={(e) => set("max_tokens", e.target.value.replace(/\D/g, ""))} />
            </label>
          </div>
          <div className="grid3">
            {([["max_tool_calls", "Max tool calls", "12"], ["max_model_calls", "Max model calls", "8"], ["call_timeout_s", "Call timeout (s)", "120"]] as const).map(([k, label, ph]) => (
              <label key={k} className="field">
                <span>{label}</span>
                <input className="input mono" inputMode="numeric" placeholder={ph} value={f[k]} onChange={(e) => set(k, e.target.value.replace(/\D/g, ""))} />
              </label>
            ))}
          </div>
          <small className="muted">执行上限：一次「自主执行」任务里最多调用几次工具和模型、每次模型调用的超时。留空用默认值。</small>
          <label className="field">
            <span>System prompt</span>
            <textarea className="input" rows={4} value={f.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
          </label>
        </>
      )}
      <div className="field">
        <span>Skills</span>
        <div className="chips">
          {(meta.data?.skills || []).map((s) => (
            <button key={s.id} type="button" className="chip" aria-pressed={f.skills.includes(s.id)} onClick={() => toggle("skills", s.id)}>
              {s.name}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <span>Permissions</span>
        <div className="chips">
          {Object.entries(meta.data?.permissions || {}).map(([p, desc]) => (
            <button key={p} type="button" className="chip" aria-pressed={f.permissions.includes(p)} onClick={() => toggle("permissions", p)} title={desc}>
              <span className="mono">{p}</span>
            </button>
          ))}
        </div>
        {missing.length > 0 && (
          <small className="warn">
            所选技能还需要：{missing.join(", ")}{" "}
            <button type="button" className="btn ghost sm" onClick={() => set("permissions", [...f.permissions, ...missing])}>
              一键加上
            </button>
          </small>
        )}
      </div>
      <div className="grid2">
        <label className="field">
          <span>Budget (USD, 0 = no cap)</span>
          <input className="input mono" value={f.budget_usd} onChange={(e) => set("budget_usd", e.target.value)} />
        </label>
        <label className="field">
          <span>ERC-8004 agent ID</span>
          <input className="input mono" value={f.onchain_id} placeholder="e.g. 2648" onChange={(e) => set("onchain_id", e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>On-chain address</span>
        <input className="input mono" value={f.onchain_address} placeholder="0x…" onChange={(e) => set("onchain_address", e.target.value)} />
        <small>只记录身份；员工不会拿到这个地址的私钥。</small>
      </label>
    </Dialog>
  );
}
