import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTokens, now } from '@/api/hooks'
import { useApp, useToast } from '@/app/store'
import { DataState, Segmented, TokenAvatar, Pct, cx } from '@/components/ui'
import { IStar, IBot } from '@/components/icons'
import { VerdictTag, SecurityBadge, securityPass } from '@/features/tokenBits'
import { usd, price, age, compactCN, relTime } from '@/lib/format'
import type { Timeframe, Token } from '@/domain/types'

export type TabKey = 'trending' | 'blue' | 'held' | 'graduated' | 'bonding' | 'agent' | 'watch'
export type SortKey = 'mcap' | 'change' | 'volume' | 'liquidity' | 'holders' | 'age' | 'bonding'

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

export interface Filter { tab: TabKey; tf: Timeframe; safeOnly: boolean; minMcap: number; launchpad: string; watch: Record<string, boolean>; sort: SortKey; dir: 1 | -1 }

export function filterTokens(list: Token[], o: Filter) {
  let r = list.slice()
  if (o.tab === 'blue') r = r.filter((t) => t.verified)
  if (o.tab === 'graduated') r = r.filter((t) => t.graduated)
  if (o.tab === 'bonding') r = r.filter((t) => !t.graduated)
  if (o.tab === 'agent') r = r.filter((t) => t.verdict === 'bought')
  if (o.tab === 'watch') r = r.filter((t) => o.watch[t.address] ?? t.watch)
  if (o.safeOnly) r = r.filter((t) => securityPass(t.security) === true)
  if (o.minMcap) r = r.filter((t) => t.mcapUsd >= o.minMcap)
  if (o.launchpad !== 'all') r = r.filter((t) => t.launchpad === o.launchpad)
  const key = (t: Token) => ({
    mcap: t.mcapUsd, change: t.stats[o.tf].change, volume: t.stats[o.tf].volume, liquidity: t.liquidityUsd,
    holders: t.holders, age: -t.ageMinutes, bonding: t.bondingPct,
  })[o.sort]
  r.sort((a, b) => (key(b) - key(a)) * o.dir)
  return r
}

export function Discover() {
  const q = useTokens()
  const [split, setSplit] = useState(false)
  return (
    <div className="space-y-4">
      <WatchStrip />
      <div className="flex items-center justify-between text-xs text-sub">
        <span>数据源：DexScreener（未接入，显示演示数据）{q.data?.[0] && ` · 更新于 ${relTime(q.data[0].updatedAt, now())}`}</span>
        <button onClick={() => setSplit((v) => !v)} aria-pressed={split} className="hidden rounded-lg border border-line px-2.5 py-1 hover:text-fg 2xl:block">{split ? '单表' : '分屏'}</button>
      </div>
      <div className={cx('grid gap-4', split && '2xl:grid-cols-2')}>
        <TokenTable initial="trending" compact={split} />
        {split && <div className="hidden 2xl:block"><TokenTable initial="agent" compact /></div>}
      </div>
    </div>
  )
}

/** 自选横条（参考 fomo）。 */
function WatchStrip() {
  const q = useTokens()
  const nav = useNavigate()
  const { watch } = useApp()
  const list = (q.data ?? []).filter((t) => watch[t.address] ?? t.watch)
  if (!q.data) return null
  return (
    <section aria-label="自选" className="scrollbar-none -mx-3 flex gap-2 overflow-x-auto px-3 sm:-mx-5 sm:px-5">
      {list.length === 0 && <p className="text-xs text-sub">还没有自选。点代币左边的星标加入。</p>}
      {list.map((t) => (
        <button key={t.address} onClick={() => nav(`/token/${t.address}`)} className="flex min-w-[168px] items-center gap-2.5 rounded-xl border border-line bg-panel px-3 py-2.5 text-left hover:bg-raised">
          <TokenAvatar symbol={t.symbol} hue={t.hue} size={30} />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1 text-[13px] font-medium">{t.symbol}<IStar filled className="text-paper" width={11} height={11} /></span>
            <span className="num block text-xs text-sub">{usd(t.mcapUsd)}</span>
          </span>
          <Pct v={t.stats['24h'].change} className="text-xs" />
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
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT[initial])
  const [dir, setDir] = useState<1 | -1>(1)
  const rows = useMemo(() => filterTokens(q.data ?? [], { tab, tf, safeOnly, minMcap, launchpad, watch, sort, dir }), [q.data, tab, tf, safeOnly, minMcap, launchpad, watch, sort, dir])
  const quickBuy = (t: Token) => toast(`后端未接入：没有下单。接入后「+ $10」会把 ${t.symbol} 的模拟买入交给风控审批。`)
  const research = (t: Token) => toast(`后端未接入：没有为 ${t.symbol} 创建研究任务。`)
  const switchTab = (v: TabKey) => { setTab(v); setSort(DEFAULT_SORT[v]); setDir(1) }
  const th = (k: SortKey, label: string, cls = '') => (
    <th className={cx('px-3 py-3 text-right font-normal', cls)} aria-sort={sort === k ? (dir === 1 ? 'descending' : 'ascending') : 'none'}>
      <button className={cx('inline-flex items-center gap-1 hover:text-fg', sort === k && 'text-fg')} onClick={() => { if (sort === k) setDir((d) => (d === 1 ? -1 : 1)); else { setSort(k); setDir(1) } }}>
        {label}<span className="text-[10px]">{sort === k ? (dir === 1 ? '↓' : '↑') : '↕'}</span>
      </button>
    </th>
  )
  const sel = 'h-8 rounded-lg border border-line bg-panel px-2 text-xs text-fg'

  return (
    <section className="min-w-0 rounded-card border border-line bg-panel">
      <div role="tablist" aria-label="代币分类" className="scrollbar-none flex gap-1 overflow-x-auto border-b border-line px-3 pt-3">
        {TABS.map((t) => (
          <button key={t.value} role="tab" aria-selected={tab === t.value} onClick={() => switchTab(t.value)}
            className={cx('-mb-px whitespace-nowrap border-b-2 px-2.5 pb-2.5 text-[14px] font-medium', tab === t.value ? 'border-agent text-fg' : 'border-transparent text-sub hover:text-fg')}>
            {t.label}
          </button>
        ))}
        <span className="ml-auto self-center pb-2"><span className="rounded border border-dashed border-sub/60 px-1.5 py-0.5 text-[11px] text-sub">演示</span></span>
      </div>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
        <Segmented label="时间段" size="sm" value={tf} onChange={setTf} options={(['5m', '1h', '4h', '24h'] as Timeframe[]).map((v) => ({ value: v, label: v }))} />
        <select aria-label="市值" className={sel} value={minMcap} onChange={(e) => setMinMcap(Number(e.target.value))}>
          <option value={0}>市值：全部</option><option value={1e6}>≥ $100万</option><option value={1e7}>≥ $1000万</option><option value={1e8}>≥ $1亿</option>
        </select>
        <select aria-label="发射平台" className={sel} value={launchpad} onChange={(e) => setLaunchpad(e.target.value)}>
          <option value="all">平台：全部</option><option>pump.fun</option><option>bonk.fun</option><option>Raydium</option>
        </select>
        <span className="rounded-lg border border-line px-2 py-1.5 text-xs text-sub" title="只支持 Solana，其他链未适配">链：Solana</span>
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs text-sub">
          <input type="checkbox" checked={safeOnly} onChange={(e) => setSafeOnly(e.target.checked)} className="accent-[var(--c-agent)]" />只看安全
        </label>
      </div>
      {tab === 'bonding' && <p className="mx-3 mb-2 rounded-lg bg-raised px-3 py-2 text-xs text-sub">内盘代币还在发射曲线上，没有 LP，Agent 按风控规则不会交易，只能手动关注。</p>}

      <DataState q={q} rows={8} isEmpty={() => rows.length === 0} empty={tab === 'watch' ? '还没有自选代币。点代币左侧的星标加入自选。' : '当前筛选条件下没有代币。'}>
        {() => (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[900px] text-[14px] font-medium">
                <thead className="text-xs text-sub">
                  <tr className="border-y border-line">
                    <th className="w-9 py-3 pl-3"><span className="sr-only">自选</span></th>
                    <th className="px-3 py-3 text-left font-normal">代币</th>
                    <th className="w-[96px] px-1 py-3"><span className="sr-only">快速买入</span></th>
                    {tab === 'bonding' ? th('bonding', '内盘进度') : th('mcap', '市值')}
                    {th('change', `${tf} 涨跌`)}
                    {!compact && th('volume', `${tf} 成交额`)}
                    {!compact && th('liquidity', '流动性')}
                    {th('holders', '持有者')}
                    <th className="px-3 py-3 text-left font-normal">Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const s = t.stats[tf]
                    const w = watch[t.address] ?? t.watch
                    return (
                      <tr key={t.address} className="group h-[61px] cursor-pointer border-b border-line transition-colors last:border-0 hover:bg-raised" onClick={() => nav(`/token/${t.address}`)}>
                        <td className="pl-3" onClick={(e) => e.stopPropagation()}>
                          <button aria-label={w ? `取消自选 ${t.symbol}` : `自选 ${t.symbol}`} aria-pressed={!!w} onClick={() => toggleWatch(t.address)} className={w ? 'text-paper' : 'text-sub/40 hover:text-sub'}><IStar filled={!!w} /></button>
                        </td>
                        <td className="px-3">
                          <div className="flex items-center gap-3">
                            <TokenAvatar symbol={t.symbol} hue={t.hue} size={34} />
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                {t.symbol}
                                {t.verified && <span title="已认证" className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-agent text-[9px] text-white">✓</span>}
                                <SecurityBadge s={t.security} />
                              </div>
                              <div className="truncate text-xs font-normal text-sub">{t.name} · {age(t.ageMinutes)}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-1" onClick={(e) => e.stopPropagation()}>
                          <div className="flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                            <button onClick={() => quickBuy(t)} className="h-8 whitespace-nowrap rounded-full bg-agent px-3 text-xs font-semibold text-white hover:bg-agent/90">+ $10</button>
                            <button onClick={() => research(t)} aria-label={`交给 Agent 研究 ${t.symbol}`} title="交给 Agent 研究" className="flex h-8 w-8 items-center justify-center rounded-full border border-line text-sub hover:text-fg"><IBot /></button>
                          </div>
                        </td>
                        {tab === 'bonding' ? (
                          <td className="px-3 text-right">
                            <div className="ml-auto flex w-28 items-center gap-2"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-up" style={{ width: `${t.bondingPct}%` }} /></div><span className="num w-9 text-xs">{t.bondingPct}%</span></div>
                          </td>
                        ) : <td className="num px-3 text-right">{usd(t.mcapUsd)}</td>}
                        <td className="px-3 text-right"><Pct v={s.change} /></td>
                        {!compact && <td className="num px-3 text-right">{usd(s.volume)}</td>}
                        {!compact && <td className="num px-3 text-right">{usd(t.liquidityUsd)}</td>}
                        <td className="num px-3 text-right">{compactCN(t.holders)}</td>
                        <td className="px-3"><VerdictTag v={t.verdict} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <ul className="md:hidden">
              {rows.map((t) => (
                <li key={t.address} className="flex items-center gap-3 border-t border-line px-3 py-3">
                  <button onClick={() => nav(`/token/${t.address}`)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                    <TokenAvatar symbol={t.symbol} hue={t.hue} size={38} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-[15px] font-medium">{t.symbol}{t.verified && <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-agent text-[9px] text-white">✓</span>}<SecurityBadge s={t.security} /></div>
                      <div className="num truncate text-xs text-sub">{usd(t.mcapUsd)} · <VerdictInline t={t} /></div>
                    </div>
                    <div className="text-right">
                      <div className="num text-[14px] font-medium">{price(t.priceUsd)}</div>
                      <Pct v={t.stats[tf].change} className="text-xs" />
                    </div>
                  </button>
                  <button onClick={() => quickBuy(t)} className="h-8 shrink-0 rounded-full bg-agent px-3 text-xs font-semibold text-white">+ $10</button>
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
  const map = { unresearched: '未研究', researching: '研究中', security_veto: '安全否决', no_trade: '不交易', bought: 'Agent 已买' }
  return <span className={t.verdict === 'bought' ? 'text-up' : t.verdict === 'security_veto' ? 'text-down' : ''}>{map[t.verdict]}</span>
}
