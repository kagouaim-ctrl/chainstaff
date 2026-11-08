import { Link } from 'react-router-dom'

/**
 * 没有 Agent 后端时的 Agent 页：讲清楚它怎么工作、风控怎么保证，不展示「全部已暂停」之类的空数据。
 */
const FLOW: [string, string][] = [
  ['Scout', '发现新代币和异动'],
  ['Security', '合约权限、貔貅、税率、LP 检查'],
  ['On-chain', '持有人、开发者、狙击者和关联钱包'],
  ['Market', '成交量、流动性和价格结构'],
  ['Strategy', '形成交易方案：仓位、止损、最长持有'],
  ['Risk', '独立审查，拥有否决权'],
  ['Execution', '只执行风控批准的方案'],
]

const RULES = [
  '单笔金额、每日亏损、最大持仓数、最大滑点、最低流动性都由程序强制执行，模型改不了。',
  '风控否决、数据异常或安全检查无法确认时，一律不交易。',
  '先在模拟盘连续运行并公开验证结果，达标前不会接入真实资金。',
  '自动交易需要你单独授权，并且随时可以一键紧急停止。',
  '每个决定都保存输入数据、模型、Prompt 版本、数据来源和风控结果，可以复查。',
]

export function AgentBeta() {
  return (
    <div className="mx-auto max-w-[760px]">
      <div className="flex items-center gap-2">
        <h1 className="text-[24px] font-bold">AI Agent</h1>
        <span className="rounded-md bg-agent/15 px-2 py-0.5 text-[12px] font-medium text-agent">内测中</span>
      </div>
      <p className="mt-2 text-[14px] font-normal text-sub">Agent 自动研究和自动交易还没有对外开放。现在你可以在代币页手动交易，所有交易都由你自己的钱包签名。</p>

      <h2 className="mt-8 text-[16px] font-medium">它会怎么工作</h2>
      <ol className="mt-3 grid gap-2 sm:grid-cols-2">
        {FLOW.map(([k, v], i) => (
          <li key={k} className="flex items-start gap-3 rounded-lg border border-line px-3 py-2.5">
            <span className="num mt-0.5 text-[12px] text-sub">{i + 1}</span>
            <span><span className="text-[14px]">{k}</span><span className="block text-[12px] font-normal text-sub">{v}</span></span>
          </li>
        ))}
      </ol>

      <h2 className="mt-8 text-[16px] font-medium">风控铁律</h2>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-[14px] font-normal text-fg/85">
        {RULES.map((r) => <li key={r}>{r}</li>)}
      </ul>

      <p className="mt-8 text-[13px] text-sub">开放时间和模拟盘战绩会在这里公布。<Link to="/legal/risk" className="text-agent hover:underline">风险披露</Link></p>
    </div>
  )
}
