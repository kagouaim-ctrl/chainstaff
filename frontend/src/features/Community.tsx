import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Segmented, TokenAvatar, cx } from '@/components/ui'
import { price as fmtPrice, shortAddr, relTime } from '@/lib/format'
import { useToast } from '@/app/store'
import { useWallet } from '@/wallet/store'
import { deletePost, messages, postMessage, putThesis, sessionFor, signIn, speakerFor, theses, type Post } from '@/api/social'
import type { Token } from '@/domain/types'

/**
 * 接了后端的群聊和观点（社区）。没接后端时代币页用原来的 TokenChat / Theses。
 * 内容都是陌生人写的，只当纯文本显示；不进入 Agent 的分析，也不影响交易。
 */
const hue = (s: string) => [...s].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7)
const hm = (sec: number) => { const d = new Date(sec * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }

/** 当前钱包是否已登录社区；没有就给登录按钮 */
function useMember(t: Token) {
  const w = useWallet()
  const wallet = t.chain === 'solana' ? w.solana?.address : w.evm?.address
  const [, bump] = useState(0)
  const signedIn = !!sessionFor(wallet)
  const toast = useToast((s) => s.show)
  const login = async () => {
    if (!wallet) { w.openPicker(t.chain === 'solana' ? 'solana' : 'evm'); return }
    try { await signIn(t.chain); bump((x) => x + 1) } catch (e) { toast(/reject|denied|cancel/i.test(String(e)) ? '你取消了签名' : `登录失败：${(e as Error).message}`) }
  }
  return { wallet, signedIn, login }
}

function Gate({ t, m }: { t: Token; m: ReturnType<typeof useMember> }) {
  return (
    <div className="space-y-1.5 text-center">
      <Button variant="outline" className="h-10 w-full rounded-full" onClick={m.login}>{m.wallet ? '用钱包签名登录后发言' : `连接${t.chain === 'solana' ? ' Solana ' : ' EVM '}钱包后发言`}</Button>
      <p className="text-[11px] text-sub">签名只证明钱包归属，不是交易，不花钱。</p>
    </div>
  )
}

export function CommunityChat({ t }: { t: Token }) {
  const qc = useQueryClient()
  const m = useMember(t)
  const toast = useToast((s) => s.show)
  const [text, setText] = useState('')
  const q = useQuery({ queryKey: ['social-chat', t.chain, t.address], queryFn: () => messages(t.chain, t.address), refetchInterval: 5_000 })
  const send = useMutation({
    mutationFn: (x: string) => postMessage(t.chain, t.address, x),
    onSuccess: () => { setText(''); qc.invalidateQueries({ queryKey: ['social-chat', t.chain, t.address] }) },
    onError: (e) => toast((e as Error).message),
  })
  const del = useMutation({ mutationFn: (id: number) => deletePost(t.chain, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['social-chat', t.chain, t.address] }) })
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [q.data])
  const people = new Set((q.data ?? []).map((p) => p.wallet)).size
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5 text-[12px] text-sub">
        <span className="font-medium text-fg">{t.symbol} 群</span>
        <span className="num">{people} 个钱包发过言</span>
      </div>
      {q.isError
        ? <p role="alert" className="p-4 text-[13px] text-down">{(q.error as Error).message}</p>
        : !q.data ? <p className="p-4 text-[13px] text-sub">加载中…</p>
          : q.data.length === 0 ? <p className="p-6 text-center text-[13px] text-sub">群里还没有人说话。</p>
            : (
              <ul ref={listRef} className="max-h-[560px] min-h-0 flex-1 overflow-y-auto px-3 pb-4 pt-1 xl:max-h-none" aria-label={`${t.symbol} 群聊消息`}>
                {q.data.map((p) => {
                  const mine = p.wallet === m.wallet
                  return (
                    <li key={p.id} className={cx('mt-3 flex gap-2', mine && 'flex-row-reverse')}>
                      {!mine && <TokenAvatar symbol={p.wallet} hue={hue(p.wallet)} size={32} />}
                      <div className={cx('flex min-w-0 max-w-[78%] flex-col', mine ? 'items-end' : 'items-start')}>
                        <div className="mb-1 flex items-center gap-1.5 text-[11px] text-sub">
                          <Link to={t.chain === 'solana' ? `/wallet/${p.wallet}` : '#'} className="num hover:text-fg">{mine ? '我' : shortAddr(p.wallet, 4, 4)}</Link>
                          <span className="num">{hm(p.createdAt)}</span>
                          {mine && <button className="hover:text-down" onClick={() => del.mutate(p.id)}>删除</button>}
                        </div>
                        <div className={cx('whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-[14px] leading-relaxed', mine ? 'rounded-tr-md bg-up text-[#060510]' : 'rounded-tl-md bg-raised')}>{p.text}</div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
      <div className="mt-auto border-t border-line p-3">
        {m.signedIn ? (
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(text) }}>
            <input value={text} maxLength={500} onChange={(e) => setText(e.target.value)} aria-label="发言" placeholder="发消息…"
              className="h-10 min-w-0 flex-1 rounded-full border border-line bg-bg px-4 text-[14px] outline-none placeholder:text-sub focus:border-agent" />
            <button type="submit" aria-label="发送" disabled={!text.trim() || send.isPending} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-agent text-white disabled:opacity-40">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M3 20.5 21 12 3 3.5l3 8.5-3 8.5Zm3-8.5h7" stroke="currentColor" strokeWidth="1.5" /></svg>
            </button>
          </form>
        ) : <Gate t={t} m={m} />}
        <p className="mt-2 text-[11px] text-sub">群聊内容来自网友，不构成建议，不会影响任何交易。</p>
      </div>
    </div>
  )
}

/** 观点（Callout）：一人一条，看多或看空，记下发布时价格，显示到现在的涨跌。 */
export function CommunityTheses({ t }: { t: Token }) {
  const qc = useQueryClient()
  const m = useMember(t)
  const toast = useToast((s) => s.show)
  const [f, setF] = useState<'all' | 'bull' | 'bear'>('all')
  const [open, setOpen] = useState(false)
  const [stance, setStance] = useState<'bull' | 'bear'>('bull')
  const [text, setText] = useState('')
  const q = useQuery({ queryKey: ['social-theses', t.chain, t.address], queryFn: () => theses(t.chain, t.address), refetchInterval: 30_000 })
  const save = useMutation({
    mutationFn: () => putThesis(t.chain, t.address, stance, text),
    onSuccess: () => { setOpen(false); setText(''); qc.invalidateQueries({ queryKey: ['social-theses', t.chain, t.address] }) },
    onError: (e) => toast((e as Error).message),
  })
  const del = useMutation({ mutationFn: (id: number) => deletePost(t.chain, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['social-theses', t.chain, t.address] }) })
  const mine = (q.data ?? []).find((p) => p.wallet === speakerFor(t.chain))
  const rows = (q.data ?? []).filter((p) => f === 'all' || p.stance === f)
  const since = (p: Post) => p.priceAt && t.priceUsd > 0 && t.quality?.level !== 'bad' ? (t.priceUsd / p.priceAt - 1) * 100 : null
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Segmented label="观点筛选" size="sm" value={f} onChange={setF} options={[{ value: 'all', label: '全部' }, { value: 'bull', label: '看多' }, { value: 'bear', label: '看空' }]} />
        {m.signedIn
          ? <Button variant="outline" className="ml-auto h-9 px-3 py-0 text-[14px]" onClick={() => setOpen((v) => !v)}>{mine ? '改观点' : '写观点'}</Button>
          : <Button variant="outline" className="ml-auto h-9 px-3 py-0 text-[14px]" onClick={m.login}>{m.wallet ? '签名登录' : '连接钱包'}</Button>}
      </div>
      {open && (
        <form className="space-y-2 border-b border-line p-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <Segmented label="看多或看空" size="sm" value={stance} onChange={setStance} options={[{ value: 'bull', label: '看多' }, { value: 'bear', label: '看空' }]} />
          <textarea value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} rows={4} aria-label="观点内容" placeholder="说说你的理由…"
            className="w-full rounded-lg border border-line bg-bg p-3 text-[14px] outline-none focus:border-agent" />
          <p className="text-[11px] text-sub">每个钱包对一个代币只有一条观点。发布时记录当前价格，之后显示从那时到现在的涨跌；修改会按修改时的价格重新记录。</p>
          <Button type="submit" variant="primary" disabled={!text.trim() || save.isPending}>发布</Button>
        </form>
      )}
      {q.isError ? <p role="alert" className="p-4 text-[13px] text-down">{(q.error as Error).message}</p>
        : !q.data ? <p className="p-4 text-[13px] text-sub">加载中…</p>
          : rows.length === 0 ? <p className="p-6 text-center text-[13px] text-sub">还没有人写观点。</p>
            : (
              <ul>
                {rows.map((p) => {
                  const ch = since(p)
                  const right = ch == null ? null : p.stance === 'bull' ? ch >= 0 : ch <= 0
                  return (
                    <li key={p.id} className="border-b border-line px-4 py-4 last:border-0">
                      <div className="flex flex-wrap items-center gap-2 text-[13px]">
                        <TokenAvatar symbol={p.wallet} hue={hue(p.wallet)} size={28} />
                        <span className="num">{p.wallet === m.wallet ? '我' : shortAddr(p.wallet, 4, 4)}</span>
                        <span className={cx('rounded px-1.5 text-[12px]', p.stance === 'bull' ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>{p.stance === 'bull' ? '看多' : '看空'}</span>
                        <span className="text-[12px] text-sub">{relTime(new Date(p.createdAt * 1000).toISOString(), new Date())}</span>
                        {p.wallet === m.wallet && <button className="ml-auto text-[12px] text-sub hover:text-down" onClick={() => del.mutate(p.id)}>删除</button>}
                      </div>
                      <div className="num mt-1 pl-9 text-[12px] text-sub">
                        {p.priceAt ? <>发布时 {fmtPrice(p.priceAt)}{ch != null && <> · 至今 <span className={ch >= 0 ? 'text-up' : 'text-down'}>{ch >= 0 ? '+' : ''}{ch.toFixed(1)}%</span>{right != null && <span className={cx('ml-1', right ? 'text-up' : 'text-down')}>{right ? '（目前判断对）' : '（目前判断错）'}</span>}</>}</> : '发布时价格不可信，未记录'}
                      </div>
                      <p className="mt-2 whitespace-pre-wrap break-words pl-9 text-[14px] leading-[22px]">{p.text}</p>
                    </li>
                  )
                })}
              </ul>
            )}
    </div>
  )
}
