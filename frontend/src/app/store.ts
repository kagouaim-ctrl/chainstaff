import { create } from 'zustand'

interface AppState {
  /** 紧急暂停。后端未接入时只在本页生效，界面会说明。 */
  paused: boolean
  pausedAt: string | null
  pause: () => void
  resume: () => void
  searchOpen: boolean
  setSearchOpen: (v: boolean) => void
  watch: Record<string, boolean>
  toggleWatch: (a: string) => void
}

const WATCH_KEY = 'kagouai.watch.v1'
function readWatch(): Record<string, boolean> {
  try { const v = JSON.parse(localStorage.getItem(WATCH_KEY) ?? '{}'); return v && typeof v === 'object' ? v : {} } catch { return {} }
}

export const useApp = create<AppState>((set) => ({
  paused: false,
  pausedAt: null,
  pause: () => set({ paused: true, pausedAt: new Date().toISOString() }),
  resume: () => set({ paused: false, pausedAt: null }),
  searchOpen: false,
  setSearchOpen: (v) => set({ searchOpen: v }),
  // 自选存在浏览器本地，刷新和下次打开都还在
  watch: readWatch(),
  toggleWatch: (a) => set((s) => {
    const watch = { ...s.watch }
    if (watch[a]) delete watch[a]; else watch[a] = true
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(watch)) } catch { /* 隐私模式不记 */ }
    return { watch }
  }),
}))

interface ToastState { msg: string | null; show: (m: string) => void; clear: () => void }
let toastTimer: ReturnType<typeof setTimeout> | undefined
export const useToast = create<ToastState>((set) => ({
  msg: null,
  // 连续提示时重新计时，避免上一条的定时器把新提示提前关掉
  show: (m) => { clearTimeout(toastTimer); set({ msg: m }); toastTimer = setTimeout(() => set({ msg: null }), 4000) },
  clear: () => set({ msg: null }),
}))
