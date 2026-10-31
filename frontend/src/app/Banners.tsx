import { useApp } from './store'
import { useOrders } from '@/api/hooks'
import { TERMINAL } from '@/domain/orderMachine'
import { IAlert } from '@/components/icons'

export function DemoBanner() {
  return (
    <div role="note" className="border-b border-line">
      <div className="flex items-center gap-2 px-4 py-2 text-[12px] font-normal text-sub">
        <span className="shrink-0 font-medium text-warn">演示数据</span>
        <span className="truncate">后端未接入，行情、Agent、群聊和交易都是固定样例，不是真实数据，也不会变化。</span>
      </div>
    </div>
  )
}

export function PauseBanner() {
  const paused = useApp((s) => s.paused)
  const orders = useOrders()
  if (!paused) return null
  const n = orders.data?.filter((o) => !TERMINAL.includes(o.status) && o.status !== 'CREATED').length ?? 0
  return (
    <div role="status" className="anim-enter border-b border-down/30 bg-down/10">
      <div className="flex items-center gap-2 px-4 py-2 text-[13px] text-down">
        <IAlert />
        <span>已暂停，已提交的 <b className="num">{n}</b> 笔交易仍在追踪确认。<span className="text-down/70">（演示：仅本页生效）</span></span>
      </div>
    </div>
  )
}
