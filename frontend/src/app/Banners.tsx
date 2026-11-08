import { useApp } from './store'
import { isDemo, useOrders } from '@/api/hooks'
import { AGENTS_ONLINE } from '@/api/live'
import { TERMINAL } from '@/domain/orderMachine'
import { IAlert } from '@/components/icons'

export function DemoBanner() {
  if (!isDemo()) return (
    <div role="note" className="border-b border-line">
      <div className="flex items-center gap-2 px-4 py-2 text-[12px] font-normal text-sub">
        <span className="shrink-0 font-medium text-up">实时数据</span>
        <span className="truncate">{AGENTS_ONLINE ? '行情和安全检查来自公开链上数据，Agent 分析只出建议、不会下单。买卖是真实交易，由你的钱包签名。' : '行情和安全检查来自公开链上数据。买卖是真实交易，由你的钱包签名；AI Agent 内测中。'}</span>
      </div>
    </div>
  )
  return (
    <div role="note" className="border-b border-line">
      <div className="flex items-center gap-2 px-4 py-2 text-[12px] font-normal text-sub">
        <span className="shrink-0 font-medium text-warn">演示数据</span>
        <span className="truncate">这是演示模式：行情、Agent、群聊和交易都是固定样例，不是真实数据，也不会变化。</span>
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
        <span>已暂停，已提交的 <b className="num">{n}</b> 笔交易仍在追踪确认。<span className="text-down/70">（{isDemo() ? '演示：' : ''}仅本页生效）</span></span>
      </div>
    </div>
  )
}
