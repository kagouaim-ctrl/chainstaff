import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from './store'
import { useAgents, useOrders, useTokens } from '@/api/hooks'
import { ISearch } from '@/components/icons'
import { TokenAvatar, cx } from '@/components/ui'
import { shortAddr } from '@/lib/format'
import type { AgentInfo, Order, Token } from '@/domain/types'

export type Hit =
  | { kind: 'token'; key: string; title: string; sub: string; token: Token }
  | { kind: 'agent'; key: string; title: string; sub: string; agent: AgentInfo }
  | { kind: 'order'; key: string; title: string; sub: string; order: Order }

export function searchAll(q: string, tokens: Token[], agents: AgentInfo[], orders: Order[]): Hit[] {
  const s = q.trim().toLowerCase()
  if (!s) return []
  const hits: Hit[] = []
  for (const t of tokens) {
    if (t.symbol.toLowerCase().includes(s) || t.name.toLowerCase().includes(s) || t.address.toLowerCase() === s || (s.length >= 4 && t.address.toLowerCase().startsWith(s)))
      hits.push({ kind: 'token', key: `t${t.address}`, title: t.symbol, sub: `${t.name} · ${shortAddr(t.address)}`, token: t })
  }
  for (const a of agents) {
    if (a.name.toLowerCase().includes(s) || a.role.includes(s)) hits.push({ kind: 'agent', key: `a${a.role}`, title: a.name, sub: a.duty, agent: a })
  }
  for (const o of orders) {
    if (o.id.toLowerCase().includes(s)) hits.push({ kind: 'order', key: `o${o.id}`, title: o.id, sub: `${o.side} ${o.token.symbol} · ${o.status}`, order: o })
  }
  return hits.slice(0, 20)
}

export function SearchPalette() {
  const { searchOpen, setSearchOpen } = useApp()
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const tokens = useTokens(); const agents = useAgents(); const orders = useOrders()
  const hits = useMemo(() => searchAll(q, tokens.data ?? [], agents.data ?? [], orders.data ?? []), [q, tokens.data, agents.data, orders.data])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
      if (e.key === '/' && !typing) { e.preventDefault(); setSearchOpen(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setSearchOpen])

  useEffect(() => { if (searchOpen) { setQ(''); setSel(0); setTimeout(() => inputRef.current?.focus(), 0) } }, [searchOpen])

  if (!searchOpen) return null
  const go = (h: Hit) => {
    setSearchOpen(false)
    if (h.kind === 'token') nav(`/token/${h.token.address}`)
    else if (h.kind === 'agent') nav(`/agents?agent=${h.agent.role}`)
    else nav(`/orders?order=${h.order.id}`)
  }
  const KIND = { token: '代币', agent: 'Agent', order: '订单' }
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-3 pt-[10vh]">
      <div className="absolute inset-0 bg-black/60" onClick={() => setSearchOpen(false)} />
      <div role="dialog" aria-modal="true" aria-label="搜索" className="anim-enter relative w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-panel">
        <div className="flex items-center gap-2 border-b border-line px-4">
          <ISearch className="text-sub" />
          <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setSel(0) }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setSearchOpen(false)
              if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)) }
              if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
              if (e.key === 'Enter' && hits[sel]) go(hits[sel])
            }}
            aria-label="搜索关键词" placeholder="代币符号、合约地址、Agent 名称、订单 ID"
            className="h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-sub" />
          <kbd className="rounded border border-line px-1.5 text-[11px] text-sub">Esc</kbd>
        </div>
        <ul role="listbox" aria-label="搜索结果" className="max-h-[50vh] overflow-y-auto p-1.5">
          {q && hits.length === 0 && <li className="p-4 text-center text-sm text-sub">没有找到「{q}」。合约地址需输入至少前 4 位。</li>}
          {!q && <li className="p-4 text-center text-sm text-sub">输入关键词开始搜索。演示数据中可试试 ZAPP、风控、ord_7f3a。</li>}
          {hits.map((h, i) => (
            <li key={h.key} role="option" aria-selected={i === sel}>
              <button onMouseEnter={() => setSel(i)} onClick={() => go(h)}
                className={cx('flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left', i === sel && 'bg-raised')}>
                {h.kind === 'token' ? <TokenAvatar symbol={h.token.symbol} hue={h.token.hue} size={28} /> : <span className="flex h-7 w-7 items-center justify-center rounded-full bg-raised text-[11px] text-sub">{KIND[h.kind].slice(0, 2)}</span>}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">{h.title}</span>
                  <span className="block truncate text-xs text-sub">{h.sub}</span>
                </span>
                <span className="text-[11px] text-sub">{KIND[h.kind]}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
