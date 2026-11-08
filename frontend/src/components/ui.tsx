import clsx from 'clsx'
import { errText } from '@/lib/format'
import { useEffect, useId, useRef, type ReactNode, useState } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { AgentStatus, Mode } from '@/domain/types'
import { pct as fmtPct } from '@/lib/format'
import { IClose } from './icons'

export const cx = clsx

/** 演示标签：任何使用演示数据的模块都必须显示。 */
export function DemoTag(props: { className?: string }) {
  void props
  // 照 fomo 不在每个面板挂标签；演示标识统一放在顶部横幅和底部状态栏（铁律 8）
  return null
}

const MODE_STYLE: Record<Mode, string> = {
  paper: 'text-paper bg-paper/10',
  live: 'text-live bg-live/10',
  backtest: 'text-sub bg-raised',
}
export const MODE_LABEL: Record<Mode, string> = { paper: '模拟', live: '实盘', backtest: '回测' }

export function ModeTag({ mode, className }: { mode: Mode; className?: string }) {
  return <span className={cx('inline-flex h-[18px] items-center rounded px-1 text-[11px] leading-none', MODE_STYLE[mode], className)}>{MODE_LABEL[mode]}</span>
}

type Tone = 'up' | 'down' | 'warn' | 'sub' | 'agent' | 'paper'
const TONE: Record<Tone, string> = {
  up: 'text-up bg-up/10', down: 'text-down bg-down/10', warn: 'text-warn bg-warn/10', sub: 'text-sub bg-raised', agent: 'text-agent bg-agent/10', paper: 'text-paper bg-paper/10',
}
export function Tag({ tone = 'sub', children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium', TONE[tone], className)}>{children}</span>
}

export const AGENT_STATUS: Record<AgentStatus, { label: string; dot: string }> = {
  running: { label: '运行中', dot: 'bg-up' },
  idle: { label: '空闲', dot: 'bg-sub' },
  degraded: { label: '降级', dot: 'bg-warn' },
  paused: { label: '已暂停', dot: 'bg-paper' },
  error: { label: '错误', dot: 'bg-down' },
}
export function StatusDot({ status, pulse }: { status: AgentStatus; pulse?: boolean }) {
  return <span aria-hidden className={cx('inline-block h-2 w-2 shrink-0 rounded-full', AGENT_STATUS[status].dot, pulse && status === 'running' && 'anim-pulse')} />
}

export function Pct({ v, className, digits }: { v: number | null | undefined; className?: string; digits?: number }) {
  const c = v == null ? 'text-sub' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-sub'
  return <span className={cx('num', c, className)}>{fmtPct(v, digits)}</span>
}

/** 带 ▲▼ 的涨跌幅（照 fomo）。 */
export function Chg({ v, className }: { v: number; className?: string }) {
  return <span className={cx('num whitespace-nowrap', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-sub', className)}>{v > 0 ? '▲' : v < 0 ? '▼' : ''}{Math.abs(v).toFixed(2)}%</span>
}

export function Signed({ v, children, className }: { v: number; children: ReactNode; className?: string }) {
  return <span className={cx('num', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-sub', className)}>{children}</span>
}

const hash = (str: string) => { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) } return h >>> 0 }

/** 代币头像：没有真实 logo 时，按符号生成固定的渐变图形（同一个代币永远一样），不用首字母。 */
export function TokenAvatar({ symbol, hue, size = 32, image }: { symbol: string; hue: number; size?: number; image?: string | null }) {
  const [broken, setBroken] = useState(false)
  const id = `ta${useId().replace(/:/g, '')}`
  if (image && !broken && /^https:\/\//i.test(image)) {
    return <img src={image} alt="" width={size} height={size} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} className="shrink-0 rounded-full bg-raised object-cover" style={{ width: size, height: size }} />
  }
  const h = hash(symbol)
  const h2 = (hue + 40 + (h % 80)) % 360
  const h3 = (hue + 180 + ((h >> 8) % 60)) % 360
  const x1 = 20 + ((h >> 4) % 60), y1 = 20 + ((h >> 10) % 60)
  const x2 = 20 + ((h >> 16) % 60), y2 = 20 + ((h >> 22) % 60)
  // 同一个头像可能在页面上出现多次（如桌面表格和手机列表），id 必须唯一（上面的 useId），否则隐藏的那份会让可见的渐变失效
  return (
    <svg aria-hidden width={size} height={size} viewBox="0 0 100 100" className="shrink-0 rounded-full">
      <defs>
        <radialGradient id={`${id}-a`} cx={`${x1}%`} cy={`${y1}%`} r="75%"><stop offset="0" stopColor={`hsl(${h2} 85% 66%)`} /><stop offset="1" stopColor={`hsl(${hue} 70% 34%)`} /></radialGradient>
        <filter id={`${id}-b`}><feGaussianBlur stdDeviation="9" /></filter>
        <clipPath id={`${id}-c`}><circle cx="50" cy="50" r="50" /></clipPath>
      </defs>
      <g clipPath={`url(#${id}-c)`}>
        <rect width="100" height="100" fill={`url(#${id}-a)`} />
        <circle cx={x2} cy={y2} r={22 + (h % 14)} fill={`hsl(${h3} 80% 60% / .55)`} filter={`url(#${id}-b)`} />
      </g>
      <circle cx="50" cy="50" r="49.5" fill="none" stroke="rgba(255,255,255,.08)" />
    </svg>
  )
}

export function Panel({ title, right, children, className, demo, bodyClass, id }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; demo?: boolean; bodyClass?: string; id?: string
}) {
  return (
    <section id={id} className={cx('min-w-0 rounded-xl border border-line', className)}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-4 pt-4">
          <h2 className="flex items-center gap-2 text-[16px] font-medium">{title}{demo && <DemoTag />}</h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className={cx(bodyClass ?? 'p-4')}>{children}</div>
    </section>
  )
}

export function Segmented<T extends string>({ value, options, onChange, label, size = 'md' }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string; size?: 'sm' | 'md'
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex gap-1">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('whitespace-nowrap rounded-lg', size === 'sm' ? 'h-7 px-2 text-[12px]' : 'h-9 px-3 text-[14px]',
            value === o.value ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Tabs<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="scrollbar-none flex gap-6 overflow-x-auto border-b border-line px-4">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('relative -mb-px whitespace-nowrap border-b-2 py-3 text-[14px]',
            value === o.value ? 'border-fg text-fg' : 'border-transparent text-sub hover:text-fg')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Button({ children, variant = 'ghost', className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' | 'outline' | 'up' | 'down' }) {
  const v = {
    primary: 'bg-agent text-white hover:bg-agent/90',
    ghost: 'text-sub hover:text-fg hover:bg-raised',
    outline: 'border border-line text-fg hover:bg-raised',
    danger: 'bg-down/15 text-down border border-down/30 hover:bg-down/25',
    up: 'bg-up text-[#060510] hover:bg-up/90',
    down: 'bg-down text-white hover:bg-down/90',
  }[variant]
  return <button {...p} className={cx('inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[14px] font-medium disabled:cursor-not-allowed disabled:opacity-40', v, className)}>{children}</button>
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded bg-raised', className)} />
}

/** 每个数据模块的四种状态：加载、空、错误、数据源未接入。 */
export function DataState<T>({ q, empty, isEmpty, children, rows = 4, notConnected }: {
  q: UseQueryResult<T>; children: (d: T) => ReactNode; empty?: ReactNode; isEmpty?: (d: T) => boolean; rows?: number
  notConnected?: { what: string; how: string }
}) {
  if (notConnected) return <NotConnected {...notConnected} />
  if (q.isPending) return <div className="space-y-2 p-4" aria-busy="true" aria-label="加载中">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
  if (q.isError) return (
    <div role="alert" className="flex flex-col items-start gap-2 p-4 text-sm">
      <p className="text-down">加载失败：{errText(q.error)}</p>
      <p className="text-sub">可能是后端服务不可用或网络中断，数据显示的是最后一次成功获取的结果。</p>
      <Button variant="outline" onClick={() => q.refetch()}>重试</Button>
    </div>
  )
  const d = q.data as T
  if (d == null || (isEmpty && isEmpty(d))) return <div className="p-6 text-center text-sm text-sub">{empty ?? '暂无数据'}</div>
  return <>{children(d)}</>
}

export function NotConnected({ what, how }: { what: string; how: string }) {
  return (
    <div className="m-4 rounded-lg border border-dashed border-line p-4 text-sm">
      <p className="font-medium text-warn">未接入：{what}</p>
      <p className="mt-1 text-sub">{how}</p>
    </div>
  )
}

export function Drawer({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    ref.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}
        className={cx('anim-drawer absolute right-0 top-0 flex h-full w-full flex-col border-l border-line bg-panel shadow-2xl outline-none', wide ? 'sm:w-[720px]' : 'sm:w-[560px]')}>
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1 text-base font-medium">{title}</div>
          <Button aria-label="关闭" onClick={onClose} className="px-2"><IClose /></Button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  )
}

export function Dialog({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    ref.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className="anim-enter relative w-full max-w-md rounded-t-2xl border border-line bg-panel p-5 outline-none sm:rounded-2xl">
        <h2 className="text-base font-medium">{title}</h2>
        <div className="mt-3 text-sm text-fg/90">{children}</div>
        <div className="mt-5 flex justify-end gap-2">{footer}</div>
      </div>
    </div>
  )
}

export function KV({ k, v, className }: { k: ReactNode; v: ReactNode; className?: string }) {
  return (
    <div className={cx('flex items-baseline justify-between gap-3 py-1.5 text-[13px]', className)}>
      <span className="text-sub">{k}</span>
      <span className="num text-right">{v}</span>
    </div>
  )
}

export function Stat({ label, value, sub, className }: { label: ReactNode; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cx('min-w-0', className)}>
      <div className="text-xs text-sub">{label}</div>
      <div className="num mt-1 truncate text-[20px] font-medium tracking-[-0.4px] sm:text-[24px] sm:tracking-[-0.9px]">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-sub">{sub}</div>}
    </div>
  )
}
