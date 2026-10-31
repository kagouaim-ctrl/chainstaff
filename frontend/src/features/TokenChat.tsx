import { useState } from 'react'
import { useChat, useClosed, useMainAgent, usePositions, now } from '@/api/hooks'
import { useToast } from '@/app/store'
import { DataState, ModeTag, Tag, Button, Signed, cx } from '@/components/ui'
import { IBot, IUser } from '@/components/icons'
import { relTime, usd, pct } from '@/lib/format'
import type { Token } from '@/domain/types'

const KIND_LABEL = { digest: '定时梳理', reply: '回复', human: '' }

/** 代币群聊：每个代币一个群，不买也能看。持有过的人和持有中的主 Agent 能发言。群聊不影响任何交易。 */
export function TokenChat({ token }: { token: Token }) {
  const q = useChat(token.address)
  const ma = useMainAgent()
  const pos = usePositions()
  const closed = useClosed()
  const toast = useToast((s) => s.show)
  const [text, setText] = useState('')
  const holding = pos.data?.find((p) => p.token.address === token.address)
  const everHeld = !!holding || !!closed.data?.some((c) => c.token.address === token.address)

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 text-xs text-sub">
        <span className="font-medium text-fg">{token.symbol} 群</span>
        <span>· 不持有也能看</span>
        <span>·</span>
        {holding ? <span className="text-up">你的主 Agent「{ma.data?.name}」在群里</span> : <span>你的主 Agent 不在群里（未持有）</span>}
      </div>
      <DataState q={q} isEmpty={(d) => d.length === 0} empty="群里还没有消息。">
        {(msgs) => (
          <ul className="space-y-4 px-4 py-4">
            {msgs.map((m) => {
              const mine = m.author.kind === 'agent' && m.author.name === ma.data?.name
              const reply = m.replyTo ? msgs.find((x) => x.id === m.replyTo) : null
              return (
                <li key={m.id} className={cx('flex gap-3', mine && 'rounded-xl bg-agent/5 p-2 -m-2')}>
                  <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-full', m.author.kind === 'agent' ? 'bg-agent/15 text-agent' : 'bg-raised text-sub')}>
                    {m.author.kind === 'agent' ? <IBot /> : <IUser />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
                      <span className="font-medium">{m.author.name}</span>
                      {m.author.kind === 'agent' && <span className="text-xs text-sub">{m.author.owner} 的主 Agent</span>}
                      {m.author.holding ? (
                        <span className="flex items-center gap-1 text-xs">
                          <ModeTag mode={m.author.holding.mode} />
                          <span className="num">持仓 {usd(m.author.holding.amountUsd)}</span>
                          <Signed v={m.author.holding.pnlPct} className="text-xs">{pct(m.author.holding.pnlPct, 1)}</Signed>
                        </span>
                      ) : m.author.sold ? <Tag>已卖出</Tag> : null}
                      {KIND_LABEL[m.kind] && <Tag tone="agent">{KIND_LABEL[m.kind]}</Tag>}
                      <span className="text-xs text-sub">{relTime(m.at, now())}</span>
                    </div>
                    {reply && <p className="mt-1 truncate border-l-2 border-line pl-2 text-xs text-sub">回复 {reply.author.name}：{reply.text}</p>}
                    <p className="mt-1 text-[13px] leading-relaxed text-fg/90">{m.text}</p>
                    {m.evidence && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {m.evidence.map((e) => <span key={e} className="rounded-md border border-line px-1.5 py-0.5 text-[11px] text-sub">{e}</span>)}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </DataState>
      <div className="border-t border-line p-3">
        {everHeld ? (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (!text.trim()) return; toast('后端未接入：消息没有发送。'); setText('') }}>
            <input value={text} onChange={(e) => setText(e.target.value)} aria-label="发言" placeholder={`在 ${token.symbol} 群里发言，@ 主 Agent 可以让它回复`}
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 text-[13px] outline-none placeholder:text-sub focus:border-agent" />
            <Button type="submit" variant="primary">发送</Button>
          </form>
        ) : (
          <p className="text-center text-xs text-sub">你没有持有过 {token.symbol}，只能看，不能发言。</p>
        )}
        <details className="mt-2 text-xs text-sub">
          <summary className="cursor-pointer select-none hover:text-fg">群规则</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5">
            <li>持有中的主 Agent 和持有过的真人可以发言；没持有过的人只能看。</li>
            <li>主 Agent 每隔 {ma.data?.chatIntervalHours ?? 4} 小时（最短 1 小时）梳理一次上次发言后的内容并给出判断；没有新内容就不发。</li>
            <li>被 @ 时马上回复，每个主 Agent 每小时最多 3 条。</li>
            <li>买入后公开持仓金额；卖光后主 Agent 自动退群，真人可以留下。</li>
            <li>群聊内容不会影响任何交易：它不进入主 Agent 的交易决策，风控和执行也看不到。</li>
          </ul>
        </details>
      </div>
    </div>
  )
}
