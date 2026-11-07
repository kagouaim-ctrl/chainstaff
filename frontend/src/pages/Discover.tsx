import { useWatched } from '@/features/watch'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTokens, isDemo, now } from '@/api/hooks'
import { useApp, useToast } from '@/app/store'
import { useResearch } from '@/features/Research'
import { CHAIN_LABEL } from '@/api/chain'
import { DataState, Segmented, TokenAvatar, Chg, cx } from '@/components/ui'
import { IStar, IBot } from '@/components/icons'
import { VerdictTag, SecurityBadge, riskLevel, QualityBadge, Masked, dataBad, rugLike, ChangeCell } from '@/features/tokenBits'
import { QuickBuyButton } from '@/features/QuickBuy'
import { COLS, ALL_COLS, useColumns, visibleCols, type ColKey } from '@/features/columns'
import { AGENTS_ONLINE } from '@/api/live'
import { usd, price, age, relTime } from '@/lib/format'
import type { Timeframe, Token, Chain } from '@/domain/types'

export type TabKey = 'trending' | 'blue' | 'held' | 'graduated' | 'bonding' | 'agent' | 'watch'
export type SortKey = ColKey | 'bonding'

/** 标签参考 fomo Discover（热门、蓝筹币、最多人持有、已毕业、内盘、自选），另加「Agent 在买」。 */
export const TABS: { value: TabKey; label: string }[] = [
  { value: 'trending', label: '热门' },
  { value: 'agent', label: 'Agent 在买' },
  { value: 'blue', label: '蓝筹币' },
  { value: 'held', label: '最多人持有' },
  { value: 'graduated', label: '已毕业' },
  { value: 'bonding', label: '内盘' },
  { value: 'watch', label: '自选' },
]

const DEFAULT_SORT: Record<TabKey, SortKey> = { trending: 'volume', agent: 'volume', blue: 'mcap', held: 'holders', graduated: 'age', bonding: 'bonding', watch: 'mcap' }

export interface Filter { tab: TabKey; tf: Timeframe; safeOnly: boolean; minMcap: number; launchpad: string; chain?: string; watch: Record<string, boolean>; sort: SortKey; dir: 1 | -1 }

export function filterTokens(list: Token[], o: Filter) {
  let r = list.slice()
  if (o.tab === 'blue') r = r.filter((t) => t.verified)
  if (o.tab === 'graduated') r = r.filter((t) => t.graduated)
  if (o.tab === 'bonding') r = r.filter((t) => !t.graduated)
  if (o.tab === 'agent') r = r.filter((t) => t.verdict === 'bought' || t.verdict === 'buy_signal')
  if (o.tab === 'watch') r = r.filter((t) => o.watch[t.address] ?? t.watch)
  // 「排除高危」：去掉有高危项的代币；还没检查的保留，界面上标为未检查
  if (o.safeOnly) r = r.filter((t) => riskLevel(t.security) !== 'high')
  if (o.minMcap) r = r.filter((t) => t.mcapUsd >= o.minMcap)
  if (o.launchpad !== 'all') r = r.filter((t) => t.launchpad === o.launchpad)
  if (o.chain && o.chain !== 'all') r = r.filter((t) => t.chain === o.chain)
  // 热门里默认不出现疑似已 Rug 的代币（跌 90% 以上且流动性不足 $1 万）
  if (o.tab === 'trending') r = r.filter((t) => !rugLike(t))
  const key = (t: Token): number | null => o.sort === 'bonding' ? t.bondingPct : COLS[o.sort].value(t, o.tf)
  // 数据异常的代币数值不可信，不参与排序，统一放到最后；没有数据的也放后面
  r.sort((a, b) => {
    const bad = Number(dataBad(a)) - Number(dataBad(b))
    if (bad) return bad
    const x = key(a), y = key(b)
    if (x == null || y == null) return Number(x == null) - Number(y == null)
    return (y - x) * o.dir
  })
  return r
}

export function Discover() {
  const q = useTokens()
  const [split, setSplit] = useState(false)
  return (
    <div className="space-y-4">
      <WatchStrip />
      <div className="hidden justify-end 2xl:flex">
        <button onClick={() => setSplit((v) => !v)} aria-pressed={split} className="rounded-lg border border-line px-2.5 py-1 text-[12px] text-sub hover:text-fg">{split ? '单表' : '分屏'}</button>
      </div>
      <div className={cx('grid gap-4', split && '2xl:grid-cols-2')}>
        <TokenTable initial="trending" compact={split} />
        {split && <div className="hidden 2xl:block"><TokenTable initial={AGENTS_ONLINE ? 'agent' : 'graduated'} compact /></div>}
      </div>
      {/* 数据口径放在表格下面，不占首屏 */}
      <p className="text-[12px] font-normal text-sub">数据源：{isDemo() ? 'DexScreener（未接入，显示演示数据）' : 'DexScreener 和 GeckoTerminal，按付费推广、最新资料和热门池子排序，不代表推荐。价格是主交易对的参考价，不是可成交报价；同名代币可能是仿盘，请核对合约地址。'}{q.data?.[0] && ` 更新于 ${relTime(q.data[0].updatedAt, now())}。`}</p>
    </div>
  )
}

/** 自选横条（参考 fomo）。 */
function WatchStrip() {
  const q = useTokens()
  const nav = useNavigate()
  const list = useWatched(q.data)
  // 实时模式没有自选时不占位置；演示模式保留引导文案
  if (!q.data || (!isDemo() && list.length === 0)) return null
  return (
    <section aria-label="自选" className="scrollbar-none -mx-4 flex gap-2 overflow-x-auto px-4">
      {list.length === 0 && <p className="text-xs text-sub">还没有自选。点代币左边的星标加入。</p>}
      {list.map((t) => (
        <button key={t.address} onClick={() => nav(`/token/${t.address}`)} className="flex min-w-[200px] shrink-0 items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left hover:bg-raised">
          <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={30} />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1 text-[14px]">{t.symbol}<IStar filled className="text-paper" width={11} height={11} /></span>
            <span className="num block whitespace-nowrap text-[12px] font-normal text-sub">市值 <Masked t={t}>{usd(t.mcapUsd, { compact: true })}</Masked></span>
          </span>
          <Masked t={t}><Chg v={t.stats['24h'].change} className="text-[12px]" /></Masked>
        </button>
      ))}
    </section>
  )
}

function TokenTable({ initial, compact }: { initial: TabKey; compact?: boolean }) {
  const q = useTokens()
  const nav = useNavigate()
  const toast = useToast((s) => s.show)
  const { watch, toggleWatch } = useApp()
  const [tab, setTab] = useState<TabKey>(initial)
  const [tf, setTf] = useState<Timeframe>('24h')
  const [safeOnly, setSafeOnly] = useState(false)
  const [minMcap, setMinMcap] = useState(0)
  const [launchpad, setLaunchpad] = useState('all')
  const [chain, setChain] = useState('all')
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT[initial])
  const [dir, setDir] = useState<1 | -1>(1)
  const watched = useWatched(q.data)
  // 自选页签用合并后的自选列表（包括不在热门列表里的代币）
  const base = tab === 'watch' ? watched : q.data ?? []
  const rows = filterTokens(base, { tab, tf, safeOnly, minMcap, launchpad, chain, watch, sort, dir })
  const demo = isDemo()
  const demoBuy = (t: Token) => toast(`演示数据不能交易：${t.symbol} 是样例代币。`)
  // 实时模式只显示有数据的分类：没有 Agent 后端就没有「Agent 在买」，没有认证数据就没有「蓝筹币」
  const tabs = demo ? TABS : TABS.filter((x) => x.value === 'trending' || x.value === 'watch' || (x.value === 'agent' ? AGENTS_ONLINE : filterTokens(q.data ?? [], { tab: x.value, tf, safeOnly: false, minMcap: 0, launchpad: 'all', chain: 'all', watch, sort: 'mcap', dir: 1 }).length > 0))
  const showAgent = demo || AGENTS_ONLINE
  const research = useResearch()
  const switchTab = (v: TabKey) => { setTab(v); setSort(DEFAULT_SORT[v]); setDir(1) }
  const th = (k: SortKey, label: string, cls = '') => (
    <th className={cx('px-3 py-3 text-right font-normal', cls)} aria-sort={sort === k ? (dir === 1 ? 'descending' : 'ascending') : 'none'}>
      <button className={cx('inline-flex items-center gap-1 hover:text-fg', sort === k && 'text-fg')} onClick={() => { if (sort === k) setDir((d) => (d === 1 ? -1 : 1)); else { setSort(k); setDir(1) } }}>
        {label}<span className="text-[10px]">{sort === k ? (dir === 1 ? '↓' : '↑') : '↕'}</span>
      </button>
    </th>
  )
  const sel = 'h-9 rounded-lg border border-line bg-bg px-2 text-[14px] text-fg'
  const { cols: chosen, dense } = useColumns()
  // 分屏时每张表只放最关键的几列
  const cols = visibleCols(compact ? chosen.filter((k) => ['price', 'mcap', 'change', 'txns'].includes(k)) : chosen, rows, tf)

  return (
    <section className="min-w-0">
      <div role="tablist" aria-label="代币分类" className="scrollbar-none flex gap-2 overflow-x-auto">
        {tabs.map((t) => (
          <button key={t.value} role="tab" aria-selected={tab === t.value} onClick={() => switchTab(t.value)}
            className={cx('h-9 shrink-0 whitespace-nowrap rounded-lg border px-3 text-[14px]', tab === t.value ? 'border-transparent bg-raised text-fg' : 'border-line text-sub hover:text-fg')}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Segmented label="时间段" size="sm" value={tf} onChange={setTf} options={(['5m', '1h', '6h', '24h'] as Timeframe[]).map((v) => ({ value: v, label: v }))} />
        <select aria-label="市值" className={sel} value={minMcap} onChange={(e) => setMinMcap(Number(e.target.value))}>
          <option value={0}>市值：全部</option><option value={1e6}>≥ $100万</option><option value={1e7}>≥ $1000万</option><option value={1e8}>≥ $1亿</option>
        </select>
        <select aria-label="发射平台" className={sel} value={launchpad} onChange={(e) => setLaunchpad(e.target.value)}>
          <option value="all">平台：全部</option><option>pump.fun</option><option>bonk.fun</option><option>Raydium</option>
        </select>
        {isDemo()
          ? <span className="flex h-9 items-center rounded-lg border border-line px-3 text-[14px] text-sub" title="只支持 Solana，其他链未适配">链：Solana</span>
          : <select aria-label="链" className={sel} value={chain} onChange={(e) => setChain(e.target.value)}>
              <option value="all">链：全部</option>{(Object.keys(CHAIN_LABEL) as Chain[]).map((c) => <option key={c} value={c}>{CHAIN_LABEL[c]}</option>)}
            </select>}
        <ColumnMenu />
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[14px] text-sub">
          <input type="checkbox" checked={safeOnly} onChange={(e) => setSafeOnly(e.target.checked)} className="accent-[var(--c-agent)]" />排除高危
        </label>
      </div>
      {tab === 'bonding' && <p className="mb-3 rounded-lg bg-panel px-3 py-2 text-[12px] font-normal text-sub">内盘代币还在发射曲线上，没有 LP，Agent 按风控规则不会交易，只能手动关注。</p>}

      <DataState q={q} rows={8} isEmpty={() => rows.length === 0} empty={tab === 'watch' ? '还没有自选代币。点代币左侧的星标加入自选。' : '当前筛选条件下没有代币。'}>
        {() => (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[900px] text-[13px] font-medium">
                <thead className="sticky top-0 z-10 bg-bg text-[12px] text-sub">
                  <tr className="h-10 border-y border-line">
                    <th className="w-8 pl-3"><span className="sr-only">自选</span></th>
                    <th className="px-2 text-left font-normal">代币</th>
                    <th className="w-[84px] px-1"><span className="sr-only">快速买入</span></th>
                    {tab === 'bonding' && th('bonding', '内盘进度')}
                    {cols.map((c) => th(c.key, c.label(tf)))}
                    {showAgent && <th className="px-3 text-left font-normal">Agent</th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const w = watch[t.address] ?? t.watch
                    return (
                      <tr key={t.address} className={cx('group cursor-pointer border-b border-line transition-colors last:border-0 hover:bg-raised', dense ? 'h-9' : 'h-11', rugLike(t) && 'opacity-60')} onClick={() => nav(`/token/${t.address}`)}>
                        <td className="pl-3" onClick={(e) => e.stopPropagation()}>
                          <button aria-label={w ? `取消自选 ${t.symbol}` : `自选 ${t.symbol}`} aria-pressed={!!w} onClick={() => toggleWatch(t.address)} className={w ? 'text-paper' : 'text-sub/40 hover:text-sub'}><IStar filled={!!w} /></button>
                        </td>
                        <td className="px-2">
                          <div className="flex items-center gap-2">
                            <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={dense ? 22 : 28} />
                            <div className="min-w-0 leading-tight">
                              <div className="flex items-center gap-1.5">
                                <span className="max-w-[120px] truncate">{t.symbol}</span>
                                <span className="rounded bg-raised px-1 text-[10px] font-normal text-fg/80">{CHAIN_LABEL[t.chain]}</span>
                                {t.verified && <span title="已认证" className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-agent text-[9px] text-white">✓</span>}
                                <SecurityBadge s={t.security} />
                                <QualityBadge t={t} />
                              </div>
                              {!dense && <div className="max-w-[220px] truncate text-[11px] font-normal text-sub">{t.name} · {age(t.ageMinutes)}{t.launchpad && t.launchpad !== t.pair?.dex ? ` · ${t.launchpad}` : ''}</div>}
                            </div>
                          </div>
                        </td>
                        <td className="px-1" onClick={(e) => e.stopPropagation()}>
                          <div className="flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                            {demo
                              ? <button onClick={() => demoBuy(t)} className="h-8 w-[65px] whitespace-nowrap rounded-full bg-agent text-[13px] text-white hover:bg-agent/90">+ $10</button>
                              : <QuickBuyButton t={t} className="h-8 min-w-[72px] whitespace-nowrap rounded-full bg-agent px-2 text-[12px] text-white hover:bg-agent/90" />}
                            {showAgent && <button onClick={() => research(t)} aria-label={`交给 Agent 研究 ${t.symbol}`} title="交给 Agent 研究" className="flex h-8 w-8 items-center justify-center rounded-full border border-line text-sub hover:text-fg"><IBot /></button>}
                          </div>
                        </td>
                        {tab === 'bonding' && (
                          <td className="px-3 text-right">
                            <div className="ml-auto flex w-28 items-center gap-2"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-up" style={{ width: `${t.bondingPct ?? 0}%` }} /></div><span className="num w-9 text-xs">{t.bondingPct ?? '--'}%</span></div>
                          </td>
                        )}
                        {cols.map((c) => <td key={c.key} className="num whitespace-nowrap px-3 text-right">{c.cell(t, tf)}</td>)}
                        {showAgent && <td className="px-3"><VerdictTag v={t.verdict} /></td>}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <ul className="md:hidden">
              {rows.map((t) => (
                <li key={t.address} className="flex h-[61px] items-center gap-3 border-t border-line">
                  <button onClick={() => nav(`/token/${t.address}`)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                    <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={38} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-[14px]">{t.symbol}{t.verified && <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-agent text-[9px] text-white">✓</span>}<SecurityBadge s={t.security} /><QualityBadge t={t} /></div>
                      <div className="num truncate text-[12px] font-normal text-sub">{CHAIN_LABEL[t.chain]} · <Masked t={t}>{usd(t.mcapUsd, { compact: true })}</Masked>{showAgent && <> · <VerdictInline t={t} /></>}</div>
                    </div>
                    <div className="text-right">
                      <div className="num text-[14px] font-medium"><Masked t={t}>{price(t.priceUsd)}</Masked></div>
                      <ChangeCell t={t} v={t.stats[tf].change} className="text-[12px]" />
                    </div>
                  </button>
                  {demo
                    ? <button onClick={() => demoBuy(t)} className="h-9 shrink-0 rounded-full bg-agent px-3 text-[14px] text-white">+ $10</button>
                    : <QuickBuyButton t={t} className="h-9 shrink-0 whitespace-nowrap rounded-full border border-agent/50 px-3 text-[13px] text-agent" />}
                </li>
              ))}
            </ul>
          </>
        )}
      </DataState>
    </section>
  )
}

function VerdictInline({ t }: { t: Token }) {
  const map = { unresearched: '未研究', researching: '研究中', security_veto: '安全否决', no_trade: '不交易', buy_signal: '建议买入', bought: 'Agent 已买' }
  return <span className={t.verdict === 'bought' ? 'text-up' : t.verdict === 'security_veto' ? 'text-down' : ''}>{map[t.verdict]}</span>
}


/** 列设置：显示 / 隐藏、上下调整顺序、紧凑模式。 */
function ColumnMenu() {
  const { cols, dense, toggle, move, setDense, reset } = useColumns()
  const [open, setOpen] = useState(false)
  return (
    <div className="relative hidden md:block">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="h-9 rounded-lg border border-line px-3 text-[14px] text-sub hover:text-fg">列设置</button>
      {open && (
        <div role="dialog" aria-label="列设置" className="absolute left-0 top-11 z-30 w-64 rounded-xl border border-line bg-panel p-3 text-[13px]">
          <label className="flex items-center justify-between pb-2"><span>紧凑模式（行高 36）</span><input type="checkbox" checked={dense} onChange={(e) => setDense(e.target.checked)} /></label>
          <ul className="divide-y divide-line border-t border-line">
            {[...cols, ...ALL_COLS.filter((k) => !cols.includes(k))].map((k: ColKey) => {
              const on = cols.includes(k)
              return (
                <li key={k} className="flex h-8 items-center gap-2">
                  <input type="checkbox" checked={on} onChange={() => toggle(k)} aria-label={`显示 ${COLS[k].label('24h')}`} />
                  <span className={cx('flex-1', !on && 'text-sub')}>{COLS[k].label('24h').replace('24h ', '')}</span>
                  {on && <>
                    <button onClick={() => move(k, -1)} aria-label={`${COLS[k].label('24h')} 上移`} className="px-1 text-sub hover:text-fg">↑</button>
                    <button onClick={() => move(k, 1)} aria-label={`${COLS[k].label('24h')} 下移`} className="px-1 text-sub hover:text-fg">↓</button>
                  </>}
                </li>
              )
            })}
          </ul>
          <div className="mt-2 flex justify-between">
            <button onClick={reset} className="text-sub hover:text-fg">恢复默认</button>
            <button onClick={() => setOpen(false)} className="text-agent">完成</button>
          </div>
        </div>
      )}
    </div>
  )
}
