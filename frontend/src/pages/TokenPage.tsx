import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useLocation } from 'react-router-dom'
import { useCandles, useEvents, useHolders, useOrders, usePositions, useToken, useTokens, now } from '@/api/hooks'
import { useApp, useToast } from '@/app/store'
import { useDrawerLink } from '@/app/links'
import { DataState, Panel, Tabs, TokenAvatar, Pct, Tag, KV, DemoTag, Segmented, cx } from '@/components/ui'
import { ICopy, IStar, ICheck, IX, IAlert } from '@/components/icons'
import { CandleChart } from '@/features/charts'
import { TokenChat } from '@/features/TokenChat'
import { TradePanel } from '@/features/TradePanel'
import { DecisionChain } from '@/features/DecisionChain'
import { VerdictTag, securityFlags, VERDICT } from '@/features/tokenBits'
import { usd, price, age, compactCN, shortAddr, relTime, pct, duration, clock } from '@/lib/format'
import { STATUS_LABEL, STATUS_TONE } from '@/domain/orderMachine'
import { ROLE_MAP } from '@/domain/agents'
import type { Token } from '@/domain/types'

type TabKey = 'chat' | 'holders' | 'activity' | 'research'

export function TokenPage() {
  const { address = '' } = useParams()
  const q = useToken(address)
  const loc = useLocation()
  useEffect(() => {
    if (loc.hash === '#trade') setTimeout(() => document.getElementById('trade')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300)
  }, [loc.hash, q.data])
  return (
    <DataState q={q} rows={6} empty={<span>没有找到代币 <span className="font-mono">{shortAddr(address)}</span>。<Link to="/discover" className="text-agent">返回发现</Link></span>}>
      {(t) => t && <TokenView t={t} />}
    </DataState>
  )
}

function TokenView({ t }: { t: Token }) {
  const [tab, setTab] = useState<TabKey>('chat')
  const { watch, toggleWatch } = useApp()
  const toast = useToast((s) => s.show)
  const w = watch[t.address] ?? t.watch
  return (
    <div className="grid grid-cols-1 gap-4 [&>*]:min-w-0 xl:grid-cols-[240px_minmax(0,1fr)_340px] lg:grid-cols-[minmax(0,1fr)_340px]">
      <SideList current={t.address} />

      <div className="min-w-0 space-y-4">
        <section className="rounded-card border border-line bg-panel p-4">
          <div className="flex flex-wrap items-center gap-3">
            <TokenAvatar symbol={t.symbol} hue={t.hue} size={44} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl font-semibold">{t.symbol}</h1>
                <span className="text-sm text-sub">{t.name}</span>
                <VerdictTag v={t.verdict} />
                <DemoTag />
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-sub">
                <span>Solana</span>
                <button className="flex items-center gap-1 font-mono hover:text-fg" onClick={() => { navigator.clipboard?.writeText(t.address).catch(() => {}); toast('合约地址已复制') }} aria-label="复制合约地址">
                  {shortAddr(t.address, 6, 6)}<ICopy />
                </button>
                <span>· 上线 {age(t.ageMinutes)}</span>
                <span>· {t.dataSource} · {relTime(t.updatedAt, now())}</span>
              </div>
            </div>
            <button onClick={() => toggleWatch(t.address)} aria-pressed={!!w} aria-label={w ? '取消自选' : '加入自选'} className={cx('rounded-lg border border-line p-2', w ? 'text-paper' : 'text-sub hover:text-fg')}><IStar filled={!!w} /></button>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-4 sm:grid-cols-6">
            {[
              ['价格', price(t.priceUsd)], ['24h 涨跌', <Pct key="p" v={t.stats['24h'].change} />], ['市值', usd(t.mcapUsd)],
              ['流动性', usd(t.liquidityUsd)], ['24h 成交量', usd(t.stats['24h'].volume)], ['持有者', compactCN(t.holders)],
            ].map(([k, v]) => (
              <div key={k as string} className="min-w-0"><div className="text-xs text-sub">{k}</div><div className="num mt-0.5 truncate text-[15px] font-semibold">{v}</div></div>
            ))}
          </div>
        </section>

        <ChartPanel t={t} />

        <section className="rounded-card border border-line bg-panel">
          <Tabs label="代币信息" value={tab} onChange={setTab} options={[
            { value: 'chat', label: '代币群聊' }, { value: 'holders', label: '持有者' }, { value: 'activity', label: 'Agent 活动' }, { value: 'research', label: '研究与决策' },
          ]} />
          {tab === 'chat' && <TokenChat token={t} />}
          {tab === 'holders' && <Holders t={t} />}
          {tab === 'activity' && <Activity t={t} />}
          {tab === 'research' && <Research t={t} />}
        </section>
      </div>

      <div className="space-y-4">
        <TradePanel token={t} />
        <SecurityBlock t={t} />
        <BuySell t={t} />
      </div>
    </div>
  )
}

function ChartPanel({ t }: { t: Token }) {
  const q = useCandles(t.address)
  const [tf, setTf] = useState('5m')
  return (
    <Panel demo title="K 线" bodyClass="p-2" right={
      <Segmented label="K 线周期" size="sm" value={tf} onChange={setTf} options={['1m', '5m', '15m', '1h'].map((v) => ({ value: v, label: v }))} />
    }>
      <DataState q={q} rows={1}>
        {(d) => (
          <>
            <CandleChart candles={d.candles} markers={d.markers} />
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 pb-1 pt-2 text-[11px] text-sub">
              <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-agent" />Scout 发现</span>
              <span className="flex items-center gap-1"><span className="h-2 w-2 bg-sub" />NO_TRADE</span>
              <span className="flex items-center gap-1 text-up">▲ 主 Agent 买入</span>
              <span className="flex items-center gap-1 text-down">▼ 卖出 / 退出评估</span>
              <span className="ml-auto">演示 K 线固定为 5m，周期切换在接入行情后生效</span>
            </div>
          </>
        )}
      </DataState>
    </Panel>
  )
}

function SideList({ current }: { current: string }) {
  const tokens = useTokens()
  const pos = usePositions()
  const nav = useNavigate()
  const { watch } = useApp()
  const [k, setK] = useState<'hot' | 'held' | 'watch'>('held')
  const list = (tokens.data ?? []).filter((t) => k === 'hot' ? true : k === 'held' ? pos.data?.some((p) => p.token.address === t.address) : (watch[t.address] ?? t.watch)).slice(0, 14)
  return (
    <aside className="hidden rounded-card border border-line bg-panel xl:block" aria-label="代币列表">
      <div className="border-b border-line p-2">
        <Segmented label="列表" size="sm" value={k} onChange={setK} options={[{ value: 'held', label: '持仓' }, { value: 'watch', label: '自选' }, { value: 'hot', label: '热门' }]} />
      </div>
      <ul className="max-h-[calc(100vh-180px)] overflow-y-auto py-1">
        {list.length === 0 && <li className="p-4 text-center text-xs text-sub">空</li>}
        {list.map((t) => (
          <li key={t.address}>
            <button onClick={() => nav(`/token/${t.address}`)} className={cx('flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-raised', t.address === current && 'bg-raised')}>
              <TokenAvatar symbol={t.symbol} hue={t.hue} size={24} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{t.symbol}</span>
                <span className="num block text-[11px] text-sub">{usd(t.mcapUsd)}</span>
              </span>
              <Pct v={t.stats['1h'].change} className="text-xs" />
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}

function SecurityBlock({ t }: { t: Token }) {
  const s = t.security
  const flags = securityFlags(s)
  return (
    <Panel demo title="安全检查" right={<span className="text-xs text-sub">{s.checkedAt ? `检查于 ${relTime(s.checkedAt, now())}` : '尚未检查'}</span>}>
      {s.checkedAt == null ? (
        <p className="flex items-start gap-2 text-[13px] text-warn"><IAlert className="mt-0.5 shrink-0" />安全检查还没完成。结果无法确认时，风控默认拒绝买入。</p>
      ) : (
        <>
          <ul className="grid grid-cols-1 gap-1.5">
            {flags.map((f) => (
              <li key={f.label} className={cx('flex items-center gap-2 text-[13px]', f.ok === false ? 'text-down' : f.ok === null ? 'text-warn' : 'text-fg/90')}>
                <span className={f.ok === false ? 'text-down' : f.ok === null ? 'text-warn' : 'text-up'}>{f.ok === false ? <IX /> : f.ok === null ? <IAlert /> : <ICheck />}</span>{f.label}
              </li>
            ))}
          </ul>
          <div className="mt-3 border-t border-line pt-2">
            <KV k="前十持有占比" v={pct(s.top10Pct, 1).replace('+', '')} />
            <KV k="开发者持有" v={pct(s.devHoldPct, 1).replace('+', '')} />
          </div>
          <p className="mt-2 text-[11px] text-sub">模拟盘的「可卖出」是近似结果：反向报价能否路由 + Token-2022 扩展检查，不是真实卖出模拟。RugCheck 未接入。</p>
        </>
      )}
    </Panel>
  )
}

function BuySell({ t }: { t: Token }) {
  const s = t.stats['24h']
  const buyPct = (s.buyers / (s.buyers + s.sellers)) * 100
  return (
    <Panel demo title="24h 买卖力量">
      <div className="flex justify-between text-xs"><span className="text-up">买家 {s.buyers}</span><span className="text-down">卖家 {s.sellers}</span></div>
      <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full">
        <div className="bg-up" style={{ width: `${buyPct}%` }} /><div className="flex-1 bg-down" />
      </div>
      <KV k="成交笔数" v={s.txns.toLocaleString()} className="mt-2" />
    </Panel>
  )
}

function Holders({ t }: { t: Token }) {
  const q = useHolders(t.address)
  return (
    <DataState q={q} isEmpty={(d) => d.length === 0}>
      {(rows) => (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-[13px]">
            <thead className="text-xs text-sub"><tr className="border-b border-line">
              <th className="px-4 py-2 text-left font-normal">#</th><th className="px-2 py-2 text-left font-normal">持有者</th>
              <th className="px-2 py-2 text-right font-normal">持仓金额</th><th className="px-2 py-2 text-right font-normal">占比</th>
              <th className="px-2 py-2 text-right font-normal">盈亏</th><th className="px-4 py-2 text-right font-normal">持有时长</th>
            </tr></thead>
            <tbody>
              {rows.map((h) => (
                <tr key={h.rank} className="border-b border-line last:border-0">
                  <td className="num px-4 py-2.5 text-sub">{h.rank}</td>
                  <td className="px-2 py-2.5">
                    {h.kind === 'agent' ? <span className="flex items-center gap-1.5"><span className="font-medium">{h.agentName}</span><span className="text-xs text-sub">{h.ownerName} 的主 Agent</span></span> : <span className="font-mono text-xs text-sub">{shortAddr(h.label, 5, 5)}</span>}
                  </td>
                  <td className="num px-2 py-2.5 text-right">{usd(h.amountUsd)}</td>
                  <td className="num px-2 py-2.5 text-right">{h.pct.toFixed(2)}%</td>
                  <td className="px-2 py-2.5 text-right"><Pct v={h.pnlPct} digits={1} /></td>
                  <td className="num px-4 py-2.5 text-right text-sub">{duration(h.heldMinutes * 60000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 py-2 text-[11px] text-sub">主 Agent 买入后公开持仓金额。演示数据中的主 Agent 和钱包均为虚构。</p>
        </div>
      )}
    </DataState>
  )
}

function Activity({ t }: { t: Token }) {
  const ev = useEvents()
  const orders = useOrders()
  const open = useDrawerLink()
  const list = (ev.data ?? []).filter((e) => e.token?.address === t.address)
  const ords = (orders.data ?? []).filter((o) => o.token.address === t.address)
  return (
    <div className="p-4">
      {ords.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {ords.map((o) => (
            <button key={o.id} onClick={() => open('order', o.id)} className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-xs hover:bg-raised">
              <span className={o.side === 'BUY' ? 'text-up' : 'text-down'}>{o.side === 'BUY' ? '买入' : '卖出'}</span>
              <span className="num">{usd(o.requestedUsd)}</span>
              <Tag tone={STATUS_TONE[o.status]}>{STATUS_LABEL[o.status]}</Tag>
            </button>
          ))}
        </div>
      )}
      {list.length === 0 ? <p className="text-center text-[13px] text-sub">Agent 还没有处理过这个代币。</p> : (
        <ul className="space-y-2.5">
          {list.map((e) => (
            <li key={e.id} className="flex gap-3 text-[13px]">
              <span className="num w-[108px] shrink-0 text-xs text-sub">{clock(e.at)}</span>
              <span className="w-10 shrink-0 text-xs text-agent">{ROLE_MAP[e.agent].short}</span>
              <span className={e.status === 'error' ? 'text-down' : e.status === 'warn' ? 'text-warn' : 'text-fg/90'}>{e.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Research({ t }: { t: Token }) {
  const orders = useOrders()
  const o = (orders.data ?? []).find((x) => x.token.address === t.address)
  if (t.verdict === 'unresearched') return <p className="p-6 text-center text-[13px] text-sub">还没有研究。点发现页的「交给 Agent」开始研究（后端未接入）。</p>
  if (t.verdict === 'researching') return <p className="p-6 text-center text-[13px] text-sub">研究中：链上、安全、情绪、量化并行分析，完成后主 Agent 给出结论。</p>
  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center gap-2 text-[13px]">主 Agent 结论：<VerdictTag v={t.verdict} /><span className="text-sub">{VERDICT[t.verdict].label === '不交易' ? '数据不足或结论冲突时输出 NO_TRADE' : ''}</span></div>
      {o ? <DecisionChain steps={o.decisionChain} /> : <p className="text-[13px] text-sub">{t.verdict === 'security_veto' ? '安全检查一票否决，没有进入策略阶段。' : '没有生成交易方案。'}</p>}
    </div>
  )
}
