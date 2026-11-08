import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { create } from 'zustand'
import { useQuery } from '@tanstack/react-query'
import { prices, CHAIN_LABEL } from '@/api/chain'
import { isDemo } from '@/api/hooks'
import { useToast } from '@/app/store'
import { Button, Dialog, cx } from '@/components/ui'
import { price as fmtPrice, shortAddr } from '@/lib/format'
import { dataBad } from './tokenBits'
import type { Chain, Token } from '@/domain/types'

/**
 * 价格提醒。只存在这个浏览器里，页面开着时每 30 秒用真实行情（主交易对价格）检查一次，
 * 触发一次后就停，不会反复打扰。数据异常（被判为不可信）的价格不会触发提醒。
 * 关掉页面就不检查了：这是浏览器提醒，不是服务器推送，页面上写明了。
 */
export type AlertKind = 'above' | 'below' | 'up_pct' | 'down_pct'
export interface PriceAlert {
  id: string; chain: Chain; address: string; symbol: string
  kind: AlertKind; value: number
  /** 建提醒时的价格，涨跌幅提醒以它为基准 */
  basePrice: number
  createdAt: number; firedAt: number | null; firedPrice: number | null
}

export const KIND_TEXT: Record<AlertKind, string> = { above: '价格高于', below: '价格低于', up_pct: '上涨超过', down_pct: '下跌超过' }
const isPct = (k: AlertKind) => k === 'up_pct' || k === 'down_pct'

/** 达到条件时返回 true。价格未知时不触发。 */
export function hits(a: PriceAlert, p: number | null): boolean {
  if (p == null || !(p > 0)) return false
  switch (a.kind) {
    case 'above': return p >= a.value
    case 'below': return p <= a.value
    case 'up_pct': return p >= a.basePrice * (1 + a.value / 100)
    case 'down_pct': return p <= a.basePrice * (1 - a.value / 100)
  }
}

export function describe(a: PriceAlert): string {
  return isPct(a.kind) ? `${KIND_TEXT[a.kind]} ${a.value}%（基准 ${fmtPrice(a.basePrice)}）` : `${KIND_TEXT[a.kind]} ${fmtPrice(a.value)}`
}

const KEY = 'kagouai.alerts.v1'
const read = (): PriceAlert[] => { try { const v = JSON.parse(localStorage.getItem(KEY) ?? '[]'); return Array.isArray(v) ? v : [] } catch { return [] } }
const save = (v: PriceAlert[]) => { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* 隐私模式不记 */ } }

interface AlertState {
  alerts: PriceAlert[]
  add: (a: Omit<PriceAlert, 'id' | 'createdAt' | 'firedAt' | 'firedPrice'>) => void
  remove: (id: string) => void
  fire: (id: string, p: number) => void
}
export const useAlerts = create<AlertState>((set, get) => ({
  alerts: read(),
  add: (a) => { const alerts = [{ ...a, id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, createdAt: Date.now(), firedAt: null, firedPrice: null }, ...get().alerts].slice(0, 100); save(alerts); set({ alerts }) },
  remove: (id) => { const alerts = get().alerts.filter((a) => a.id !== id); save(alerts); set({ alerts }) },
  fire: (id, p) => { const alerts = get().alerts.map((a) => a.id === id ? { ...a, firedAt: Date.now(), firedPrice: p } : a); save(alerts); set({ alerts }) },
}))

function notify(title: string, body: string) {
  try { if ('Notification' in window && Notification.permission === 'granted') new Notification(title, { body }) } catch { /* 有的浏览器不支持 */ }
}

/** 挂在全局布局里：有未触发的提醒时，按链批量查价格。 */
export function AlertRunner() {
  const { alerts, fire } = useAlerts()
  const toast = useToast((s) => s.show)
  const active = alerts.filter((a) => a.firedAt == null)
  const chains = [...new Set(active.map((a) => a.chain))]
  const key = active.map((a) => a.id).join(',')
  const q = useQuery({
    queryKey: ['alertPrices', key],
    queryFn: async () => {
      const out = new Map<string, number | null>()
      for (const c of chains) {
        const rows = await prices(c, [...new Set(active.filter((a) => a.chain === c).map((a) => a.address))])
        for (const [addr, r] of rows) out.set(`${c}:${addr}`, r.bad ? null : r.priceUsd)
      }
      return out
    },
    enabled: !isDemo() && active.length > 0, refetchInterval: 30_000, staleTime: 0,
  })
  useEffect(() => {
    if (!q.data) return
    for (const a of active) {
      const p = q.data.get(`${a.chain}:${a.address}`) ?? null
      if (p != null && hits(a, p)) {
        fire(a.id, p)
        const msg = `${a.symbol} ${describe(a)}，现价 ${fmtPrice(p)}`
        toast(`价格提醒：${msg}`)
        notify('KAGOUAI 价格提醒', msg)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在新价格到达时检查
  }, [q.data])
  return null
}

/** 代币页的「提醒」按钮和对话框 */
export function AlertButton({ t, className }: { t: Token; className?: string }) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<AlertKind>('above')
  const [value, setValue] = useState('')
  const add = useAlerts((s) => s.add)
  const toast = useToast((s) => s.show)
  const count = useAlerts((s) => s.alerts.filter((a) => a.address === t.address && a.firedAt == null).length)
  const bad = dataBad(t)
  const v = Number(value)
  const valid = v > 0 && (!isPct(kind) || v < (kind === 'down_pct' ? 100 : 100_000))
  const submit = () => {
    if (!valid || bad) return
    add({ chain: t.chain, address: t.address, symbol: t.symbol, kind, value: v, basePrice: t.priceUsd })
    try { if ('Notification' in window && Notification.permission === 'default') void Notification.requestPermission() } catch { /* 忽略 */ }
    toast(`已添加提醒：${t.symbol} ${isPct(kind) ? `${KIND_TEXT[kind]} ${v}%` : `${KIND_TEXT[kind]} ${fmtPrice(v)}`}`)
    setOpen(false); setValue('')
  }
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="价格提醒" title={bad ? '数据异常，不能设提醒' : '价格提醒'} disabled={bad}
        className={cx('flex h-7 items-center gap-1 rounded-md px-1.5 text-[12px] hover:bg-raised hover:text-fg disabled:opacity-40', className)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
        {count > 0 && <span className="num">{count}</span>}
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title={`${t.symbol} 价格提醒`}
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>取消</Button><Button variant="primary" disabled={!valid} onClick={submit}>添加</Button></>}>
        <p className="text-[13px] text-sub">现价 {fmtPrice(t.priceUsd)}（主交易对参考价）</p>
        <div className="mt-3 flex gap-2">
          <select aria-label="提醒条件" value={kind} onChange={(e) => setKind(e.target.value as AlertKind)} className="h-10 rounded-lg border border-line bg-bg px-2">
            {(Object.keys(KIND_TEXT) as AlertKind[]).map((k) => <option key={k} value={k}>{KIND_TEXT[k]}</option>)}
          </select>
          <input aria-label={isPct(kind) ? '百分比' : '价格（美元）'} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value.trim())}
            placeholder={isPct(kind) ? '例如 50（%）' : '价格，美元'} className="num h-10 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3" />
        </div>
        <p className="mt-3 text-[12px] text-sub">提醒只保存在这个浏览器里，页面开着时每 30 秒检查一次，触发一次后停止。浏览器允许通知时会弹系统通知。</p>
      </Dialog>
    </>
  )
}

/** 持仓页里的提醒列表 */
export function AlertList() {
  const { alerts, remove } = useAlerts()
  return (
    <section aria-label="价格提醒">
      <h2 className="text-[16px] font-medium">价格提醒</h2>
      {alerts.length === 0
        ? <p className="mt-2 text-[13px] text-sub">还没有。在代币页点铃铛图标添加，提醒只保存在这个浏览器里。</p>
        : (
          <ul className="mt-2 divide-y divide-line">
            {alerts.map((a) => (
              <li key={a.id} className="flex min-h-11 flex-wrap items-center gap-3 py-1 text-[13px]">
                <Link to={`/token/${a.address}`} className="hover:underline">{a.symbol}</Link>
                <span className="text-sub">{CHAIN_LABEL[a.chain]} · {shortAddr(a.address, 4, 4)}</span>
                <span>{describe(a)}</span>
                {a.firedAt
                  ? <span className="rounded bg-up/10 px-1.5 text-[12px] text-up">已触发 {new Date(a.firedAt).toLocaleString('zh-CN', { hour12: false })}{a.firedPrice != null && ` · ${fmtPrice(a.firedPrice)}`}</span>
                  : <span className="rounded bg-raised px-1.5 text-[12px] text-sub">等待中</span>}
                <button className="ml-auto text-[12px] text-sub hover:text-down" onClick={() => remove(a.id)}>删除</button>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}
