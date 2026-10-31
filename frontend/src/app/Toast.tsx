import { useToast } from './store'
export function Toast() {
  const msg = useToast((s) => s.msg)
  if (!msg) return null
  return <div role="status" className="anim-enter fixed bottom-4 left-1/2 z-[60] w-[min(92vw,480px)] -translate-x-1/2 rounded-xl border border-line bg-raised px-4 py-3 text-[13px] shadow-2xl">{msg}</div>
}
