import { useEffect, useState } from 'react'
import { Link, useParams, useLocation } from 'react-router-dom'
import { useCandles, useEvents, useHolders, useOrders, usePositions, useResearchSteps, useToken, useTrades, useTheses, useClosed, isDemo, now } from '@/api/hooks'
import { useApp } from '@/app/store'
import { useDrawerLink } from '@/app/links'
import { Button, DataState, Panel, Segmented, Tabs, TokenAvatar, Pct, Tag, KV, Chg, cx } from '@/components/ui'
import { ICopy, IStar, ICheck, IX, IAlert, ISearch } from '@/components/icons'
import { CandleChart, type Indicators } from '@/features/charts'
import { readTrades } from '@/wallet/history'
import { TokenChat } from '@/features/TokenChat'
import { Theses } from '@/features/Theses'
import { TradePanel } from '@/features/TradePanel'
import { DecisionChain } from '@/features/DecisionChain'
import { useResearch } from '@/features/Research'
import { AGENTS_ONLINE } from '@/api/live'
import { CHAIN_LABEL } from '@/api/chain'
import { VerdictTag, securityFlags, VERDICT, RiskStrip, QualityBadge, Masked, dataBad, qualityTitle } from '@/features/tokenBits'
import { usd, price, age, compactCN, shortAddr, relTime, pct, duration, clock } from '@/lib/format'
import { STATUS_LABEL, STATUS_TONE } from '@/domain/orderMachine'
import { ROLE_MAP } from '@/domain/agents'
import type { ChartMarker, ChartTf, Holder, MarkerKind, Token } from '@/domain/types'

type TabKey = 'holders' | 'trades' | 'research'

export function TokenPage() {
  const { address = '' } = useParams()
  const q = useToken(address)
  const loc = useLocation()
  useEffect(() => {
    if (loc.hash === '#trade') setTimeout(() => document.getElementById('trade')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300)
  }, [loc.hash, q.data])
  return (
    <DataState q={q} rows={6} empty={<span>没有找到代币 <span className="font-mono">{shortAddr(address)}</span>。<Link to="/" className="text-agent">返回发现</Link></span>}>
      {(t) => t && <TokenView t={t} />}
    </DataState>
  )
}


function TokenView({ t }: { t: Token }) {
  const [tab, setTab] = useState<TabKey>('holders')
  useEffect(() => { document.title = `${t.symbol} ${dataBad(t) ? "" : price(t.priceUsd) + " "}· KAGOUAI` }, [t])
  return (
    <div className="-mx-4 -mt-4 grid grid-cols-1 [&>*]:min-w-0 lg:grid-cols-[minmax(0,1fr)_380px] xl:grid-cols-[400px_minmax(0,1fr)_380px]">
      <SocialPanel t={t} />

      <div className="min-w-0 border-line lg:border-r">
        <Header t={t} />
        <ChartPanel t={t} />
        <section>
          <Tabs label="代币信息" value={tab} onChange={setTab} options={[
            { value: 'holders', label: <>持有者 <span className="text-sub">({t.holders === null ? '--' : compactCN(t.holders)})</span></> },
            { value: 'trades', label: '交易' },
            // 没有 Agent 后端时不显示空的「Agent 研究」
            ...(isDemo() || AGENTS_ONLINE ? [{ value: 'research' as const, label: 'Agent 研究' }] : []),
          ]} />
          {tab === 'holders' && <Holders t={t} />}
          {tab === 'trades' && <Trades t={t} />}
          {tab === 'research' && <><Research t={t} /><Activity t={t} /></>}
        </section>
      </div>

      <div className="space-y-4 p-4 pb-24 lg:pb-4">
        <div className="hidden lg:block"><TradePanel token={t} /></div>
        <About t={t} />
        <MyPosition t={t} />
        <SecurityBlock t={t} />
      </div>
      <MobileTradeBar t={t} />
    </div>
  )
}

/** 手机：底部固定买入 / 卖出（照 fomo App），点开是底部弹层。 */
function MobileTradeBar({ t }: { t: Token }) {
  const [side, setSide] = useState<'BUY' | 'SELL' | null>(null)
  useEffect(() => {
    if (!side) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSide(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [side])
  return (
    <>
      <div className="fixed inset-x-0 bottom-0 z-30 flex gap-2 border-t border-line bg-bg px-4 pb-[max(12px,env(safe-area-inset-bottom))] pt-3 lg:hidden">
        <button onClick={() => setSide('BUY')} className="h-12 flex-1 rounded-full bg-up text-[16px] text-[#060510]">买入</button>
        <button onClick={() => setSide('SELL')} className="h-12 flex-1 rounded-full bg-raised text-[16px]">卖出</button>
      </div>
      {side && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setSide(null)} />
          <div role="dialog" aria-modal="true" aria-label={`${side === 'BUY' ? '买入' : '卖出'} ${t.symbol}`} className="anim-sheet absolute inset-x-0 bottom-0 rounded-t-3xl border-t border-line bg-bg px-4 pb-[max(16px,env(safe-area-inset-bottom))] pt-2">
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" />
            <TradePanel token={t} initialSide={side} bare />
          </div>
        </div>
      )}
    </>
  )
}

function Header({ t }: { t: Token }) {
  const { watch, toggleWatch } = useApp()
  const [copied, setCopied] = useState(false)
  const w = watch[t.address] ?? t.watch
  const s = t.stats['24h']
  const m = (v: React.ReactNode) => <Masked t={t}>{v}</Masked>
  const boxes: [string, React.ReactNode][] = [
    ['价格', m(price(t.priceUsd))], ['24小时涨跌', m(<Chg key="c" v={s.change} />)], ['24小时交易量', usd(s.volume, { compact: true })], ['流动性', usd(t.liquidityUsd, { compact: true })],
    ...(t.fdvUsd != null && Math.abs(t.fdvUsd - t.mcapUsd) > t.mcapUsd * 0.01 ? [['完全稀释估值', m(usd(t.fdvUsd, { compact: true }))] as [string, React.ReactNode]] : []),
  ]
  return (
    <section className="border-b border-line px-4 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={40} />
        <h1 className="text-[20px] font-bold leading-6 tracking-[-0.4px]">{t.symbol}</h1>
        <div className="flex items-center gap-1 text-sub">
          <a href={`https://x.com/search?q=${encodeURIComponent('$' + t.symbol)}&f=live`} target="_blank" rel="noreferrer" aria-label="在 X 上搜索" className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-raised hover:text-fg"><ISearch /></a>
          <button onClick={() => toggleWatch(t.address)} aria-pressed={!!w} aria-label={w ? '取消自选' : '加入自选'} className={cx('flex h-7 w-7 items-center justify-center rounded-md hover:bg-raised', w ? 'text-paper' : 'hover:text-fg')}><IStar filled={!!w} /></button>
        </div>
        <span className="text-[14px] text-sub">{age(t.ageMinutes)}</span>
        <button className="flex items-center gap-1 rounded-md px-1 text-[14px] text-sub hover:bg-raised hover:text-fg" title={t.address}
          onClick={() => { navigator.clipboard?.writeText(t.address).catch(() => {}); setCopied(true); setTimeout(() => setCopied(false), 1500) }} aria-label="复制合约地址">
          {shortAddr(t.address, 4, 4)}{copied ? <ICheck className="text-up" /> : <ICopy />}
        </button>
        {(isDemo() || AGENTS_ONLINE) && <VerdictTag v={t.verdict} />}
        <QualityBadge t={t} />
      </div>
      {dataBad(t) && <p role="alert" className="mt-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[12px] text-warn">行情数据异常，价格、市值和涨跌暂不展示，也不能交易：{qualityTitle(t)}</p>}
      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div className="mr-4">
          <div className="text-[12px] font-normal text-sub">市值</div>
          <div className="num text-[32px] font-medium leading-9 tracking-[-1.5px]">{m(usd(t.mcapUsd, { compact: true }))}</div>
        </div>
        {boxes.map(([k, v]) => (
          <div key={k} className="rounded-lg border border-line px-3 py-2">
            <div className="text-[12px] font-normal text-sub">{k}</div>
            <div className="num text-[14px]">{v}</div>
          </div>
        ))}
      </div>
      {t.pair && <p className="mt-2 text-[12px] font-normal text-sub" title={`价格、K 线和成交都来自这个池子：${t.pair.address}`}>主交易对：{t.pair.dex} · {t.symbol}/{t.pair.quote || '?'} · <span className="num">{shortAddr(t.pair.address, 4, 4)}</span></p>}
      <div className="mt-3"><RiskStrip s={t.security} /></div>
    </section>
  )
}

const TF: [ChartTf, string][] = [['1s', '1秒'], ['5s', '5秒'], ['15s', '15秒'], ['1m', '1分'], ['5m', '5分'], ['15m', '15分'], ['1h', '1小时'], ['4h', '4小时'], ['1d', '1天']]
const OVERLAY: Record<'mine' | 'research', MarkerKind[]> = { mine: ['buy', 'sell'], research: ['discovered', 'no_trade', 'risk_rejected'] }

function ChartPanel({ t }: { t: Token }) {
  const [tf, setTf] = useState<ChartTf>('5m')
  const q = useCandles(t.address, tf)
  const [unit, setUnit] = useState<'price' | 'mcap'>('price')
  const [ov, setOv] = useState({ mine: true, research: true })
  const [ind, setInd] = useState<Indicators>({ ma20: false, ema50: false })
  const live = !isDemo()
  // 市值 = 价格 × 流通量，用当前市值和价格的比值换算整条 K 线
  const factor = unit === 'mcap' && t.priceUsd > 0 ? t.mcapUsd / t.priceUsd : 1
  const mine = live ? readTrades().filter((x) => x.chain === t.chain && x.address.toLowerCase() === t.address.toLowerCase()) : []
  const tfLabel = TF.find(([v]) => v === tf)?.[1] ?? tf
  return (
    <section className="border-b border-line" aria-label="K 线">
      <div className="scrollbar-none flex h-10 items-center gap-1 overflow-x-auto whitespace-nowrap border-b border-line px-2 text-[13px]">
        {(live ? TF : TF.filter(([v]) => v === '5m')).map(([v, l]) => (
          <button key={v} onClick={() => setTf(v)} aria-pressed={tf === v} className={cx('rounded-md px-2 py-1', tf === v ? 'text-fg' : 'text-sub hover:text-fg')}>{l}</button>
        ))}
        <span className="mx-1 h-4 w-px bg-line" />
        {([['ma20', 'MA20'], ['ema50', 'EMA50']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setInd({ ...ind, [k]: !ind[k] })} aria-pressed={ind[k]} className={cx('rounded-md px-2 py-1', ind[k] ? (k === 'ma20' ? 'text-paper' : 'text-agent') : 'text-sub hover:text-fg')}>{l}</button>
        ))}
        <span className="mx-1 h-4 w-px bg-line" />
        <button onClick={() => setUnit('price')} className={cx('rounded-md px-1 py-1', unit === 'price' ? 'text-fg' : 'text-sub hover:text-fg')}>价格</button>
        <span className="text-mute">/</span>
        <button onClick={() => setUnit('mcap')} className={cx('rounded-md px-1 py-1', unit === 'mcap' ? 'text-fg' : 'text-sub hover:text-fg')}>市值</button>
        {!live && <span className="ml-auto hidden pr-1 text-[12px] font-normal text-mute sm:inline">演示 K 线固定 5 分钟</span>}
        {live && tf.endsWith('s') && <span className="ml-auto hidden pr-1 text-[12px] font-normal text-mute sm:inline" title="免费数据只有最近 300 笔成交，页面开着会一直累积；数据源本身有约半分钟延迟">秒级 K 线由最近成交合成，延迟约半分钟</span>}
      </div>
      <DataState q={q} rows={1} isEmpty={(d) => d.candles.length === 0} empty="这个周期还没有 K 线数据。">
        {(d) => {
          const series = factor === 1 ? d.candles : d.candles.map((c) => ({ ...c, open: c.open * factor, high: c.high * factor, low: c.low * factor, close: c.close * factor }))
          const step = series.length > 1 ? series[1].time - series[0].time : 300
          // 自己的成交时间对到所在的那根 K 线上
          const myMarkers: ChartMarker[] = mine.map((x) => {
            const bar = series.reduce((best, c) => (c.time <= x.time / 1000 && c.time > best ? c.time : best), series[0]?.time ?? 0)
            return { time: bar, kind: (x.side === 'BUY' ? 'buy' : 'sell') as MarkerKind, label: x.side === 'BUY' ? '买' : '卖' }
          }).filter((m) => m.time >= (series[0]?.time ?? 0) && m.time <= (series[series.length - 1]?.time ?? 0) + step)
          const all = [...d.markers, ...myMarkers].filter((m) => (ov.mine || !OVERLAY.mine.includes(m.kind)) && (ov.research || !OVERLAY.research.includes(m.kind)))
          return (
            <>
              <div className="px-1 pt-1">
                <CandleChart label={`${t.symbol}/USD${unit === 'mcap' ? ' 市值' : ''} · ${tfLabel}`} candles={series} markers={all} indicators={ind} viewKey={`${t.address}:${tf}:${unit}`} />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-[12px] font-normal text-sub">
                <span>图表叠加层</span>
                <label className="flex cursor-pointer items-center gap-1.5 text-fg"><input type="checkbox" checked={ov.mine} onChange={(e) => setOv({ ...ov, mine: e.target.checked })} className="accent-[var(--c-agent)]" />{live ? '我的买卖' : '我的 Agent 买卖'}</label>
                {!live && <label className="flex cursor-pointer items-center gap-1.5 text-fg"><input type="checkbox" checked={ov.research} onChange={(e) => setOv({ ...ov, research: e.target.checked })} className="accent-[var(--c-agent)]" />Agent 研究</label>}
              </div>
            </>
          )
        }}
      </DataState>
    </section>
  )
}

/** 选中代币后左栏：群聊 / 观点，可以切换（替代 fomo 的代币列表）。 */
function SocialPanel({ t }: { t: Token }) {
  const [k, setK] = useState<'chat' | 'thesis'>('chat')
  const th = useTheses(t.address)
  return (
    <aside aria-label="群聊和观点" className="order-last flex flex-col border-t border-line lg:col-span-2 xl:order-none xl:col-span-1 xl:h-[calc(100vh-64px-33px-32px)] xl:border-r xl:border-t-0">
      <div role="tablist" aria-label="群聊或观点" className="flex gap-1 border-b border-line p-2">
        {([['chat', '群聊'], ['thesis', `观点 (${th.data?.length ?? 0})`]] as const).map(([v, l]) => (
          <button key={v} role="tab" aria-selected={k === v} onClick={() => setK(v)}
            className={cx('h-9 flex-1 rounded-lg text-[14px]', k === v ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>{l}</button>
        ))}
      </div>
      <div className={cx('min-h-0 flex-1', k === 'thesis' && 'overflow-y-auto')}>
        {k === 'chat' ? <TokenChat token={t} /> : <Theses token={t} />}
      </div>
    </aside>
  )
}

function Bar({ l, r, lv, rv }: { l: string; r: string; lv: number; rv: number }) {
  const p = lv + rv > 0 ? (lv / (lv + rv)) * 100 : 50
  return (
    <div>
      <div className="flex justify-between text-[12px]"><span className="num">{l}</span><span className="num">{r}</span></div>
      <div className="mt-1 flex h-1 gap-0.5 overflow-hidden rounded-full">
        <div className="rounded-full bg-up" style={{ width: `${p}%` }} /><div className="flex-1 rounded-full bg-down" />
      </div>
    </div>
  )
}

function About({ t }: { t: Token }) {
  const s = t.stats['24h']
  const bShare = s.buyers + s.sellers ? s.buyers / (s.buyers + s.sellers) : 0.5
  return (
    <section className="rounded-xl border border-line p-4" aria-label={`关于 ${t.symbol}`}>
      <h2 className="text-[16px]">关于 {t.symbol}</h2>
      <p className="mt-1 text-[14px] font-normal text-sub">{t.name} · {CHAIN_LABEL[t.chain]} · {t.chain === 'solana' ? <>{t.launchpad} 发射 · {t.graduated ? '已毕业' : t.bondingPct === null ? '未毕业' : `内盘进度 ${t.bondingPct}%`}</> : `主要交易对在 ${t.launchpad}`}</p>
      <div className="mt-3 grid grid-cols-4 gap-2">
        {([['5M', '5m'], ['1H', '1h'], ['6H', '6h'], ['1D', '24h']] as const).map(([l, k]) => (
          <div key={k} className="rounded-lg border border-line px-2 py-2 text-center">
            <div className="text-[12px] font-normal text-sub">{l}</div>
            <Chg v={t.stats[k].change} className="text-[13px]" />
          </div>
        ))}
      </div>
      <div className="mt-4 space-y-3">
        <Bar l={`${usd(s.volume * bShare, { compact: true })} 交易量`} r={`${usd(s.volume * (1 - bShare), { compact: true })} 交易量`} lv={bShare} rv={1 - bShare} />
        <Bar l={`${s.buyers} 笔买入`} r={`${s.sellers} 笔卖出`} lv={s.buyers} rv={s.sellers} />
      </div>
      {t.description && <p className="mt-4 line-clamp-4 whitespace-pre-line text-[13px] font-normal text-fg/80">{t.description}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        {(t.links ?? []).map((l) => (
          <a key={l.url} href={l.url} target="_blank" rel="noreferrer noopener" title={l.url} className="flex h-9 items-center rounded-lg bg-panel px-3 text-[14px] hover:bg-raised">{l.label}</a>
        ))}
        <a href={`https://x.com/search?q=${encodeURIComponent('$' + t.symbol)}&f=live`} target="_blank" rel="noreferrer" className="flex h-9 items-center gap-1.5 rounded-lg bg-panel px-3 text-[14px] hover:bg-raised"><ISearch />在 X 上搜 ${t.symbol}</a>
        <a href={`https://x.com/search?q=${encodeURIComponent(t.address)}&f=live`} target="_blank" rel="noreferrer" className="flex h-9 items-center gap-1.5 rounded-lg bg-panel px-3 text-[14px] hover:bg-raised"><ISearch />在 X 上搜合约地址</a>
      </div>
      {!isDemo() && !(t.links ?? []).length && <p className="mt-2 text-[12px] font-normal text-sub">项目方没有在 DexScreener 登记官网或社交账号。</p>}
      <p className="mt-3 text-[12px] font-normal text-mute">{isDemo() ? '买入 / 卖出笔数和交易量按买家占比估算（演示）' : '交易量按买卖笔数的比例估算，不是实际买卖金额。'}</p>
    </section>
  )
}

function MyPosition({ t }: { t: Token }) {
  const pos = usePositions()
  const closed = useClosed()
  const [k, setK] = useState<'open' | 'closed'>('open')
  const p = pos.data?.find((x) => x.token.address === t.address)
  const c = (closed.data ?? []).filter((x) => x.token.address === t.address)
  return (
    <section className="rounded-xl border border-line p-4" aria-label="你的持仓">
      <h2 className="text-[16px]">你的持仓</h2>
      <div className="mt-3 flex gap-4 text-[14px]">
        <button onClick={() => setK('open')} aria-pressed={k === 'open'} className={k === 'open' ? 'text-fg' : 'text-sub hover:text-fg'}>持仓中</button>
        <button onClick={() => setK('closed')} aria-pressed={k === 'closed'} className={k === 'closed' ? 'text-fg' : 'text-sub hover:text-fg'}>已平仓</button>
      </div>
      <div className="mt-3">
        {k === 'open' && (p ? (
          <>
            <div className="flex items-baseline justify-between"><span className="num text-[20px] tracking-[-0.4px]">{usd(p.valueUsd)}</span><Pct v={(p.unrealizedUsd / p.costBasisUsd) * 100} digits={1} /></div>
            <KV k="数量" v={p.amount.toLocaleString()} className="mt-1" />
            <KV k="成本" v={usd(p.costBasisUsd)} />
            <KV k="止盈 / 止损" v={<span><span className="text-up">+{p.takeProfit}%</span> / <span className="text-down">{p.stopLoss}%</span></span>} />
            <KV k="谁买的" v="阿狗一号（Agent）" />
          </>
        ) : <p className="py-4 text-center text-[14px] font-normal text-sub">还没有持仓</p>)}
        {k === 'closed' && (c.length ? c.map((x) => (
          <KV key={x.closedAt} k={`${relTime(x.closedAt, now())}平仓`} v={<span className={x.realizedUsd >= 0 ? 'text-up' : 'text-down'}>{usd(x.realizedUsd, { sign: true })}</span>} />
        )) : <p className="py-4 text-center text-[14px] font-normal text-sub">没有已平仓记录</p>)}
      </div>
    </section>
  )
}

function SecurityBlock({ t }: { t: Token }) {
  const s = t.security
  const flags = securityFlags(s)
  return (
    <Panel title="安全检查" right={<span className="text-xs text-sub">{s.checkedAt ? `检查于 ${relTime(s.checkedAt, now())}` : '尚未检查'}</span>}>
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
            <KV k="前十持有占比" v={s.top10Pct === null ? '未知' : pct(s.top10Pct, 1).replace('+', '')} />
            <KV k="开发者持有" v={s.devHoldPct === null ? '未知' : pct(s.devHoldPct, 1).replace('+', '')} />
          </div>
          <p className="mt-2 text-[11px] text-sub">{t.chain === 'solana' ? '「可卖出」只是近似结果：反向报价能否路由，不是真实卖出模拟。' : '安全检查来自 GoPlus：可卖出、税率、权限都是它的模拟和合约分析结果。'}{isDemo() ? 'RugCheck 未接入。' : ''}</p>
        </>
      )}
    </Panel>
  )
}

function Holders({ t }: { t: Token }) {
  const q = useHolders(t.address)
  const [agentOnly, setAgentOnly] = useState(false)
  const [thesisOnly, setThesisOnly] = useState(false)
  return (
    <DataState q={q} isEmpty={(d) => d.length === 0}>
      {(all) => {
        const rows = all.filter((h) => (!agentOnly || h.kind === 'agent') && (!thesisOnly || h.hasThesis))
        return (
          <div>
            <HolderBar all={all} top10={t.security.top10Pct} />
            {isDemo() && <div className="flex gap-4 px-4 py-2.5 text-xs text-sub">
              <label className="flex cursor-pointer items-center gap-1.5"><input type="checkbox" checked={agentOnly} onChange={(e) => setAgentOnly(e.target.checked)} className="accent-[var(--c-agent)]" />只看 Agent</label>
              <label className="flex cursor-pointer items-center gap-1.5"><input type="checkbox" checked={thesisOnly} onChange={(e) => setThesisOnly(e.target.checked)} className="accent-[var(--c-agent)]" />只看有观点的</label>
            </div>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-y border-line">
                  <th className="px-4 py-2 text-left font-normal">持有者</th>
                  <th className="px-2 py-2 text-right font-normal">持仓</th><th className="px-2 py-2 text-right font-normal">占比</th>
                  <th className="px-2 py-2 text-right font-normal">盈亏</th><th className="px-4 py-2 text-right font-normal">持有时长</th>
                </tr></thead>
                <tbody>
                  {rows.map((h) => (
                    <tr key={h.rank} className="border-b border-line last:border-0">
                      <td className="px-4 py-2.5">
                        {h.kind === 'agent'
                          ? <span className="flex items-center gap-1.5"><span className="num w-5 text-xs text-sub">{h.rank}</span><TokenAvatar symbol={h.label} hue={(h.rank * 47) % 360} size={22} /><span className="font-medium">{h.agentName}</span><span className="rounded bg-agent/15 px-1 text-[10px] text-agent">AI</span><span className="text-xs text-sub">{h.ownerName}</span>{h.hasThesis && <span className="text-[11px] text-sub">· 有观点</span>}</span>
                          : <span className="flex items-center gap-1.5"><span className="num w-5 text-xs text-sub">{h.rank}</span><TokenAvatar symbol={h.label} hue={(h.rank * 47) % 360} size={22} /><span className="num text-[13px] text-sub">{shortAddr(h.label, 4, 4)}</span></span>}
                      </td>
                      <td className="num px-2 py-2.5 text-right">{usd(h.amountUsd)}</td>
                      <td className="num px-2 py-2.5 text-right">{h.pct.toFixed(2)}%</td>
                      <td className="px-2 py-2.5 text-right"><Pct v={h.pnlPct} digits={1} /></td>
                      <td className="num px-4 py-2.5 text-right text-sub">{h.heldMinutes === null ? '--' : duration(h.heldMinutes * 60000)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      }}
    </DataState>
  )
}

/** 持仓分布条：前十名各占多少，Agent 用强调色，一眼看出筹码是否集中。 */
function HolderBar({ all, top10 }: { all: Holder[]; top10: number | null }) {
  const top = all.slice(0, 10)
  return (
    <div className="px-4 pt-3">
      <div className="flex justify-between text-[12px] font-normal text-sub">
        <span>前十持有 <span className="num text-fg">{top10 === null ? '未知' : `${top10.toFixed(1)}%`}</span></span>
        <span className="flex items-center gap-3"><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-agent" />Agent</span><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-sub/60" />钱包</span></span>
      </div>
      <div className="mt-1.5 flex h-2 gap-px overflow-hidden rounded-full bg-raised" role="img" aria-label={`前十持有 ${top10 === null ? '未知' : `${top10.toFixed(1)}%`}`}>
        {top.map((h) => <div key={h.rank} title={`#${h.rank} ${h.agentName ?? shortAddr(h.label)} ${h.pct.toFixed(2)}%`} className={h.kind === 'agent' ? 'bg-agent' : 'bg-sub/60'} style={{ width: `${h.pct}%` }} />)}
      </div>
    </div>
  )
}

function Trades({ t }: { t: Token }) {
  const q = useTrades(t.address)
  const [side, setSide] = useState<'all' | 'BUY' | 'SELL'>('all')
  const [agentOnly, setAgentOnly] = useState(false)
  return (
    <DataState q={q} isEmpty={(d) => d.length === 0} empty="还没有成交。">
      {(all) => {
        const rows = all.filter((r) => (side === 'all' || r.side === side) && (!agentOnly || r.kind === 'agent'))
        return (
        <div className="overflow-x-auto">
          <div className="flex items-center gap-3 px-4 py-2.5">
            <Segmented label="成交方向" size="sm" value={side} onChange={setSide} options={[{ value: 'all', label: '全部' }, { value: 'BUY', label: '买入' }, { value: 'SELL', label: '卖出' }]} />
            {isDemo() && <label className="flex cursor-pointer items-center gap-1.5 text-xs text-sub"><input type="checkbox" checked={agentOnly} onChange={(e) => setAgentOnly(e.target.checked)} />只看 Agent</label>}
            <span className="ml-auto text-[12px] font-normal text-sub">{rows.length} 笔</span>
          </div>
          <table className="w-full min-w-[560px] text-[13px]">
            <thead className="text-xs text-sub"><tr className="border-b border-line">
              <th className="px-4 py-2 text-left font-normal">时间</th><th className="px-2 py-2 text-left font-normal">方向</th>
              <th className="px-2 py-2 text-left font-normal">交易者</th><th className="px-2 py-2 text-right font-normal">金额</th><th className="px-4 py-2 text-right font-normal">价格</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-line last:border-0">
                  <td className="num px-4 py-2 text-xs text-sub">{relTime(r.at, now())}</td>
                  <td className={cx('px-2 py-2', r.side === 'BUY' ? 'text-up' : 'text-down')}>{r.side === 'BUY' ? '买' : '卖'}</td>
                  <td className="px-2 py-2"><span className="flex items-center gap-1.5"><TokenAvatar symbol={r.who} hue={r.who.length * 37 % 360} size={20} />{r.kind === 'agent' ? <>{r.who} <span className="rounded bg-agent/15 px-1 text-[10px] text-agent">AI</span> <span className="text-xs text-sub">{r.owner}</span></> : <span className="num text-[13px] text-sub">{shortAddr(r.who, 4, 4)}</span>}</span></td>
                  <td className={cx('num px-2 py-2 text-right', r.side === 'BUY' ? 'text-up' : 'text-down')}>{usd(r.usd)}</td>
                  <td className="num px-4 py-2 text-right">{price(r.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )
      }}
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
  const steps = useResearchSteps(t.address)
  const research = useResearch()
  const o = (orders.data ?? []).find((x) => x.token.address === t.address)
  const again = AGENTS_ONLINE && t.verdict !== 'researching' && <Button variant="outline" className="h-9 px-3 py-0 text-[14px]" onClick={() => research(t)}>{t.verdict === 'unresearched' ? '交给 Agent 研究' : '重新研究'}</Button>
  if (t.verdict === 'unresearched') return <div className="space-y-3 p-6 text-center text-[13px] text-sub"><p>还没有研究。{isDemo() ? '点发现页的「交给 Agent」开始研究（后端未接入）。' : AGENTS_ONLINE ? '' : 'Agent 分析还没上线。'}</p>{again}</div>
  const live = steps.data ?? []
  if (t.verdict === 'researching' && live.length === 0) return <p className="p-6 text-center text-[13px] text-sub">研究中：链上、安全、情绪、量化并行分析，完成后主 Agent 给出结论。</p>
  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">主 Agent 结论：<VerdictTag v={t.verdict} /><span className="text-sub">{VERDICT[t.verdict].label === '不交易' ? '数据不足或结论冲突时输出 NO_TRADE' : ''}</span><span className="ml-auto">{again}</span></div>
      {live.length > 0 ? <DecisionChain steps={live} /> : o ? <DecisionChain steps={o.decisionChain} /> : <p className="text-[13px] text-sub">{t.verdict === 'security_veto' ? '安全检查一票否决，没有进入策略阶段。' : '没有生成交易方案。'}</p>}
    </div>
  )
}
