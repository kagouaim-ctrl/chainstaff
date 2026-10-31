import { useMemo, useState } from 'react'
import { useOrders } from '@/api/hooks'
import { useDrawerLink } from '@/app/links'
import { DataState, Panel, Tag, TokenAvatar, ModeTag, cx } from '@/components/ui'
import { STATUS_LABEL, STATUS_TONE } from '@/domain/orderMachine'
import { usd, price, clock } from '@/lib/format'
import type { OrderStatus } from '@/domain/types'

const GROUPS: { key: string; label: string; match: OrderStatus[] | null }[] = [
  { key: 'all', label: '全部', match: null },
  { key: 'open', label: '进行中', match: ['CREATED', 'VALIDATING', 'READY', 'SUBMITTED', 'PENDING'] },
  { key: 'unknown', label: '状态未知', match: ['UNKNOWN'] },
  { key: 'done', label: '已确认', match: ['CONFIRMED'] },
  { key: 'bad', label: '拒绝 / 失败 / 过期', match: ['REJECTED', 'FAILED', 'EXPIRED'] },
]

export function Orders() {
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
