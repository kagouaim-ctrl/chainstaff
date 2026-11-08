import { useMemo, useState } from 'react'
import { useOrders } from '@/api/hooks'
import { useDrawerLink } from '@/app/links'
import { DataState, Panel, Tag, TokenAvatar, ModeTag, cx } from '@/components/ui'
import { STATUS_LABEL, STATUS_TONE } from '@/domain/orderMachine'
import { usd, price, clock } from '@/lib/format'
import type { Chain, OrderStatus } from '@/domain/types'
import { Link } from 'react-router-dom'
import { LIVE } from '@/api/live'
import { CHAIN_LABEL } from '@/api/chain'
import { readTrades, type MyTrade } from '@/wallet/history'
import { txUrl } from '@/wallet/chains'
import { shortAddr } from '@/lib/format'

const GROUPS: { key: string; label: string; match: OrderStatus[] | null }[] = [
  { key: 'all', label: '全部', match: null },
  { key: 'open', label: '进行中', match: ['CREATED', 'VALIDATING', 'READY', 'SUBMITTED', 'PENDING'] },
  { key: 'unknown', label: '状态未知', match: ['UNKNOWN'] },
  { key: 'done', label: '已确认', match: ['CONFIRMED'] },
  { key: 'bad', label: '拒绝 / 失败 / 过期', match: ['REJECTED', 'FAILED', 'EXPIRED'] },
]

/** 实时模式：先列出在本设备通过 KAGOUAI 确认上链的真实交易，下面保留原来的交易记录。 */
export function Orders() {
  if (!LIVE) return <OrderList />
  return (
    <div className="space-y-6">
      <MyTradesPanel />
      <OrderList />
    </div>
  )
}

const csvCell = (v: unknown) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
export function tradesCsv(rows: MyTrade[]): string {
  const head = ['时间', '链', '方向', '代币', '合约地址', '付出', '付出单位', '得到（报价）', '得到单位', '美元价值（报价）', '交易哈希']
  const body = rows.map((t) => [new Date(t.time).toISOString(), CHAIN_LABEL[t.chain] ?? t.chain, t.side === 'BUY' ? '买入' : '卖出', t.symbol, t.address,
    t.inAmount, t.inSymbol, t.outAmount, t.outSymbol, t.usd ?? '', t.id].map(csvCell).join(','))
  return [head.join(','), ...body].join('\n')
}

export function filterTrades(rows: MyTrade[], chain: Chain | 'all', side: 'all' | 'BUY' | 'SELL'): MyTrade[] {
  return rows.filter((t) => (chain === 'all' || t.chain === chain) && (side === 'all' || t.side === side))
}

function MyTradesPanel() {
  const [rows] = useState(readTrades)
  const [chain, setChain] = useState<Chain | 'all'>('all')
  const [side, setSide] = useState<'all' | 'BUY' | 'SELL'>('all')
  const list = filterTrades(rows, chain, side)
  const chains = [...new Set(rows.map((t) => t.chain))]
  const download = () => {
    const url = URL.createObjectURL(new Blob(['\ufeff' + tradesCsv(list)], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a'); a.href = url; a.download = `kagouai-trades-${new Date().toISOString().slice(0, 10)}.csv`; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const pill = (on: boolean) => cx('whitespace-nowrap rounded-lg border px-2.5 py-1 text-xs', on ? 'border-fg/20 bg-raised text-fg' : 'border-line text-sub hover:text-fg')
  return (
    <Panel title="我的成交（本设备）" bodyClass="p-0"
      right={rows.length > 0 && <button onClick={download} className="text-xs text-agent hover:underline">导出 CSV</button>}>
      <p className="px-4 pt-1 text-[12px] text-sub">通过 KAGOUAI 买卖面板确认上链的真实交易。记录只存在这台设备的浏览器里，换设备或清缓存会看不到；金额是下单前的报价，实际成交以区块浏览器为准。</p>
      {rows.length > 0 && (
        <div className="scrollbar-none flex gap-1.5 overflow-x-auto border-b border-line px-4 py-3">
          <button onClick={() => setChain('all')} aria-pressed={chain === 'all'} className={pill(chain === 'all')}>全部链 <span className="num text-sub">{rows.length}</span></button>
          {chains.map((c) => <button key={c} onClick={() => setChain(c)} aria-pressed={chain === c} className={pill(chain === c)}>{CHAIN_LABEL[c]} <span className="num text-sub">{rows.filter((t) => t.chain === c).length}</span></button>)}
          <span className="mx-1 w-px bg-line" />
          {([['all', '买入和卖出'], ['BUY', '买入'], ['SELL', '卖出']] as const).map(([k, l]) => <button key={k} onClick={() => setSide(k)} aria-pressed={side === k} className={pill(side === k)}>{l}</button>)}
        </div>
      )}
      {rows.length === 0
        ? <p className="p-6 text-center text-[13px] text-sub">这台设备上还没有成交。在代币页的买卖面板或「快速买入」确认上链后，会记在这里。</p>
        : list.length === 0 ? <p className="p-6 text-center text-[13px] text-sub">没有符合筛选条件的成交。</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-b border-line">
                  {['时间', '代币', '方向', '付出', '得到（报价）', '价值（报价）', ''].map((h, i) => <th key={i} className={`px-4 py-2.5 font-normal ${i > 2 ? 'text-right' : 'text-left'}`}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {list.map((t) => (
                    <tr key={t.id} className="border-b border-line last:border-0 hover:bg-raised">
                      <td className="num px-4 py-2.5 text-xs text-sub">{new Date(t.time).toLocaleString('zh-CN', { hour12: false })}</td>
                      <td className="px-4"><Link to={`/token/${t.address}`} className="hover:underline">{t.symbol || shortAddr(t.address, 4, 4)}</Link> <span className="text-xs text-sub">{CHAIN_LABEL[t.chain]}</span></td>
                      <td className={cx('px-4', t.side === 'BUY' ? 'text-up' : 'text-down')}>{t.side === 'BUY' ? '买入' : '卖出'}</td>
                      <td className="num px-4 text-right">{t.inAmount ? `${t.inAmount} ${t.inSymbol ?? ''}` : <span className="text-sub">--</span>}</td>
                      <td className="num px-4 text-right">{t.outAmount ? `${t.outAmount} ${t.outSymbol ?? ''}` : <span className="text-sub">--</span>}</td>
                      <td className="num px-4 text-right">{t.usd != null ? usd(t.usd) : <span className="text-sub">--</span>}</td>
                      <td className="px-4 text-right"><a href={txUrl(t.chain, t.id)} target="_blank" rel="noreferrer" className="text-xs text-agent hover:underline">区块浏览器</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
    </Panel>
  )
}

/** 原来的交易记录（订单状态机），保持不变 */
function OrderList() {
  const q = useOrders()
  const open = useDrawerLink()
  const [g, setG] = useState('all')
  const grp = GROUPS.find((x) => x.key === g)!
  const list = useMemo(() => (q.data ?? []).filter((o) => !grp.match || grp.match.includes(o.status)), [q.data, grp])
  return (
    <Panel demo title="交易记录" bodyClass="p-0">
      <div className="scrollbar-none flex gap-1.5 overflow-x-auto border-b border-line px-4 py-3">
        {GROUPS.map((x) => {
          const n = (q.data ?? []).filter((o) => !x.match || x.match.includes(o.status)).length
          return (
            <button key={x.key} onClick={() => setG(x.key)} aria-pressed={g === x.key}
              className={cx('whitespace-nowrap rounded-lg border px-2.5 py-1 text-xs', g === x.key ? 'border-fg/20 bg-raised text-fg' : 'border-line text-sub hover:text-fg')}>
              {x.label} <span className="num text-sub">{n}</span>
            </button>
          )
        })}
      </div>
      <DataState q={q} rows={6} isEmpty={() => list.length === 0} empty="没有符合条件的订单。">
        {() => (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-[13px]">
              <thead className="text-xs text-sub"><tr className="border-b border-line">
                {['订单', '代币', '方向', '请求金额', '参考价', '成交价', '状态', '时间'].map((h, i) => <th key={h} className={`px-4 py-2.5 font-normal ${i > 2 ? 'text-right' : 'text-left'}`}>{h}</th>)}
              </tr></thead>
              <tbody>
                {list.map((o) => (
                  <tr key={o.id} onClick={() => open('order', o.id)} className="cursor-pointer border-b border-line last:border-0 hover:bg-raised">
                    <td className="px-4 py-3 font-mono text-xs"><button onClick={(e) => { e.stopPropagation(); open('order', o.id) }} className="hover:text-agent">{o.id}</button></td>
                    <td className="px-4"><span className="flex items-center gap-2"><TokenAvatar symbol={o.token.symbol} hue={o.token.hue} size={24} />{o.token.symbol}<ModeTag mode={o.mode} /></span></td>
                    <td className={cx('px-4', o.side === 'BUY' ? 'text-up' : 'text-down')}>{o.side === 'BUY' ? '买入' : '卖出'}</td>
                    <td className="num px-4 text-right">{usd(o.requestedUsd)}</td>
                    <td className="num px-4 text-right">{price(o.referencePrice)}</td>
                    <td className="num px-4 text-right">{o.fillPrice ? price(o.fillPrice) : <span className="text-sub">--</span>}</td>
                    <td className="px-4 text-right"><Tag tone={STATUS_TONE[o.status]}>{STATUS_LABEL[o.status]}</Tag></td>
                    <td className="num px-4 text-right text-xs text-sub">{clock(o.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataState>
    </Panel>
  )
}
