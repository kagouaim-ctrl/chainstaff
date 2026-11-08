import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { Button, NotConnected, Segmented, Skeleton, cx } from '@/components/ui'
import { usd, shortAddr, compactCN } from '@/lib/format'
import { leaderboard, topTraders, walletDetail, NotConfigured, TAG_TEXT, TAGS, type PnlSummary } from '@/api/wallets'

/** 钱包数据的加载、未配置、出错三种状态。未配置时说清楚，不显示空表。 */
function WalletState<T>({ q, children, empty }: { q: UseQueryResult<T>; children: (d: T) => React.ReactNode; empty: (d: T) => boolean }) {
  if (q.isPending) return <div className="space-y-2 p-4" aria-busy="true">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
  if (q.error instanceof NotConfigured) return <NotConnected what="钱包情报（Birdeye）" how="需要在后端服务器配置 BIRDEYE_API_KEY 后才有数据。" />
  if (q.isError) return (
    <div role="alert" className="flex flex-col items-start gap-2 p-4 text-sm">
      <p className="text-down">加载失败：{(q.error as Error).message}</p>
      <Button variant="outline" onClick={() => q.refetch()}>重试</Button>
    </div>
  )
  if (empty(q.data as T)) return <p className="p-6 text-center text-sm text-sub">Birdeye 没有返回记录</p>
  return <>{children(q.data as T)}</>
}

const Pnl = ({ v }: { v: number | null }) => <span className={cx('num', v == null ? 'text-mute' : v >= 0 ? 'text-up' : 'text-down')}>{usd(v, { sign: true })}</span>
const TagChip = ({ tag }: { tag: string }) => <span className={cx('rounded px-1 text-[10px]', tag === 'smart_trader' ? 'bg-agent/20 text-agent' : tag === 'insider' || tag === 'bundler' ? 'bg-down/15 text-down' : 'bg-raised text-sub')}>{TAG_TEXT[tag] ?? tag}</span>
const WalletLink = ({ a }: { a: string }) => <Link to={`/wallet/${a}`} className="num hover:text-agent" title={a}>{shortAddr(a, 4, 4)}</Link>

/** 代币页「交易员」：这个代币成交额最大的钱包，可以按 Birdeye 的标签筛（狙击、老鼠仓、聪明钱等）。 */
export function TopTraders({ mint }: { mint: string }) {
  const [tf, setTf] = useState('24h')
  const [tag, setTag] = useState<string>('')
  const q = useQuery({ queryKey: ['traders', mint, tf, tag], queryFn: () => topTraders(mint, tf, tag || undefined), retry: (n, e) => !(e instanceof NotConfigured) && n < 1 })
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Segmented size="sm" label="时间范围" value={tf} onChange={setTf} options={['1h', '4h', '24h', '7d'].map((v) => ({ value: v, label: v }))} />
        <Segmented size="sm" label="钱包标签" value={tag} onChange={setTag} options={[{ value: '', label: '全部' }, ...TAGS.map((t) => ({ value: t, label: TAG_TEXT[t] }))]} />
      </div>
      <WalletState q={q} empty={(d) => !d.length}>
        {(rows) => (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-[13px]">
              <thead className="text-[12px] text-sub"><tr className="h-9 border-b border-line text-right">
                <th className="px-4 text-left font-normal">钱包</th><th className="px-3 font-normal">买 / 卖笔数</th><th className="px-3 font-normal">买入额</th><th className="px-3 font-normal">卖出额</th>
                <th className="px-3 font-normal">已实现</th><th className="px-3 font-normal">未实现</th><th className="px-4 font-normal">仍持有</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.wallet} className="h-11 border-b border-line text-right last:border-0">
                    <td className="px-4 text-left"><div className="flex items-center gap-1.5"><WalletLink a={r.wallet} />{r.tags.map((t) => <TagChip key={t} tag={t} />)}</div></td>
                    <td className="num px-3"><span className="text-up">{r.buys ?? '--'}</span> / <span className="text-down">{r.sells ?? '--'}</span></td>
                    <td className="num px-3">{usd(r.buyUsd)}</td><td className="num px-3">{usd(r.sellUsd)}</td>
                    <td className="px-3"><Pnl v={r.realizedPnl} /></td><td className="px-3"><Pnl v={r.unrealizedPnl} /></td>
                    <td className="num px-4">{usd(r.holdUsd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </WalletState>
      <p className="px-4 py-2 text-[11px] text-sub">数据和标签来自 Birdeye，每 2 分钟更新。标签是 Birdeye 的判断，可能有误。</p>
    </div>
  )
}

/** 交易员排行：Birdeye 的盈利榜，可以按总盈亏、已实现盈亏或交易员评分排。 */
export function SmartMoney() {
  const [period, setPeriod] = useState('1W')
  const [sort, setSort] = useState('PnL')
  const q = useQuery({ queryKey: ['board', period, sort], queryFn: () => leaderboard(period, sort), retry: (n, e) => !(e instanceof NotConfigured) && n < 1 })
  return (
    <div className="mx-auto max-w-[1200px] px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-[20px] font-bold">聪明钱排行</h1>
        <span className="text-[12px] text-sub">Solana</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <Segmented size="sm" label="周期" value={period} onChange={setPeriod} options={[{ value: 'today', label: '今天' }, { value: 'yesterday', label: '昨天' }, { value: '1W', label: '7 天' }, { value: '30d', label: '30 天' }, { value: '90d', label: '90 天' }]} />
          <Segmented size="sm" label="排序" value={sort} onChange={setSort} options={[{ value: 'PnL', label: '总盈亏' }, { value: 'realized_pnl', label: '已实现' }, { value: 'trader_score', label: '评分' }]} />
        </div>
      </div>
      <section className="rounded-xl border border-line bg-panel">
        <WalletState q={q} empty={(d) => !d.length}>
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-[13px]">
                <thead className="text-[12px] text-sub"><tr className="h-9 border-b border-line text-right">
                  <th className="w-10 px-3 text-left font-normal">#</th><th className="px-3 text-left font-normal">钱包</th><th className="px-3 font-normal">总盈亏</th>
                  <th className="px-3 font-normal">已实现</th><th className="px-3 font-normal">未实现</th><th className="px-3 font-normal">成交额</th><th className="px-4 font-normal">笔数</th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.wallet} className="h-11 border-b border-line text-right last:border-0 hover:bg-raised">
                      <td className="num px-3 text-left text-sub">{i + 1}</td>
                      <td className="px-3 text-left"><WalletLink a={r.wallet} />{r.score != null && <span className="ml-2 text-[11px] text-sub">评分 {r.score.toFixed(0)}</span>}</td>
                      <td className="px-3"><Pnl v={r.pnl} /></td><td className="px-3"><Pnl v={r.realizedPnl} /></td><td className="px-3"><Pnl v={r.unrealizedPnl} /></td>
                      <td className="num px-3">{usd(r.volumeUsd)}</td><td className="num px-4">{r.trades == null ? '--' : compactCN(r.trades)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </WalletState>
      </section>
      <p className="mt-3 text-[12px] text-sub">数据来自 Birdeye 交易员盈亏榜，每 10 分钟更新。盈亏是历史结果，不代表以后，跟单有风险。</p>
    </div>
  )
}

function PnlCards({ p }: { p: PnlSummary }) {
  const items: [string, React.ReactNode][] = [
    ['总盈亏', <Pnl v={p.totalUsd} />], ['已实现', <Pnl v={p.realizedUsd} />], ['未实现', <Pnl v={p.unrealizedUsd} />],
    ['胜率', p.winRate == null ? '--' : `${(p.winRate * (p.winRate <= 1 ? 100 : 1)).toFixed(1)}%`],
    ['买入总额', usd(p.investedUsd)], ['卖出总额', usd(p.soldUsd)], ['交易代币数', p.tokens ?? '--'], ['买 / 卖笔数', `${p.buys ?? '--'} / ${p.sells ?? '--'}`],
  ]
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
      {items.map(([k, v]) => <div key={k} className="bg-panel px-4 py-3"><div className="text-[12px] text-sub">{k}</div><div className="num mt-1 text-[16px]">{v}</div></div>)}
    </div>
  )
}

/** 钱包详情：Birdeye 的盈亏汇总和最近 50 笔兑换。持仓用钱包页（Jupiter 余额）看。 */
export function WalletPage() {
  const { address = '' } = useParams()
  const [duration, setDuration] = useState('30d')
  const q = useQuery({ queryKey: ['wallet', address, duration], queryFn: () => walletDetail(address, duration), retry: (n, e) => !(e instanceof NotConfigured) && n < 1 })
  return (
    <div className="mx-auto max-w-[1200px] px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="num text-[18px] font-bold" title={address}>{shortAddr(address, 6, 6)}</h1>
        <button className="text-[12px] text-sub hover:text-fg" onClick={() => navigator.clipboard?.writeText(address)}>复制地址</button>
        <a className="text-[12px] text-sub hover:text-fg" href={`https://solscan.io/account/${address}`} target="_blank" rel="noreferrer">在 Solscan 查看</a>
        <div className="ml-auto"><Segmented size="sm" label="统计周期" value={duration} onChange={setDuration} options={[{ value: '24h', label: '24h' }, { value: '7d', label: '7 天' }, { value: '30d', label: '30 天' }, { value: '90d', label: '90 天' }, { value: 'all', label: '全部' }]} /></div>
      </div>
      <WalletState q={q} empty={() => false}>
        {(d) => (
          <div className="space-y-4">
            {d.pnl ? <PnlCards p={d.pnl} /> : <p className="text-sm text-sub">Birdeye 没有这个钱包在这段时间的盈亏记录。</p>}
            <section className="rounded-xl border border-line bg-panel">
              <h2 className="border-b border-line px-4 py-2 text-[14px] font-semibold">最近兑换</h2>
              {d.trades.length === 0 ? <p className="p-6 text-center text-sm text-sub">没有兑换记录</p> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[600px] text-[13px]">
                    <thead className="text-[12px] text-sub"><tr className="h-9 border-b border-line text-right">
                      <th className="px-4 text-left font-normal">时间</th><th className="px-3 text-left font-normal">方向</th><th className="px-3 text-left font-normal">代币</th><th className="px-3 font-normal">数量</th><th className="px-3 font-normal">金额</th><th className="px-4 font-normal">交易</th>
                    </tr></thead>
                    <tbody>
                      {d.trades.map((t, i) => (
                        <tr key={t.tx ?? i} className="h-10 border-b border-line text-right last:border-0">
                          <td className="num px-4 text-left text-sub">{t.at ? new Date(t.at * 1000).toLocaleString() : '--'}</td>
                          <td className={cx('px-3 text-left', t.side === 'buy' ? 'text-up' : t.side === 'sell' ? 'text-down' : 'text-sub')}>{t.side === 'buy' ? '买入' : t.side === 'sell' ? '卖出' : '--'}</td>
                          <td className="px-3 text-left">{t.base.address ? <Link className="hover:text-agent" to={`/token/${t.base.address}`}>{t.base.symbol ?? shortAddr(t.base.address)}</Link> : '--'}</td>
                          <td className="num px-3">{t.base.amount == null ? '--' : compactCN(Math.abs(t.base.amount))}</td>
                          <td className="num px-3">{usd(t.volumeUsd)}</td>
                          <td className="px-4">{t.tx ? <a className="text-sub hover:text-fg" href={`https://solscan.io/tx/${t.tx}`} target="_blank" rel="noreferrer">查看</a> : '--'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <p className="text-[12px] text-sub">盈亏和成交来自 Birdeye，每 2 分钟更新。</p>
          </div>
        )}
      </WalletState>
    </div>
  )
}
