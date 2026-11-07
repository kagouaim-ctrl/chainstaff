import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { DataState, Segmented, TokenAvatar, cx } from '@/components/ui'
import { usd, age, compactCN, relTime } from '@/lib/format'
import { CHAIN_LABEL } from '@/api/chain'
import { launches, LAUNCH_CHAINS, type LaunchChain, type Launches } from '@/api/launches'
import { isDemo, useTokens } from '@/api/hooks'
import { QuickBuyButton } from '@/features/QuickBuy'
import { ChangeCell, Masked, QualityBadge, dataBad } from '@/features/tokenBits'
import type { Token } from '@/domain/types'

/**
 * 新币页：三列并排，刚创建 / 快毕业 / 刚毕业。数据每 60 秒刷新一次。
 * 列表不查安全报告（每个代币都查会把免费接口打满），点进代币页才查。
 */
type ColKey = 'fresh' | 'soon' | 'graduated'
const MIN_LIQ = [0, 1_000, 5_000, 10_000] as const

/** 演示模式没有真实新币，用演示列表按同样的规则分三列 */
function fromDemo(ts: Token[]): Launches {
  return {
    fresh: ts.filter((t) => !t.graduated).sort((a, b) => a.ageMinutes - b.ageMinutes),
    soon: ts.filter((t) => !t.graduated && (t.bondingPct ?? 0) >= 50).sort((a, b) => (b.bondingPct ?? 0) - (a.bondingPct ?? 0)),
    graduated: ts.filter((t) => t.graduated && t.ageMinutes < 24 * 60).sort((a, b) => a.ageMinutes - b.ageMinutes),
    hasProgress: true, updatedAt: new Date().toISOString(),
  }
}

export function applyFilters(ts: Token[], f: { minLiq: number; hideBad: boolean; minTxns1h: number }): Token[] {
  return ts.filter((t) => t.liquidityUsd >= f.minLiq && (!f.hideBad || !dataBad(t)) && t.stats['1h'].txns >= f.minTxns1h)
}

export function NewTokens() {
  const demo = isDemo()
  const [chain, setChain] = useState<LaunchChain>('solana')
  const [mobileCol, setMobileCol] = useState<ColKey>('fresh')
  const [minLiq, setMinLiq] = useState(0)
  const [minTxns1h, setMinTxns1h] = useState(0)
  const [hideBad, setHideBad] = useState(true)
  const demoQ = useTokens()
  const liveQ = useQuery({ queryKey: ['launches', chain], queryFn: () => launches(chain), refetchInterval: 60_000, enabled: !demo })
  const q = demo ? { ...demoQ, data: demoQ.data ? fromDemo(demoQ.data.filter((t) => t.chain === chain)) : undefined } as typeof liveQ : liveQ
  const f = { minLiq, hideBad, minTxns1h }
  // 刷新失败但有上次的数据时，继续显示上次的数据，并说明没刷新成功
  const stale = q.isError && !!q.data
  const shown = stale ? { ...q, isError: false } as unknown as typeof q : q

  const cols: { key: ColKey; title: string; note: string }[] = useMemo(() => [
    { key: 'fresh', title: '新创建', note: '刚在发射平台上创建、还在内盘上的代币，最新的在前。' },
    {
      key: 'soon', title: q.data?.hasProgress === false ? '内盘活跃' : '即将毕业',
      note: q.data?.hasProgress === false
        ? `${CHAIN_LABEL[chain]} 上读不到内盘进度，这里按 24 小时成交笔数列出内盘代币，不代表快毕业。`
        : 'pump.fun 内盘代币按链上读到的进度排序，进度 100% 时迁移到外盘。',
    },
    { key: 'graduated', title: '刚毕业', note: '24 小时内迁移到外盘的代币。按迁移目标判断：pump.fun 代币在 PumpSwap、bonk.fun 代币在 Raydium、four.meme 代币在 PancakeSwap 上新建的池子。' },
  ], [q.data?.hasProgress, chain])

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-[20px] font-bold">新币</h1>
        <Segmented size="sm" label="链" value={chain} onChange={setChain} options={LAUNCH_CHAINS.map((c) => ({ value: c, label: CHAIN_LABEL[c] }))} />
        <div className="ml-auto flex flex-wrap items-center gap-3 text-[12px] text-sub">
          <label className="flex items-center gap-1">最低流动性
            <select aria-label="最低流动性" value={minLiq} onChange={(e) => setMinLiq(Number(e.target.value))} className="rounded border border-line bg-panel px-1 py-0.5 text-fg">
              {MIN_LIQ.map((v) => <option key={v} value={v}>{v ? usd(v, { compact: true }) : '不限'}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1">1h 最少笔数
            <select aria-label="1 小时最少成交笔数" value={minTxns1h} onChange={(e) => setMinTxns1h(Number(e.target.value))} className="rounded border border-line bg-panel px-1 py-0.5 text-fg">
              {[0, 10, 50, 200].map((v) => <option key={v} value={v}>{v || '不限'}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={hideBad} onChange={(e) => setHideBad(e.target.checked)} />隐藏数据异常</label>
        </div>
      </div>

      {(stale || q.data?.partial) && (
        <p role="status" className="mb-3 rounded-lg border border-line bg-panel px-3 py-2 text-[12px] text-sub">
          {stale ? `刷新失败（${(q.error as Error).message}），下面是上次读到的数据。` : '部分数据源这次没读到（公开接口限流），列表可能不全，稍后会自动刷新。'}
        </p>
      )}
      <div className="mb-3 lg:hidden">
        <Segmented size="sm" label="新币分栏" value={mobileCol} onChange={setMobileCol} options={cols.map((c) => ({ value: c.key, label: c.title }))} />
      </div>

      <DataState q={shown} rows={8}>
        {(d) => (
          <div className="grid gap-3 lg:grid-cols-3">
            {cols.map((c) => {
              const rows = applyFilters(d[c.key], f)
              return (
                <section key={c.key} aria-label={c.title} className={cx('min-w-0 rounded-xl border border-line bg-panel', mobileCol !== c.key && 'hidden lg:block')}>
                  <header className="flex items-baseline gap-2 border-b border-line px-3 py-2">
                    <h2 className="shrink-0 whitespace-nowrap text-[14px] font-semibold">{c.title}</h2>
                    <span className="num text-[12px] text-sub">{rows.length}</span>
                    <p className="ml-auto min-w-0 truncate text-[11px] font-normal text-sub" title={c.note}>{c.note}</p>
                  </header>
                  {rows.length === 0
                    ? <p className="p-6 text-center text-[13px] text-sub">{d[c.key].length ? '没有符合筛选条件的代币' : '暂时没有'}</p>
                    : <ul className="max-h-[calc(100vh-220px)] overflow-y-auto">{rows.map((t) => <LaunchRow key={t.address} t={t} showProgress={c.key !== 'graduated'} demo={demo} />)}</ul>}
                </section>
              )
            })}
          </div>
        )}
      </DataState>
      <p className="mt-3 text-[12px] font-normal text-sub">
        数据源：{demo ? '演示数据' : 'GeckoTerminal 公开接口；pump.fun 内盘进度读自 Solana 链上发射曲线账户'}。新币风险极高，列表没有做安全检查，买入前请打开代币页看安全项。
        {q.data && ` 更新于 ${relTime(q.data.updatedAt, new Date())}。`}
      </p>
    </div>
  )
}

function LaunchRow({ t, showProgress, demo }: { t: Token; showProgress: boolean; demo: boolean }) {
  const nav = useNavigate()
  const h1 = t.stats['1h']
  return (
    <li className="group flex min-h-[58px] cursor-pointer items-center gap-2 border-b border-line px-3 py-1.5 last:border-0 hover:bg-raised" onClick={() => nav(`/token/${t.address}`)}>
      <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={32} />
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-1.5 whitespace-nowrap text-[13px]">
          <span className="max-w-[110px] truncate font-semibold">{t.symbol}</span>
          <span className="num shrink-0 text-[11px] font-normal text-sub">{age(t.ageMinutes)}</span>
          <span className="truncate text-[11px] font-normal text-sub">{t.launchpad}</span>
          <QualityBadge t={t} />
        </div>
        <div className="num mt-0.5 flex items-center gap-2 overflow-hidden whitespace-nowrap text-[11px] font-normal text-sub">
          <span>市值 <span className="text-fg"><Masked t={t}>{usd(t.mcapUsd, { compact: true })}</Masked></span></span>
          <span>池 {t.liquidityUsd > 0 ? usd(t.liquidityUsd, { compact: true }) : '--'}</span>
          <span className="hidden sm:inline" title={`1 小时：买 ${h1.buyers} 笔 / 卖 ${h1.sellers} 笔`}>1h {compactCN(h1.txns)} 笔 · {usd(h1.volume, { compact: true })}</span>
        </div>
        {showProgress && (
          <div className="mt-1 flex items-center gap-1.5" title={t.bondingPct == null ? '读不到内盘进度' : `内盘进度 ${t.bondingPct}%`}>
            <div className="h-1 w-24 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-up" style={{ width: `${t.bondingPct ?? 0}%` }} /></div>
            <span className="num whitespace-nowrap text-[10px] text-sub">{t.bondingPct == null ? '进度未知' : `${t.bondingPct}%`}</span>
          </div>
        )}
      </div>
      <div className="num w-14 text-right text-[12px]"><ChangeCell t={t} v={h1.change} /></div>
      {!demo && (
        <div onClick={(e) => e.stopPropagation()}>
          <QuickBuyButton t={t} className="h-7 min-w-[64px] whitespace-nowrap rounded-full bg-agent px-2 text-[11px] text-white hover:bg-agent/90 disabled:opacity-40" />
        </div>
      )}
    </li>
  )
}
