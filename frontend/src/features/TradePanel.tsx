import { useState } from 'react'
import { useToast } from '@/app/store'
import { useApp } from '@/app/store'
import { useStrategy, usePortfolio, usePositions } from '@/api/hooks'
import { PLATFORM_FEE_RATE } from '@/api/demo'
import { Button, Segmented, KV, DemoTag, cx } from '@/components/ui'
import { usd, bps, price } from '@/lib/format'
import type { Token } from '@/domain/types'
import { securityPass } from './tokenBits'

/** 手动模拟交易面板。手动下单同样要经过风控，执行只走唯一入口。 */
export function TradePanel({ token }: { token: Token }) {
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [amount, setAmount] = useState('10')
  const [slip, setSlip] = useState(300)
  const st = useStrategy(); const pf = usePortfolio(); const pos = usePositions()
  const toast = useToast((s) => s.show)
  const paused = useApp((s) => s.paused)
  const holding = pos.data?.find((p) => p.token.address === token.address)
  const usdAmt = Number(amount) || 0
  const total = pf.data?.totalUsd ?? 0
  const maxTrade = st.data ? (total * st.data.policy.maxTradePct) / 100 : 0
  const impactPct = usdAmt > 0 ? (usdAmt / token.liquidityUsd) * 100 * 2 : 0
  const platform = usdAmt * PLATFORM_FEE_RATE
  const network = 0.0011 + 0.0042
  const sec = securityPass(token.security)
  const issues: string[] = []
  if (paused) issues.push('已紧急暂停，新指令会被拒绝')
  if (side === 'BUY' && usdAmt > maxTrade) issues.push(`超过单笔上限 ${usd(maxTrade)}（总资金 ${st.data?.policy.maxTradePct}%）`)
  if (side === 'BUY' && sec === false) issues.push('安全检查未通过，一票否决')
  if (side === 'BUY' && sec === null) issues.push('安全检查未完成，结果无法确认时默认拒绝')
  if (token.liquidityUsd < (st.data?.policy.minLiquidityUsd ?? 0)) issues.push('流动性低于下限')
  if (side === 'SELL' && !holding) issues.push('没有持仓，不能卖出')
  if (impactPct * 100 > slip) issues.push('预计价格冲击超过滑点上限')
  const presets = side === 'BUY' ? [...new Set([5, 10, 20, Math.floor(maxTrade)])] : null

  return (
    <div id="trade" className="rounded-card border border-line bg-panel">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold">手动交易 <span className="rounded border border-paper/40 bg-paper/10 px-1.5 py-0.5 text-[11px] text-paper">模拟</span><DemoTag /></h2>
        <Segmented label="买卖方向" size="sm" value={side} onChange={setSide} options={[{ value: 'BUY', label: '买入' }, { value: 'SELL', label: '卖出' }]} />
      </div>
      <div className="space-y-3 p-4">
        <label className="block">
          <span className="text-xs text-sub">{side === 'BUY' ? '金额（USDC）' : `卖出比例（持仓 ${holding ? usd(holding.valueUsd) : '无'}）`}</span>
          <div className="mt-1 flex items-center rounded-lg border border-line bg-bg focus-within:border-agent">
            <span className="pl-3 text-sub">{side === 'BUY' ? '$' : '%'}</span>
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))} aria-label="金额"
              className="num h-11 min-w-0 flex-1 bg-transparent px-2 text-lg font-semibold outline-none" />
          </div>
        </label>
        <div className="flex gap-1.5">
          {(presets ?? [25, 50, 100]).map((p) => (
            <button key={p} onClick={() => setAmount(String(p))} className={cx('num flex-1 rounded-lg border py-1.5 text-xs', amount === String(p) ? 'border-fg/20 bg-raised' : 'border-line text-sub hover:text-fg')}>
              {side === 'BUY' ? (p === Math.floor(maxTrade) ? '上限' : `$${p}`) : `${p}%`}
            </button>
          ))}
        </div>
        <div className="flex justify-between text-xs text-sub"><span>可用余额</span><span className="num text-fg">{usd(pf.data?.cashUsd ?? 0)}</span></div>
        <label className="flex items-center justify-between text-xs text-sub">
          最大滑点
          <select value={slip} onChange={(e) => setSlip(Number(e.target.value))} className="rounded-md border border-line bg-bg px-2 py-1 text-fg">
            {[100, 200, 300].map((v) => <option key={v} value={v}>{bps(v)}</option>)}
          </select>
        </label>
        <div className="rounded-lg border border-line px-3 py-1">
          <KV k="参考价格" v={price(token.priceUsd)} />
          <KV k="预计价格冲击（估算）" v={impactPct > 0 && impactPct < 0.01 ? '< 0.01%' : `${impactPct.toFixed(2)}%`} />
          <KV k="网络费 + 优先费" v={usd(network)} />
          <KV k="平台手续费（含 AI 费用）" v={usd(platform)} />
          <KV k="报价来源" v={<span className="text-warn">Jupiter 未接入</span>} />
        </div>
        {issues.length > 0 && (
          <ul className="space-y-1 rounded-lg border border-down/30 bg-down/10 p-2.5 text-xs text-down">
            {issues.map((i) => <li key={i}>风控预检：{i}</li>)}
          </ul>
        )}
        <Button variant={side === 'BUY' ? 'primary' : 'down'} className="h-12 w-full rounded-full text-[15px] font-semibold"
          onClick={() => toast('后端未接入：没有创建订单。接入后，这笔指令会先交给风控审批，批准后才由执行服务模拟成交。')}>
          {side === 'BUY' ? '模拟买入' : '模拟卖出'} {token.symbol}
        </Button>
        <p className="text-[11px] leading-relaxed text-sub">手动下单与 Agent 下单走同一套风控和执行。模拟盘按真实报价、800ms 延迟后重新取价，可能部分成交或失败。</p>
      </div>
    </div>
  )
}
