import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { Button, Segmented, TokenAvatar, cx } from '@/components/ui'
import { usd, price as fmtPrice } from '@/lib/format'
import { useToast } from '@/app/store'
import { CHAIN_LABEL, prices } from '@/api/chain'
import type { Chain, Token } from '@/domain/types'
import { PAPER_FEE, PAPER_START_OPTIONS, PaperError, paperBlocked, paperValue, usePaper, type PaperPosition } from './paper'

const BUY_USD = [10, 50, 100, 500]
const SELL_PCT = [25, 50, 100]
const signed = (n: number) => `${n >= 0 ? '+' : '-'}${usd(Math.abs(n))}`
const pnlTone = (n: number) => (n >= 0 ? 'text-up' : 'text-down')

/** 代币页右栏：用模拟资金按真实价格买卖这个代币。不连钱包，不上链。 */
export function PaperBox({ t }: { t: Token }) {
  const paper = usePaper()
  const toast = useToast((s) => s.show)
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [amount, setAmount] = useState('')
  const pos = paper.positions.find((p) => p.chain === t.chain && p.address === t.address)
  const blocked = paperBlocked(t)
  const run = (f: () => void) => { try { f() } catch (e) { toast(e instanceof PaperError ? e.message : `模拟失败：${(e as Error).message}`) } }
  const buy = (v: number) => run(() => { const tr = paper.buy(t, v); setAmount(''); toast(`模拟买入 ${t.symbol} ${usd(tr.usd)}，成交均价 ${fmtPrice(tr.fillUsd)}`) })
  const sell = (pct: number) => run(() => { const tr = paper.sell(t, pct); toast(`模拟卖出 ${t.symbol}，到手 ${usd(tr.usd)}，这笔${(tr.realizedUsd ?? 0) >= 0 ? '赚' : '亏'} ${usd(Math.abs(tr.realizedUsd ?? 0))}`) })
  const value = pos && t.priceUsd > 0 && t.quality?.level !== 'bad' ? pos.qty * t.priceUsd : null
  return (
    <section aria-label="模拟盘" className="rounded-xl border border-dashed border-line p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-[15px] font-medium">模拟盘</h2>
        <span className="rounded bg-raised px-1.5 text-[11px] text-sub">不花真钱</span>
        <Link to="/paper" className="ml-auto text-[12px] text-agent hover:underline">余额 {usd(paper.cashUsd)}</Link>
      </div>
      {pos && (
        <div className="num mt-2 flex flex-wrap gap-x-3 text-[12px] text-sub">
          <span>持有 {pos.qty.toLocaleString('en-US', { maximumFractionDigits: 2 })} {t.symbol}</span>
          <span>成本 {usd(pos.costUsd)}</span>
          {value != null && <span>现值 {usd(value)} <span className={pnlTone(value - pos.costUsd)}>{signed(value - pos.costUsd)}</span></span>}
        </div>
      )}
      <div className="mt-3"><Segmented label="模拟买卖方向" size="sm" value={side} onChange={setSide} options={[{ value: 'BUY', label: '模拟买入' }, { value: 'SELL', label: '模拟卖出' }]} /></div>
      {blocked
        ? <p className="mt-3 text-[12px] text-down">{blocked}</p>
        : side === 'BUY' ? (
          <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); buy(Number(amount)) }}>
            <div className="flex gap-1.5">
              {BUY_USD.map((v) => <button type="button" key={v} onClick={() => buy(v)} className="h-8 flex-1 rounded-lg border border-line text-[12px] hover:bg-raised">${v}</button>)}
            </div>
            <div className="flex gap-2">
              <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))} aria-label="模拟买入金额（美元）" placeholder="自定义金额（美元）"
                className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 text-[13px] outline-none focus:border-agent" />
              <Button type="submit" variant="outline" className="h-9 px-3 py-0 text-[13px]" disabled={!(Number(amount) > 0)}>买入</Button>
            </div>
          </form>
        ) : (
          <div className="mt-3 flex gap-1.5">
            {SELL_PCT.map((v) => <button key={v} disabled={!pos} onClick={() => sell(v)} className="h-8 flex-1 rounded-lg border border-line text-[12px] hover:bg-raised disabled:opacity-40">{v === 100 ? '全部' : `${v}%`}</button>)}
          </div>
        )}
      <p className="mt-2 text-[11px] leading-relaxed text-sub">按现价和池子深度估算成交价，另扣 {PAPER_FEE * 100}% 假设手续费。真实成交还受滑点、优先费和 MEV 影响，结果仅供练习。</p>
    </section>
  )
}

/** 模拟持仓的现价：同一条链的代币一次查完，60 秒刷新。数据异常的价格不计入。 */
function usePaperPrices(positions: PaperPosition[]) {
  const chains = [...new Set(positions.map((p) => p.chain))]
  const qs = useQueries({
    queries: chains.map((c) => ({
      queryKey: ['paper-prices', c, positions.filter((p) => p.chain === c).map((p) => p.address).sort().join(',')],
      queryFn: () => prices(c, positions.filter((p) => p.chain === c).map((p) => p.address)),
      refetchInterval: 60_000,
    })),
  })
  const byChain = new Map<Chain, Awaited<ReturnType<typeof prices>>>()
  chains.forEach((c, i) => { const d = qs[i]?.data; if (d) byChain.set(c, d) })
  const loading = qs.some((q) => q.isPending)
  return { priceOf: (p: PaperPosition) => byChain.get(p.chain)?.get(p.address)?.priceUsd ?? null, loading }
}

/** /paper：模拟盘总览 */
export function PaperPage() {
  const paper = usePaper()
  const [start, setStart] = useState<number>(paper.startUsd)
  const [confirm, setConfirm] = useState(false)
  const { priceOf, loading } = usePaperPrices(paper.positions)
  const v = paperValue(paper, priceOf)
  const sells = paper.trades.filter((x) => x.side === 'SELL')
  const wins = sells.filter((x) => (x.realizedUsd ?? 0) > 0).length
  return (
    <div className="mx-auto max-w-[1200px] space-y-6 px-4 py-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-[20px] font-bold">模拟盘</h1>
        <p className="text-[12px] text-sub">用真实行情练习交易，不连钱包、不上链、不花钱。记录只存在这台设备的浏览器里。</p>
      </div>

      <section aria-label="模拟盘概览" className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ['总资产', loading && paper.positions.length ? '…' : usd(v.totalUsd)],
          ['可用余额', usd(paper.cashUsd)],
          ['持仓市值', loading && paper.positions.length ? '…' : usd(v.marketUsd)],
          ['总盈亏', <span key="p" className={pnlTone(v.pnlUsd)}>{signed(v.pnlUsd)}（{v.pnlPct >= 0 ? '+' : ''}{v.pnlPct.toFixed(1)}%）</span>],
          ['已实现 / 胜率', <span key="r"><span className={pnlTone(v.realizedUsd)}>{signed(v.realizedUsd)}</span> · {sells.length ? `${Math.round((wins / sells.length) * 100)}%（${wins}/${sells.length}）` : '--'}</span>],
        ].map(([k, val]) => (
          <div key={k as string} className="rounded-xl border border-line p-3">
            <div className="text-[12px] text-sub">{k}</div>
            <div className="num mt-1 text-[16px] font-medium">{val}</div>
          </div>
        ))}
      </section>
      {v.unknown > 0 && <p className="text-[12px] text-sub">有 {v.unknown} 个持仓暂时读不到可信价格，没有计入市值和盈亏。</p>}

      <section aria-label="模拟持仓">
        <h2 className="mb-2 text-[16px] font-medium">持仓</h2>
        {paper.positions.length === 0
          ? <p className="rounded-xl border border-line p-6 text-center text-[13px] text-sub">还没有模拟持仓。打开任意代币页，在右侧「模拟盘」里买入。</p>
          : (
            <div className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full min-w-[720px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-b border-line">
                  {['代币', '数量', '成本', '现价', '现值', '未实现盈亏'].map((h, i) => <th key={h} className={cx('px-4 py-2.5 font-normal', i ? 'text-right' : 'text-left')}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {paper.positions.map((p) => {
                    const px = priceOf(p)
                    const val = px != null ? p.qty * px : null
                    return (
                      <tr key={`${p.chain}:${p.address}`} className="border-b border-line last:border-0 hover:bg-raised">
                        <td className="px-4 py-2.5"><Link to={`/token/${p.address}`} className="flex items-center gap-2 hover:underline"><TokenAvatar image={p.image} symbol={p.symbol} hue={[...p.address].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7)} size={24} />{p.symbol}<span className="text-xs text-sub">{CHAIN_LABEL[p.chain]}</span></Link></td>
                        <td className="num px-4 text-right">{p.qty.toLocaleString('en-US', { maximumFractionDigits: 2 })}</td>
                        <td className="num px-4 text-right">{usd(p.costUsd)}</td>
                        <td className="num px-4 text-right">{px != null ? fmtPrice(px) : <span className="text-sub">{loading ? '…' : '读不到'}</span>}</td>
                        <td className="num px-4 text-right">{val != null ? usd(val) : '--'}</td>
                        <td className={cx('num px-4 text-right', val != null && pnlTone(val - p.costUsd))}>{val != null ? `${signed(val - p.costUsd)}（${((val / p.costUsd - 1) * 100).toFixed(1)}%）` : '--'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
      </section>

      <section aria-label="模拟成交">
        <h2 className="mb-2 text-[16px] font-medium">模拟成交 <span className="num text-[12px] font-normal text-sub">{paper.trades.length}</span></h2>
        {paper.trades.length === 0
          ? <p className="rounded-xl border border-line p-6 text-center text-[13px] text-sub">还没有模拟成交。</p>
          : (
            <div className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full min-w-[760px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-b border-line">
                  {['时间', '代币', '方向', '金额', '成交均价', '手续费', '实现盈亏'].map((h, i) => <th key={h} className={cx('px-4 py-2.5 font-normal', i > 2 ? 'text-right' : 'text-left')}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {paper.trades.slice(0, 200).map((x) => (
                    <tr key={x.id} className="border-b border-line last:border-0">
                      <td className="num px-4 py-2 text-xs text-sub">{new Date(x.time).toLocaleString('zh-CN', { hour12: false })}</td>
                      <td className="px-4"><Link to={`/token/${x.address}`} className="hover:underline">{x.symbol}</Link></td>
                      <td className={cx('px-4', x.side === 'BUY' ? 'text-up' : 'text-down')}>{x.side === 'BUY' ? '买入' : '卖出'}{x.by === 'agent' && <span className="ml-1 text-xs text-agent">Agent</span>}</td>
                      <td className="num px-4 text-right">{usd(x.usd)}</td>
                      <td className="num px-4 text-right">{fmtPrice(x.fillUsd)}</td>
                      <td className="num px-4 text-right text-sub">{usd(x.feeUsd)}</td>
                      <td className={cx('num px-4 text-right', x.realizedUsd != null && pnlTone(x.realizedUsd))}>{x.realizedUsd != null ? signed(x.realizedUsd) : '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>

      <section aria-label="重置模拟盘" className="rounded-xl border border-line p-4">
        <h2 className="text-[15px] font-medium">重新开始</h2>
        <p className="mt-1 text-[12px] text-sub">清空模拟持仓和成交记录，用新的初始资金重新开始。开始于 {new Date(paper.startedAt).toLocaleDateString('zh-CN')}，初始资金 {usd(paper.startUsd)}。</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Segmented label="初始资金" size="sm" value={String(start)} onChange={(x) => setStart(Number(x))} options={PAPER_START_OPTIONS.map((n) => ({ value: String(n), label: usd(n, { compact: true }) }))} />
          {confirm
            ? <>
              <Button variant="danger" className="h-9 px-3 py-0 text-[13px]" onClick={() => { paper.reset(start); setConfirm(false) }}>确认清空并重新开始</Button>
              <Button variant="ghost" className="h-9 px-3 py-0 text-[13px]" onClick={() => setConfirm(false)}>取消</Button>
            </>
            : <Button variant="outline" className="h-9 px-3 py-0 text-[13px]" onClick={() => setConfirm(true)}>重新开始</Button>}
        </div>
      </section>
    </div>
  )
}
