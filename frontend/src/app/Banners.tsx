import { useApp } from './store'
import { useOrders } from '@/api/hooks'
import { TERMINAL } from '@/domain/orderMachine'
import { IAlert } from '@/components/icons'

export function DemoBanner() {
  return (
    <div role="note" className="border-b border-line bg-panel/60">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-xs text-sub sm:px-5">
        <span className="rounded border border-dashed border-sub/60 px-1.5 py-0.5 font-medium text-fg/80">演示数据</span>
        <span>后端未接入：页面上的行情、Agent 状态、活动和交易都是固定的演示样例，不代表真实行情或真实交易，也不会实时变化。</span>
        <span className="text-sub/80">实时连接：未接入</span>
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
      <div className="mx-auto flex max-w-[1600px] items-center gap-2 px-3 py-2 text-[13px] text-down sm:px-5">
        <IAlert />
        <span>已暂停，已提交的 <b className="num">{n}</b> 笔交易仍在追踪确认。<span className="text-down/70">（演示：仅本页生效）</span></span>
      </div>
    </div>
  )
}
