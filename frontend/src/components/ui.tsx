import clsx from 'clsx'
import { useEffect, useRef, type ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { AgentStatus, Mode } from '@/domain/types'
import { pct as fmtPct } from '@/lib/format'
import { IClose } from './icons'

export const cx = clsx

/** 演示标签：任何使用演示数据的模块都必须显示。 */
export function DemoTag({ className }: { className?: string }) {
  return (
    <span title="演示数据：后端未接入，不代表真实行情或交易" className={cx('inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium leading-none border border-dashed border-sub/60 text-sub', className)}>
      演示
    </span>
  )
}

const MODE_STYLE: Record<Mode, string> = {
  paper: 'text-paper border-paper/40 bg-paper/10',
  live: 'text-live border-live/40 bg-live/10',
  backtest: 'text-sub border-line bg-raised',
}
export const MODE_LABEL: Record<Mode, string> = { paper: '模拟', live: '实盘', backtest: '回测' }

export function ModeTag({ mode, className }: { mode: Mode; className?: string }) {
  return <span className={cx('inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium leading-none border', MODE_STYLE[mode], className)}>{MODE_LABEL[mode]}</span>
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

export function Signed({ v, children, className }: { v: number; children: ReactNode; className?: string }) {
  return <span className={cx('num', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-sub', className)}>{children}</span>
}

export function TokenAvatar({ symbol, hue, size = 32 }: { symbol: string; hue: number; size?: number }) {
  return (
    <span aria-hidden className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white/90"
      style={{ width: size, height: size, fontSize: size * 0.38, background: `hsl(${hue} 45% 32%)`, boxShadow: `inset 0 0 0 1px hsl(${hue} 50% 45% / .5)` }}>
      {symbol.slice(0, 2)}
    </span>
  )
}

export function Panel({ title, right, children, className, demo, bodyClass, id }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; demo?: boolean; bodyClass?: string; id?: string
}) {
  return (
    <section id={id} className={cx('min-w-0 rounded-card border border-line bg-panel', className)}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">{title}{demo && <DemoTag />}</h2>
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
    <div role="tablist" aria-label={label} className="inline-flex rounded-lg border border-line bg-bg p-0.5">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('rounded-md font-medium transition-colors', size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-[13px]',
            value === o.value ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Tabs<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="scrollbar-none flex gap-5 overflow-x-auto border-b border-line px-4">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('relative -mb-px whitespace-nowrap border-b-2 py-3 text-[13px] font-medium transition-colors',
            value === o.value ? 'border-agent text-fg' : 'border-transparent text-sub hover:text-fg')}>
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
    up: 'bg-up text-[#06140f] hover:bg-up/90',
    down: 'bg-down text-white hover:bg-down/90',
  }[variant]
  return <button {...p} className={cx('inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40', v, className)}>{children}</button>
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
      <p className="text-down">加载失败：{(q.error as Error)?.message ?? '未知错误'}</p>
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
          <div className="min-w-0 flex-1 text-base font-semibold">{title}</div>
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
        <h2 className="text-base font-semibold">{title}</h2>
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
      <div className="num mt-1 truncate text-xl font-semibold tracking-tight sm:text-2xl">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-sub">{sub}</div>}
    </div>
  )
}
