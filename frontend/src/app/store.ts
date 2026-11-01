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

export const useApp = create<AppState>((set) => ({
  paused: false,
  pausedAt: null,
  pause: () => set({ paused: true, pausedAt: new Date().toISOString() }),
  resume: () => set({ paused: false, pausedAt: null }),
  searchOpen: false,
  setSearchOpen: (v) => set({ searchOpen: v }),
  watch: {},
  toggleWatch: (a) => set((s) => ({ watch: { ...s.watch, [a]: !s.watch[a] } })),
}))

interface ToastState { msg: string | null; show: (m: string) => void; clear: () => void }
let toastTimer: ReturnType<typeof setTimeout> | undefined
export const useToast = create<ToastState>((set) => ({
  msg: null,
  // 连续提示时重新计时，避免上一条的定时器把新提示提前关掉
  show: (m) => { clearTimeout(toastTimer); set({ msg: m }); toastTimer = setTimeout(() => set({ msg: null }), 4000) },
  clear: () => set({ msg: null }),
}))
