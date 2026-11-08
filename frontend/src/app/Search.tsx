import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from './store'
import { useQuery } from '@tanstack/react-query'
import { isDemo, useAgents, useOrders, useTokens } from '@/api/hooks'
import { ISearch } from '@/components/icons'
import { TokenAvatar, cx } from '@/components/ui'
import { age, shortAddr, usd } from '@/lib/format'
import { CHAIN_LABEL, isAddress, search } from '@/api/chain'
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
      hits.push({ kind: 'token', key: `t${t.address}`, title: t.symbol, sub: `${t.name} · ${CHAIN_LABEL[t.chain] ?? ''} · ${shortAddr(t.address)}`, token: t })
  }
  // 粘贴了一个列表里没有的合约地址：直接去代币页查
  if (isAddress(q.trim()) && !hits.length) hits.push({ kind: 'token', key: `t${q.trim()}`, title: shortAddr(q.trim()), sub: '查看这个合约地址', token: { address: q.trim(), symbol: shortAddr(q.trim()), name: '', hue: 0 } as Token })
  for (const a of agents) {
    if (a.name.toLowerCase().includes(s) || a.role.includes(s)) hits.push({ kind: 'agent', key: `a${a.role}`, title: a.name, sub: a.duty, agent: a })
  }
  for (const o of orders) {
    if (o.id.toLowerCase().includes(s)) hits.push({ kind: 'order', key: `o${o.id}`, title: o.id, sub: `${o.side} ${o.token.symbol} · ${o.status}`, order: o })
  }
  return hits.slice(0, 20)
}

const RECENT_KEY = 'memeagent.recent'
const readRecent = (): string[] => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') } catch { return [] } }
const saveRecent = (a: string) => { try { localStorage.setItem(RECENT_KEY, JSON.stringify([a, ...readRecent().filter((x) => x !== a)].slice(0, 6))) } catch { /* 隐私模式下不记 */ } }

/** 没输入时：最近看过的代币 + 热门，和交易所的搜索框一样。 */
function Suggest({ tokens, onPick }: { tokens: Token[]; onPick: (t: Token) => void }) {
  const recent = readRecent().map((a) => tokens.find((t) => t.address === a)).filter((t): t is Token => !!t)
  const hot = [...tokens].sort((a, b) => b.stats['24h'].volume - a.stats['24h'].volume).slice(0, 6)
  const Row = ({ title, list }: { title: string; list: Token[] }) => list.length ? (
    <li className="px-2 pb-2 pt-1">
      <div className="px-1 pb-1.5 text-[12px] font-normal text-sub">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {list.map((t) => (
          <button key={t.address} onClick={() => onPick(t)} className="flex h-8 items-center gap-1.5 rounded-full border border-line pl-1 pr-3 text-[13px] hover:bg-raised">
            <TokenAvatar symbol={t.symbol} hue={t.hue} size={22} />{t.symbol}
          </button>
        ))}
      </div>
    </li>
  ) : null
  return <><Row title="最近看过" list={recent} /><Row title="24 小时成交最多" list={hot} /></>
}

export function SearchPalette() {
  const { searchOpen, setSearchOpen } = useApp()
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const tokens = useTokens(); const agents = useAgents(); const orders = useOrders()
  const local = useMemo(() => searchAll(q, tokens.data ?? [], agents.data ?? [], orders.data ?? []), [q, tokens.data, agents.data, orders.data])
  // 发现页只列几十个代币；其余的在 DexScreener 上搜，停止输入 300 毫秒后再查
  const [term, setTerm] = useState('')
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 300); return () => clearTimeout(t) }, [q])
  const remote = useQuery({ queryKey: ['search', term], queryFn: () => search(term), enabled: !isDemo() && term.length >= 2 && !isAddress(term), staleTime: 60_000 })
  const hits = useMemo(() => {
    const seen = new Set(local.filter((h) => h.kind === 'token').map((h) => h.token.address.toLowerCase()))
    const extra: Hit[] = (term === q.trim() ? remote.data ?? [] : []).filter((r) => !seen.has(r.address.toLowerCase())).map((r) => ({
      kind: 'token', key: `s${r.chain}${r.address}`, title: r.symbol,
      sub: `${r.name} · ${CHAIN_LABEL[r.chain]} · 流动性 ${usd(r.liquidityUsd, { compact: true })} · 24h 成交 ${usd(r.volumeH24, { compact: true })}${r.ageMinutes != null ? ` · ${age(r.ageMinutes)}` : ''} · ${shortAddr(r.address)}`,
      token: { address: r.address, symbol: r.symbol, name: r.name, hue: 0, chain: r.chain } as Token,
    }))
    // 本地列表里的和 DexScreener 搜到的放在一起按 24h 成交额排，免得发现页里的同名仿盘排在正主前面
    const vol = (h: Hit) => h.kind !== 'token' ? 0 : (h.token.stats?.['24h']?.volume ?? remote.data?.find((r) => r.address === h.token.address)?.volumeH24 ?? 0)
    const tokensFirst = [...local.filter((h) => h.kind === 'token'), ...extra].sort((x, y) => vol(y) - vol(x))
    return [...tokensFirst, ...local.filter((h) => h.kind !== 'token')].slice(0, 20)
  }, [local, remote.data, term, q])
  // 同一个符号对应多个合约时提醒核对，仿盘常用同名
  const dupes = useMemo(() => {
    const n = new Map<string, number>()
    for (const h of hits) if (h.kind === 'token') n.set(h.title.toLowerCase(), (n.get(h.title.toLowerCase()) ?? 0) + 1)
    return [...n.entries()].filter(([, c]) => c > 1)
  }, [hits])
  const searching = !isDemo() && q.trim().length >= 2 && !isAddress(q.trim()) && (term !== q.trim() || remote.isFetching)

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
    if (h.kind === 'token') saveRecent(h.token.address)
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
          {dupes.length > 0 && <li role="note" className="mx-1.5 mb-1 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">{dupes.map(([sym, c]) => `有 ${c} 个叫「${sym.toUpperCase()}」的代币`).join('，')}，名字可以随便起，请核对合约地址。已按成交额排序，流动性和币龄写在每一行里。</li>}
          {q && hits.length === 0 && <li className="p-4 text-center text-sm text-sub">{searching ? '正在搜索…' : remote.isError ? '搜索暂时不可用，可以直接粘贴合约地址。' : `没有找到「${q}」。可以输入代币名称、符号或完整的合约地址。`}</li>}
          {!q && <Suggest tokens={tokens.data ?? []} onPick={(t) => go({ kind: 'token', key: t.address, title: t.symbol, sub: t.name, token: t })} />}
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
