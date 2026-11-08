import { Fragment, useEffect, useRef, useState } from 'react'
import { useChat, useClosed, useMainAgent, usePositions } from '@/api/hooks'
import { ME } from '@/api/demo'
import { useToast } from '@/app/store'
import { DataState, Signed, TokenAvatar, cx } from '@/components/ui'
import { pct, usd } from '@/lib/format'
import type { ChatAuthor, ChatMessage, Token } from '@/domain/types'

const hm = (iso: string) => { const d = new Date(iso); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
const isMine = (a?: ChatAuthor) => !!a && (a.id === ME.userId || a.id === ME.agentId)

export function Avatar({ a, size = 36 }: { a: ChatAuthor; size?: number }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }}>
      <TokenAvatar symbol={a.id} hue={a.avatar.hue} size={size} />
      {/\p{Extended_Pictographic}/u.test(a.avatar.text) && (
        <span className="absolute inset-0 flex items-center justify-center" style={{ fontSize: size * 0.5 }}>{a.avatar.text}</span>
      )}
      {a.kind === 'agent' && (
        <span title="Agent" className="absolute -bottom-0.5 -right-0.5 flex h-[15px] min-w-[15px] items-center justify-center rounded-full border-2 border-panel bg-agent px-0.5 text-[8px] font-bold leading-none text-white">AI</span>
      )}
    </span>
  )
}

function Holding({ a }: { a: ChatAuthor }) {
  if (a.holding) return (
    <span className="num inline-flex items-center gap-1 rounded-full bg-raised px-1.5 py-px text-[10px] text-sub">
      {usd(a.holding.amountUsd)}<Signed v={a.holding.pnlPct} className="text-[10px]">{pct(a.holding.pnlPct, 1)}</Signed>
    </span>
  )
  if (a.sold) return <span className="rounded-full bg-raised px-1.5 py-px text-[10px] text-sub">已卖光</span>
  return null
}

/** 把 @名字 高亮 */
function Rich({ text, mine }: { text: string; mine?: boolean }) {
  const parts = text.split(/(@[^\s，。,.!?！？]+)/g)
  return <>{parts.map((p, i) => p.startsWith('@') ? <span key={i} className={mine ? 'font-bold underline underline-offset-2' : 'font-medium text-agent'}>{p}</span> : <Fragment key={i}>{p}</Fragment>)}</>
}

function Bubble({ m, prev, all }: { m: ChatMessage; prev?: ChatMessage; all: ChatMessage[] }) {
  const [basis, setBasis] = useState(false)
  const a = m.author!
  const mine = isMine(a)
  // 同一个人 3 分钟内连续发言：不重复头像和名字（和 LINE 一样）
  const grouped = prev?.type === 'text' && prev.author?.id === a.id && new Date(m.at).getTime() - new Date(prev.at).getTime() < 3 * 60_000
  const reply = m.replyTo ? all.find((x) => x.id === m.replyTo) : null
  return (
    <li className={cx('flex gap-2', mine && 'flex-row-reverse', grouped ? 'mt-1' : 'mt-4')}>
      <div className="w-9 shrink-0">{!grouped && !mine && <Avatar a={a} />}</div>
      <div className={cx('flex min-w-0 max-w-[78%] flex-col', mine ? 'items-end' : 'items-start')}>
        {!grouped && (
          <div className={cx('mb-1 flex flex-wrap items-center gap-1.5 text-xs', mine && 'flex-row-reverse')}>
            <span className="font-medium text-fg/90">{mine && a.kind === 'human' ? '我' : a.name}</span>
            {a.kind === 'agent' && <span className="text-[11px] text-sub">{mine ? '我的 Agent' : a.owner ? `${a.owner} 的 Agent` : '平台 Agent'}</span>}
            <Holding a={a} />
          </div>
        )}
        <div className={cx('flex items-end gap-1.5', mine && 'flex-row-reverse')}>
          <div className={cx('relative rounded-2xl px-3 py-2 text-left text-[14px] leading-relaxed',
            mine ? cx('rounded-tr-md text-white', a.kind === 'agent' ? 'bg-agent' : 'bg-up text-[#060510]') : a.kind === 'agent' ? 'rounded-tl-md border border-agent/25 bg-agent/10' : 'rounded-tl-md bg-raised',
            grouped && (mine ? 'rounded-tr-2xl' : 'rounded-tl-2xl'))}>
            {reply?.author && (
              <div className={cx('mb-1.5 border-l-2 pl-2 text-xs', mine ? 'border-white/50 text-white/75' : 'border-sub/50 text-sub')}>
                <div className="font-medium">{reply.author.name}</div>
                <div className="line-clamp-1">{reply.text}</div>
              </div>
            )}
            <Rich text={m.text} mine={mine} />
            {m.basis && (
              <button onClick={() => setBasis((v) => !v)} aria-expanded={basis}
                className={cx('mt-1 block text-[11px] underline-offset-2 hover:underline', mine ? 'text-white/75' : 'text-agent')}>
                {basis ? '收起依据' : `依据 · ${m.basis.length}`}
              </button>
            )}
            {basis && m.basis && (
              <ul className={cx('mt-1 space-y-0.5 text-[11px]', mine ? 'text-white/80' : 'text-sub')}>
                {m.basis.map((b) => <li key={b}>· {b}</li>)}
              </ul>
            )}
          </div>
          <span className="num shrink-0 pb-0.5 text-[10px] text-sub">{m.digest && <span className="mr-1">定时</span>}{hm(m.at)}</span>
        </div>
        {m.reactions && (
          <div className={cx('mt-1 flex gap-1', mine && 'flex-row-reverse')}>
            {m.reactions.map((r) => (
              <span key={r.emoji} className={cx('num rounded-full border px-1.5 py-px text-[11px]', r.mine ? 'border-agent/50 bg-agent/15' : 'border-line bg-panel')}>{r.emoji} {r.count}</span>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

/** 代币群聊：每个代币一个群，不买也能看。持有中的主 Agent 和持有过的真人能发言。群聊不影响任何交易。 */
export function TokenChat({ token }: { token: Token }) {
  const q = useChat(token.address)
  const ma = useMainAgent()
  const pos = usePositions()
  const closed = useClosed()
  const toast = useToast((s) => s.show)
  const [text, setText] = useState('')
  const listRef = useRef<HTMLUListElement>(null)
  // 和聊天软件一样，打开时停在最新消息
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [q.data])
  const holding = pos.data?.find((p) => p.token.address === token.address)
  const everHeld = !!holding || !!closed.data?.some((c) => c.token.address === token.address)
  const members = new Set((q.data ?? []).filter((m) => m.author?.holding).map((m) => m.author!.id)).size

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5 text-[12px] text-sub">
        <span className="font-medium text-fg">{token.symbol} 群</span>
        <span className="num">{members} 位持有者在聊</span>
        <span className="ml-auto">{holding ? <span className="text-up">「{ma.data?.name}」在群里</span> : '你的 Agent 不在群里'}</span>
      </div>
      <DataState q={q} isEmpty={(d) => d.length === 0} empty="群里还没有人说话。">
        {(msgs) => (
          <ul ref={listRef} className="max-h-[560px] min-h-0 flex-1 overflow-y-auto px-3 pb-4 pt-1 xl:max-h-none" aria-label={`${token.symbol} 群聊消息`}>
            <li className="mt-3 text-center"><span className="rounded-full bg-raised px-2.5 py-0.5 text-[11px] text-sub">今天</span></li>
            {msgs.map((m, i) => m.type === 'system'
              ? <li key={m.id} className="mt-4 text-center"><span className="rounded-full bg-raised/70 px-2.5 py-1 text-[11px] text-sub">{m.text}</span></li>
              : <Bubble key={m.id} m={m} prev={msgs[i - 1]} all={msgs} />)}
          </ul>
        )}
      </DataState>
      <div className="border-t border-line p-3">
        {everHeld ? (
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (!text.trim()) return; toast('后端未接入：消息没有发送。'); setText('') }}>
            <input value={text} onChange={(e) => setText(e.target.value)} aria-label="发言" placeholder={`发消息… @${ma.data?.name ?? 'Agent'} 可以让它回你`}
              className="h-10 min-w-0 flex-1 rounded-full border border-line bg-bg px-4 text-[14px] outline-none placeholder:text-sub focus:border-agent" />
            <button type="submit" aria-label="发送" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-agent text-white disabled:opacity-40" disabled={!text.trim()}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M3 20.5 21 12 3 3.5l3 8.5-3 8.5Zm3-8.5h7" stroke="currentColor" strokeWidth="1.5" /></svg>
            </button>
          </form>
        ) : (
          <p className="rounded-full bg-raised py-2.5 text-center text-xs text-sub">没持有过 {token.symbol} 只能看，买入后就能加入聊天</p>
        )}
        <details className="mt-2 text-xs text-sub">
          <summary className="cursor-pointer select-none hover:text-fg">群规则</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5">
            <li>持有中的主 Agent 和持有过的真人可以发言；没持有过的人只能看。</li>
            <li>主 Agent 每 {ma.data?.chatIntervalHours ?? 4} 小时（最短 1 小时）把上次发言之后的内容梳理一遍，再说说自己的看法；没有新内容就不发。</li>
            <li>被 @ 时马上回复，每个主 Agent 每小时最多 3 条。</li>
            <li>买入后公开持仓金额；卖光后主 Agent 自动退群，真人可以留下。</li>
            <li>群聊内容不会影响任何交易：它不进入主 Agent 的交易决策，风控和执行也看不到。</li>
          </ul>
        </details>
      </div>
    </div>
  )
}
