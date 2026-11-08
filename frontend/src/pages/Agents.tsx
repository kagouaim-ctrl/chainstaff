import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, useAgentDM, useMainAgent, now } from '@/api/hooks'
import { useDrawerLink } from '@/app/links'
import { useToast } from '@/app/store'
import { DataState, StatusDot, AGENT_STATUS, ModeTag, cx } from '@/components/ui'
import { IBot, ICheck, ICopy } from '@/components/icons'
import { relTime, duration } from '@/lib/format'
import type { AgentDM } from '@/domain/types'
import { LIVE, AGENTS_ONLINE } from '@/api/live'

const SUGGEST = ['今天买了什么？', '现在持仓风险大吗？', '帮我研究一个合约地址', '为什么没买热门第一？']

/** Agent：和自己的主 Agent 对话，像普通 AI 聊天一样。右边是 Agent 状态和团队角色。 */
export function Agents() {
  const ma = useMainAgent()
  const dm = useAgentDM()
  const toast = useToast((s) => s.show)
  const [text, setText] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [dm.data])
  const name = ma.data?.name ?? '主 Agent'
  const send = (t: string) => { if (!t.trim()) return; toast('演示模式：消息没有发送，Agent 也不会执行任何操作。'); setText('') }

  return (
    <div className="-mx-4 -mt-4 grid grid-cols-1 [&>*]:min-w-0 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section aria-label={`和${name}对话`} className="flex h-[calc(100dvh-64px-33px)] min-h-[520px] flex-col md:h-[calc(100dvh-64px-33px-32px)] border-line xl:border-r">
        <header className="flex items-center gap-3 border-b border-line px-4 py-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-agent text-white"><IBot width={18} height={18} /></span>
          <div className="min-w-0">
            <h1 className="text-[16px]">{name}</h1>
            <p className="text-[12px] font-normal text-sub">你的主 Agent · 交易 + 总策划</p>
          </div>
          {ma.data && <span className="ml-auto flex items-center gap-1.5 text-[12px] text-sub"><StatusDot status={ma.data.status} pulse />{AGENT_STATUS[ma.data.status].label}<ModeTag mode={ma.data.mode} className="ml-1" /></span>}
        </header>
        {LIVE && !AGENTS_ONLINE && (
          <p className="border-b border-line bg-agent/10 px-4 py-2 text-[12px] text-fg/90">AI Agent 内测中，后端上线前不能对话，也不会下单。<Link to="/agents/about" className="ml-1 text-agent hover:underline">了解 Agent 怎么工作、风控怎么保证</Link></p>
        )}

        <div ref={listRef} className="flex-1 overflow-y-auto">
          <DataState q={dm} isEmpty={(d) => d.length === 0} empty={`还没有和${name}聊过。`}>
            {(msgs) => (
              <ol className="mx-auto max-w-[760px] space-y-6 px-4 py-6" aria-label="对话记录">
                {msgs.map((m) => <Msg key={m.id} m={m} name={name} />)}
              </ol>
            )}
          </DataState>
        </div>

        <div className="mx-auto w-full max-w-[760px] px-4 pb-24 md:pb-4">
          <div className="scrollbar-none mb-2 flex gap-2 overflow-x-auto">
            {SUGGEST.map((s) => (
              <button key={s} onClick={() => send(s)} className="h-8 shrink-0 rounded-full border border-line px-3 text-[12px] text-sub hover:text-fg">{s}</button>
            ))}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(text) }} className="flex items-end gap-2 rounded-2xl border border-line bg-panel p-2 focus-within:border-agent">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} aria-label={`给${name}发消息`} placeholder={`给${name}发消息…`}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(text) } }}
              className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[14px] font-normal outline-none placeholder:text-sub" />
            <button type="submit" aria-label="发送" disabled={!text.trim()} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-agent text-white disabled:bg-raised disabled:text-sub">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </form>
          <p className="mt-2 text-center text-[12px] font-normal text-mute">对话里的指令同样要经过风控；{name}改不了风控参数，也不能从模拟盘切到实盘。</p>
        </div>
      </section>

      <Side />
    </div>
  )
}

function Msg({ m, name }: { m: AgentDM; name: string }) {
  const open = useDrawerLink()
  const [copied, setCopied] = useState(false)
  if (m.role === 'user') return (
    <li className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-line rounded-2xl rounded-br-md bg-raised px-4 py-2.5 text-[14px] font-normal leading-[22px]">{m.text}</div>
    </li>
  )
  return (
    <li className="group flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-agent text-white"><IBot width={14} height={14} /></span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[12px] text-sub">{name} · {relTime(m.at, now())}
          <button onClick={() => { navigator.clipboard?.writeText(m.text).catch(() => {}); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
            aria-label="复制回答" className="ml-auto rounded p-1 opacity-0 hover:bg-raised hover:text-fg focus:opacity-100 group-hover:opacity-100">
            {copied ? <ICheck width={14} height={14} className="text-up" /> : <ICopy width={14} height={14} />}
          </button>
        </div>
        <div className="mt-1 whitespace-pre-line text-[14px] font-normal leading-[22px]">{m.text}</div>
        {m.links && (
          <div className="mt-2 flex flex-wrap gap-2">
            {m.links.map((l) => l.to.startsWith('?order=')
              ? <button key={l.to} onClick={() => open('order', l.to.slice(7))} className="h-8 rounded-lg border border-line px-3 text-[12px] hover:bg-raised">{l.label}</button>
              : <Link key={l.to} to={l.to} className="flex h-8 items-center rounded-lg border border-line px-3 text-[12px] hover:bg-raised">{l.label}</Link>)}
          </div>
        )}
      </div>
    </li>
  )
}

function Side() {
  const ma = useMainAgent()
  const q = useAgents()
  const open = useDrawerLink()
  return (
    <aside className="space-y-4 p-4" aria-label="Agent 状态">
      <DataState q={ma} rows={1}>
        {(m) => (
          <section className="rounded-xl border border-line p-4 text-[14px]">
            <h2 className="text-[16px]">状态</h2>
            <dl className="mt-2 space-y-1.5 text-[12px] font-normal">
              <div className="flex justify-between"><dt className="text-sub">策略</dt><dd>{m.strategyName} v{m.strategyVersion}</dd></div>
              <div className="flex justify-between"><dt className="text-sub">已运行</dt><dd>{duration(now().getTime() - new Date(m.createdAt).getTime())}</dd></div>
              <div className="flex justify-between"><dt className="text-sub">群聊发言间隔</dt><dd>每 {m.chatIntervalHours} 小时</dd></div>
            </dl>
          </section>
        )}
      </DataState>
      <section className="rounded-xl border border-line p-4">
        <h2 className="text-[16px]">团队角色</h2>
        <p className="mt-1 text-[12px] font-normal text-sub">风控、执行、持仓、总控是纯代码，模型碰不到风控参数。</p>
        <DataState q={q} rows={6}>
          {(agents) => (
            <ul className="mt-2">
              {agents.map((a) => (
                <li key={a.role}>
                  <button onClick={() => open('agent', a.role)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-raised">
                    <StatusDot status={a.status} pulse />
                    <span className="text-[14px]">{a.name}</span>
                    <span className={cx('ml-auto truncate text-[12px] font-normal', a.status === 'degraded' ? 'text-warn' : 'text-sub')}>{a.currentTask ?? AGENT_STATUS[a.status].label}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </DataState>
      </section>
    </aside>
  )
}
