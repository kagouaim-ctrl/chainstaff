import { useState } from 'react'
import { useToast } from '@/app/store'
import { useApp } from '@/app/store'
import { useStrategy, usePortfolio, usePositions } from '@/api/hooks'
import { PLATFORM_FEE_RATE } from '@/api/demo'
import { KV, cx } from '@/components/ui'
import { ISliders } from '@/components/icons'
import { usd, bps, price } from '@/lib/format'
import type { Token } from '@/domain/types'
import { securityPass } from './tokenBits'

const BUY_PRESETS = [10, 100, 500, 1000]
const SELL_PRESETS = [25, 50, 100]

/** 买卖面板（照 fomo）。手动下单同样要经过风控，执行只走唯一入口。 */
export function TradePanel({ token, initialSide = 'BUY', bare }: { token: Token; initialSide?: 'BUY' | 'SELL'; bare?: boolean }) {
  const [side, setSide] = useState<'BUY' | 'SELL'>(initialSide)
  const [amount, setAmount] = useState('')
  const [slip, setSlip] = useState(300)
  const [settings, setSettings] = useState(false)
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
  const buy = side === 'BUY'
  const submit = () => {
    if (!usdAmt) return toast(buy ? '先输入金额' : '先选择卖出比例')
    toast('后端未接入：没有创建订单。接入后，这笔指令会先交给风控审批，批准后才由执行服务模拟成交。')
  }

  return (
    <form id={bare ? undefined : 'trade'} aria-label="买卖" className={bare ? '' : 'rounded-xl border border-line p-4'} onSubmit={(e) => { e.preventDefault(); submit() }}>
      <div className="flex items-center gap-2">
        <div role="tablist" aria-label="买卖方向" className="flex flex-1 gap-1 rounded-lg bg-panel p-1">
          {(['BUY', 'SELL'] as const).map((s) => (
            <button type="button" key={s} role="tab" aria-selected={side === s} onClick={() => { setSide(s); setAmount('') }}
              className={cx('h-9 flex-1 rounded-md text-[14px]', side === s ? (s === 'BUY' ? 'bg-up text-[#060510]' : 'bg-down text-[#060510]') : 'text-sub hover:text-fg')}>
              {s === 'BUY' ? '买入' : '卖出'}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setSettings((v) => !v)} aria-label="交易设置" aria-expanded={settings} className={cx('flex h-11 w-11 items-center justify-center rounded-lg border border-line', settings ? 'text-fg' : 'text-sub hover:text-fg')}>
          <ISliders width={18} height={18} />
        </button>
      </div>

      <label className="mt-4 block rounded-xl bg-panel px-4 py-5 text-center">
        <span className="flex items-center justify-center">
          <span className={cx('num text-[40px] font-medium leading-none tracking-[-2px]', usdAmt ? 'text-fg' : 'text-mute')}>{buy ? '$' : ''}</span>
          <input inputMode="decimal" value={amount} placeholder="0" onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))} aria-label="金额"
            style={{ width: `${Math.max(1, amount.length || 1)}ch` }}
            className="num bg-transparent text-[40px] font-medium leading-none tracking-[-2px] outline-none placeholder:text-mute" />
          <span className={cx('num text-[40px] font-medium leading-none tracking-[-2px]', usdAmt ? 'text-fg' : 'text-mute')}>{buy ? '' : '%'}</span>
        </span>
        <span className="num mt-2 block text-[12px] font-normal text-sub">
          {usdAmt > 0
            ? buy ? `≈ ${Math.floor(usdAmt / token.priceUsd).toLocaleString()} ${token.symbol}` : `≈ ${usd(((holding?.valueUsd ?? 0) * Math.min(usdAmt, 100)) / 100)}`
            : buy ? `输入金额 · 可用 ${usd(pf.data?.cashUsd ?? 0)}` : `卖出比例 · 持仓 ${holding ? usd(holding.valueUsd) : '无'}`}
        </span>
      </label>
      {buy && (
        <div className="mt-1 flex justify-between px-1 text-[12px] font-normal text-sub">
          <span>单笔上限 <span className="num text-fg">{usd(maxTrade)}</span></span>
          <button type="button" onClick={() => setAmount(String(Math.floor(Math.min(maxTrade, pf.data?.cashUsd ?? 0) * 100) / 100))} className="text-agent hover:underline">用到上限</button>
        </div>
      )}

      <div className="mt-3 grid grid-cols-4 gap-2">
        {(buy ? BUY_PRESETS : SELL_PRESETS).map((p) => (
          <button type="button" key={p} onClick={() => setAmount(String(p))}
            className={cx('num h-9 rounded-lg text-[14px]', amount === String(p) ? 'bg-raised text-fg' : 'bg-panel text-sub hover:text-fg')}>
            {buy ? `$${p}` : `${p}%`}
          </button>
        ))}
        {!buy && <button type="button" onClick={() => setAmount('100')} className="h-9 rounded-lg bg-panel text-[14px] text-sub hover:text-fg">全部</button>}
      </div>

      {settings && (
        <label className="mt-3 flex items-center justify-between text-[12px] text-sub">
          最大滑点
          <select value={slip} onChange={(e) => setSlip(Number(e.target.value))} className="rounded-md border border-line bg-panel px-2 py-1 text-fg">
            {[100, 200, 300].map((v) => <option key={v} value={v}>{bps(v)}</option>)}
          </select>
        </label>
      )}

      {usdAmt > 0 && issues.length > 0 && (
        <ul className="mt-3 space-y-1 text-[12px] font-normal text-down">
          {issues.map((i) => <li key={i}>风控预检：{i}</li>)}
        </ul>
      )}

      <button type="submit"
        className={cx('mt-4 h-11 w-full rounded-full text-[14px]', usdAmt > 0 ? (buy ? 'bg-up text-[#060510]' : 'bg-down text-[#060510]') : 'bg-raised text-sub')}>
        {buy ? '买入' : '卖出'} {token.symbol}
      </button>

      {usdAmt > 0 && (
        <div className="mt-3 text-[12px] font-normal">
          <KV k="参考价格" v={price(token.priceUsd)} className="py-1 text-[12px]" />
          <KV k="预计价格冲击（估算）" v={impactPct < 0.01 ? '< 0.01%' : `${impactPct.toFixed(2)}%`} className="py-1 text-[12px]" />
          <KV k="网络费 + 优先费" v={usd(network)} className="py-1 text-[12px]" />
          <KV k="平台手续费（含 AI 费用）" v={usd(platform)} className="py-1 text-[12px]" />
          <KV k="报价来源" v={<span className="text-warn">Jupiter 未接入</span>} className="py-1 text-[12px]" />
        </div>
      )}
      <p className="mt-3 text-center text-[12px] font-normal text-mute">模拟盘 · 手动下单和 Agent 下单走同一套风控 · 回车下单</p>
    </form>
  )
}
