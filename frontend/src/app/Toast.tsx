import { useToast } from './store'
import { IAlert, IClose } from '@/components/icons'

export function Toast() {
  const { msg, clear } = useToast()
  if (!msg) return null
  const warn = msg.startsWith('演示模式')
  return (
    <div role="status" className="anim-enter fixed bottom-20 left-1/2 z-[60] flex w-[min(92vw,440px)] -translate-x-1/2 items-start gap-2.5 rounded-xl border border-line bg-raised px-4 py-3 text-[13px] font-normal leading-5 shadow-[0_8px_32px_rgba(0,0,0,.5)] md:bottom-12">
      {warn && <IAlert className="mt-0.5 shrink-0 text-warn" />}
      <span className="flex-1">{msg}</span>
      <button onClick={clear} aria-label="关闭提示" className="-mr-1 shrink-0 rounded p-0.5 text-sub hover:text-fg"><IClose width={14} height={14} /></button>
    </div>
  )
}
