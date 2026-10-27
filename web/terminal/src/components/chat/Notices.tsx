import { AlertTriangle, ArrowRight, Plus, Wallet } from "lucide-react";
import { useAtApprovals, useAtOverview, useAtRuntime, useAtWorkspaces } from "../../lib/at";
import { DEMO } from "../../lib/demo";
import { openCreateWorker, openOps } from "../../lib/shell";
import { go, usd } from "../../lib/util";
import { ApprovalMessage } from "../Approval";

/** Share of a budget used; null when there is no budget to compare with. */
export function budgetUse(spent?: string | null, budget?: string | null): number | null {
  const b = Number(budget || 0);
  return b > 0 ? Number(spent || 0) / b : null;
}

/**
 * What needs the person before they type: approvals waiting (as short messages, at most two), system problems,
 * a budget close to its cap, and a missing first agent or workspace. Each line is one sentence with one way out.
 */
export function Notices({ approvals = true }: { approvals?: boolean }) {
  const ap = useAtApprovals("pending");
  const o = useAtOverview();
  const rt = useAtRuntime();
  const ws = useAtWorkspaces();
  const pending = ap.data || [];
  const problems = o.data?.health.problems || [];
  const g = rt.data?.gateway;
  const day = budgetUse(g?.spent_today_usd, g?.daily_budget_usd);
  const month = budgetUse(g?.spent_month_usd, g?.monthly_budget_usd);
  const near = Math.max(day ?? 0, month ?? 0) >= 0.8;
  const noAgents = o.data && !o.data.agents.length;
  const noWs = ws.data && !ws.data.filter((w) => !w.archived).length;
  if (!pending.length && !problems.length && !near && !noAgents && !noWs) return null;
  return (
    <div className="notices">
      {approvals && pending.slice(0, 2).map((a) => <ApprovalMessage key={a.id} a={a} />)}
      {approvals && pending.length > 2 && (
        <button className="notice link-row" onClick={() => openOps("approvals")}>还有 {pending.length - 2} 项等待你确认 <ArrowRight size={13} /></button>
      )}
      {problems.map((p) => (
        <div key={p} className="notice warn">
          <AlertTriangle size={14} />
          <span className="grow">{p}</span>
          {p.includes("Worker") && <a className="link xs" href="#/settings?s=runtime">查看运行状态</a>}
          {p.includes("API Key") && <a className="link xs" href="#/settings?s=models">配置模型</a>}
        </div>
      ))}
      {near && g && (
        <div className="notice warn">
          <Wallet size={14} />
          <span className="grow">
            模型花费接近预算：{day !== null && `今日 ${usd(g.spent_today_usd)} / $${g.daily_budget_usd}（${Math.round(day * 100)}%）`}
            {day !== null && month !== null && "，"}{month !== null && `本月 ${usd(g.spent_month_usd)} / $${g.monthly_budget_usd}（${Math.round(month * 100)}%）`}。到达上限后任务会停止并通知你。
          </span>
          <a className="link xs" href="#/usage">Usage</a>
        </div>
      )}
      {noWs && (
        <div className="notice">
          <span className="grow">还没有工作区。工作区登记 Agent 可以使用的仓库、合约、代币、钱包和 API，KAGOU 按它分配任务。</span>
          <button className="btn sm" onClick={() => go("workspace")}>建立工作区</button>
        </div>
      )}
      {noAgents && !noWs && (
        <div className="notice">
          <span className="grow">还没有 Agent。建一个，写下它负责什么，就可以在这里把任务交给它。</span>
          {!DEMO && <button className="btn sm" onClick={openCreateWorker}><Plus size={13} /> 新建 Agent</button>}
        </div>
      )}
    </div>
  );
}
