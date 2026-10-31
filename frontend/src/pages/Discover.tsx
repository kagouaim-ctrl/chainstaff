import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTokens, now } from '@/api/hooks'
import { useApp, useToast } from '@/app/store'
import { DataState, Panel, Segmented, Tabs, TokenAvatar, Pct, Button, cx } from '@/components/ui'
import { IStar, IBot } from '@/components/icons'
import { VerdictTag, SecurityBadge, securityPass } from '@/features/tokenBits'
import { usd, price, age, compactCN, relTime } from '@/lib/format'
import type { Timeframe, Token } from '@/domain/types'

type TabKey = 'hot' | 'new' | 'researched' | 'bought' | 'watch'
type SortKey = 'mcap' | 'change' | 'volume' | 'liquidity' | 'holders' | 'age'

const TABS: { value: TabKey; label: string }[] = [
  { value: 'hot', label: '热门' },
  { value: 'new', label: '新池子' },
  { value: 'researched', label: 'Agent 已研究' },
  { value: 'bought', label: 'Agent 已买入' },
  { value: 'watch', label: '自选' },
]
const CATS = ['全部', '猫', '狗', 'AI', '表情包', '动物', '美食']

export function filterTokens(list: Token[], o: { tab: TabKey; tf: Timeframe; cat: string; safeOnly: boolean; watch: Record<string, boolean>; sort: SortKey; dir: 1 | -1 }) {
  let r = list.slice()
  if (o.tab === 'new') r = r.filter((t) => t.ageMinutes < 24 * 60)
  if (o.tab === 'researched') r = r.filter((t) => t.verdict !== 'unresearched')
  if (o.tab === 'bought') r = r.filter((t) => t.verdict === 'bought')
  if (o.tab === 'watch') r = r.filter((t) => o.watch[t.address] ?? t.watch)
  if (o.cat !== '全部') r = r.filter((t) => t.tags.includes(o.cat))
  if (o.safeOnly) r = r.filter((t) => securityPass(t.security) === true)
  const key = (t: Token) => ({
    mcap: t.mcapUsd, change: t.stats[o.tf].change, volume: t.stats[o.tf].volume, liquidity: t.liquidityUsd, holders: t.holders, age: -t.ageMinutes,
  })[o.sort]
  r.sort((a, b) => (key(b) - key(a)) * o.dir)
  return r
}

export function Discover() {
  const q = useTokens()
  const nav = useNavigate()
  const toast = useToast((s) => s.show)
  const { watch, toggleWatch } = useApp()
  const [tab, setTab] = useState<TabKey>('hot')
  const [tf, setTf] = useState<Timeframe>('1h')
  const [cat, setCat] = useState('全部')
  const [safeOnly, setSafeOnly] = useState(false)
  const [sort, setSort] = useState<SortKey>('volume')
  const [dir, setDir] = useState<1 | -1>(1)
  const rows = useMemo(() => filterTokens(q.data ?? [], { tab, tf, cat, safeOnly, watch, sort, dir }), [q.data, tab, tf, cat, safeOnly, watch, sort, dir])
  const research = (t: Token) => toast(`后端未接入：没有为 ${t.symbol} 创建研究任务。接入后这里会把代币交给 Scout 进入研究流程。`)
  const th = (k: SortKey, label: string, cls = '') => (
    <th className={cx('px-3 py-2.5 text-right font-normal', cls)} aria-sort={sort === k ? (dir === 1 ? 'descending' : 'ascending') : 'none'}>
      <button className={cx('inline-flex items-center gap-1 hover:text-fg', sort === k && 'text-fg')} onClick={() => { if (sort === k) setDir((d) => (d === 1 ? -1 : 1)); else { setSort(k); setDir(1) } }}>
        {label}<span className="text-[10px]">{sort === k ? (dir === 1 ? '↓' : '↑') : '↕'}</span>
      </button>
    </th>
  )
  const updated = q.data?.[0]?.updatedAt

  return (
    <Panel demo bodyClass="p-0" title="发现" right={
      <span className="hidden text-xs text-sub md:inline">数据源：DexScreener（未接入，显示演示数据）{updated && ` · 更新于 ${relTime(updated, now())}`}</span>
    }>
      <Tabs label="代币分类" value={tab} options={TABS} onChange={setTab} />
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <Segmented label="时间段" size="sm" value={tf} onChange={setTf} options={(['5m', '1h', '4h', '24h'] as Timeframe[]).map((v) => ({ value: v, label: v }))} />
        <div className="scrollbar-none flex max-w-full gap-1.5 overflow-x-auto">
          {CATS.map((c) => (
            <button key={c} onClick={() => setCat(c)} aria-pressed={cat === c}
              className={cx('whitespace-nowrap rounded-lg border px-2.5 py-1 text-xs', cat === c ? 'border-fg/20 bg-raised text-fg' : 'border-line text-sub hover:text-fg')}>{c}</button>
          ))}
        </div>
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-sub">
          <input type="checkbox" checked={safeOnly} onChange={(e) => setSafeOnly(e.target.checked)} className="accent-[var(--c-agent)]" />只看安全检查通过
        </label>
      </div>

      <DataState q={q} rows={8} isEmpty={() => rows.length === 0} empty={tab === 'watch' ? '还没有自选代币。点代币左侧的星标加入自选。' : '当前筛选条件下没有代币。'}>
        {() => (
          <>
            {/* 桌面：表格 */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[980px] text-[13px]">
                <thead className="text-xs text-sub">
                  <tr className="border-b border-line">
                    <th className="w-10 px-3 py-2.5"><span className="sr-only">自选</span></th>
                    <th className="px-3 py-2.5 text-left font-normal">代币</th>
                    <th className="px-3 py-2.5 text-right font-normal">价格</th>
                    {th('change', `${tf} 涨跌`)}
                    {th('mcap', '市值')}
                    {th('volume', `${tf} 成交量`)}
                    {th('liquidity', '流动性')}
                    {th('holders', '持有者')}
                    <th className="px-3 py-2.5 text-center font-normal">安全</th>
                    <th className="px-3 py-2.5 text-left font-normal">Agent 结论</th>
                    <th className="w-[220px] px-3 py-2.5"><span className="sr-only">操作</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const s = t.stats[tf]
                    const w = watch[t.address] ?? t.watch
                    return (
                      <tr key={t.address} className="group h-[60px] cursor-pointer border-b border-line last:border-0 hover:bg-raised" onClick={() => nav(`/token/${t.address}`)}>
                        <td className="px-3" onClick={(e) => e.stopPropagation()}>
                          <button aria-label={w ? `取消自选 ${t.symbol}` : `自选 ${t.symbol}`} aria-pressed={!!w} onClick={() => toggleWatch(t.address)} className={w ? 'text-paper' : 'text-sub/50 hover:text-sub'}><IStar filled={!!w} /></button>
                        </td>
                        <td className="px-3">
                          <div className="flex items-center gap-3">
                            <TokenAvatar symbol={t.symbol} hue={t.hue} />
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 font-medium">{t.symbol}{!t.graduated && <span className="rounded bg-raised px-1 text-[10px] text-sub">新</span>}</div>
                              <div className="truncate text-xs text-sub">{t.name} · {age(t.ageMinutes)}</div>
                            </div>
                          </div>
                        </td>
                        <td className="num px-3 text-right">{price(t.priceUsd)}</td>
                        <td className="px-3 text-right"><Pct v={s.change} /></td>
                        <td className="num px-3 text-right">{usd(t.mcapUsd)}</td>
                        <td className="num px-3 text-right">{usd(s.volume)}</td>
                        <td className="num px-3 text-right">{usd(t.liquidityUsd)}</td>
                        <td className="num px-3 text-right">{compactCN(t.holders)}</td>
                        <td className="px-3 text-center"><SecurityBadge s={t.security} /></td>
                        <td className="px-3"><VerdictTag v={t.verdict} /></td>
                        <td className="px-3 text-right" onClick={(e) => e.stopPropagation()}>
                          <div className="flex justify-end gap-1.5 opacity-70 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                            <Button variant="outline" className="whitespace-nowrap px-2 py-1 text-xs" onClick={() => research(t)}><IBot />交给 Agent</Button>
                            <Button variant="up" className="whitespace-nowrap px-2 py-1 text-xs" onClick={() => nav(`/token/${t.address}#trade`)}>模拟买入</Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {/* 手机：列表 */}
            <ul className="md:hidden">
              {rows.map((t) => (
                <li key={t.address}>
                  <button onClick={() => nav(`/token/${t.address}`)} className="flex w-full items-center gap-3 border-b border-line px-4 py-3 text-left last:border-0 active:bg-raised">
                    <TokenAvatar symbol={t.symbol} hue={t.hue} size={36} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 font-medium">{t.symbol}<SecurityBadge s={t.security} /></div>
                      <div className="truncate text-xs text-sub">市值 {usd(t.mcapUsd)} · 流动性 {usd(t.liquidityUsd)}</div>
                    </div>
                    <div className="text-right">
                      <div className="num text-[13px]">{price(t.priceUsd)}</div>
                      <Pct v={t.stats[tf].change} className="text-xs" />
                    </div>
                    <div className="w-[64px] text-right"><VerdictTag v={t.verdict} /></div>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </DataState>
    </Panel>
  )
}
