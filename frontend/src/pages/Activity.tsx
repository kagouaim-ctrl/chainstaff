import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useEvents, isDemo } from '@/api/hooks'
import { useDrawerLink } from '@/app/links'
import { DataState, Panel, Button, cx } from '@/components/ui'
import { IPause, IPlay } from '@/components/icons'
import { ROLES, ROLE_MAP } from '@/domain/agents'
import { clock } from '@/lib/format'
import type { AgentEvent, AgentRole, EventStatus } from '@/domain/types'

export function filterEvents(evs: AgentEvent[], f: { agent: AgentRole | 'all'; type: string; status: EventStatus | 'all'; token: string }) {
  const tk = f.token.trim().toLowerCase()
  return evs.filter((e) => (f.agent === 'all' || e.agent === f.agent) && (f.type === 'all' || e.type === f.type) && (f.status === 'all' || e.status === f.status) && (!tk || e.token?.symbol.toLowerCase().includes(tk)))
}

const STATUS_TXT: Record<EventStatus, string> = { info: '信息', ok: '成功', warn: '警告', error: '错误' }

export function Activity() {
  const q = useEvents()
  const nav = useNavigate()
  const open = useDrawerLink()
  const [agent, setAgent] = useState<AgentRole | 'all'>('all')
  const [type, setType] = useState('all')
  const [status, setStatus] = useState<EventStatus | 'all'>('all')
  const [token, setToken] = useState('')
  const [follow, setFollow] = useState(true)
  const types = useMemo(() => Array.from(new Set((q.data ?? []).map((e) => e.type))), [q.data])
  const list = useMemo(() => filterEvents(q.data ?? [], { agent, type, status, token }), [q.data, agent, type, status, token])
  const sel = 'h-8 rounded-lg border border-line bg-bg px-2 text-[13px]'
  return (
    <Panel demo title="实时动态" bodyClass="p-0" right={
      <Button variant="outline" onClick={() => setFollow((v) => !v)} aria-pressed={!follow} className="py-1 text-xs">{follow ? <><IPause />暂停自动滚动</> : <><IPlay />恢复自动滚动</>}</Button>
    }>
      <div className="flex flex-wrap gap-2 border-b border-line px-4 py-3">
        <select aria-label="按 Agent 筛选" className={sel} value={agent} onChange={(e) => setAgent(e.target.value as AgentRole | 'all')}>
          <option value="all">全部 Agent</option>{ROLES.map((r) => <option key={r.role} value={r.role}>{r.name}</option>)}
        </select>
        <select aria-label="按任务类型筛选" className={sel} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="all">全部类型</option>{types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select aria-label="按状态筛选" className={sel} value={status} onChange={(e) => setStatus(e.target.value as EventStatus | 'all')}>
          <option value="all">全部状态</option>{(Object.keys(STATUS_TXT) as EventStatus[]).map((s) => <option key={s} value={s}>{STATUS_TXT[s]}</option>)}
        </select>
        <input aria-label="按代币筛选" placeholder="代币符号" value={token} onChange={(e) => setToken(e.target.value)} className={cx(sel, 'w-32 placeholder:text-sub')} />
        <span className="ml-auto self-center text-xs text-sub">{isDemo() ? '事件来自任务日志。实时推送未接入，当前显示固定演示样例。' : '事件来自 Agent 的分析记录，每次发言后刷新。'}</span>
      </div>
      <DataState q={q} rows={8} isEmpty={() => list.length === 0} empty="没有符合条件的事件。">
        {() => (
          <ul>
            {list.map((e) => (
              <li key={e.id} className="anim-enter flex flex-wrap items-start gap-x-3 gap-y-1 border-b border-line px-4 py-3 text-[13px] last:border-0 sm:flex-nowrap">
                <span className="num w-[108px] shrink-0 text-xs text-sub">{clock(e.at)}</span>
                <span className={cx('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', { info: 'bg-sub', ok: 'bg-up', warn: 'bg-warn', error: 'bg-down' }[e.status])} aria-label={STATUS_TXT[e.status]} />
                <button onClick={() => open('agent', e.agent)} className="w-14 shrink-0 text-left text-xs text-agent hover:underline">{ROLE_MAP[e.agent].short}</button>
                <span className="w-16 shrink-0 text-xs text-sub">{e.type}</span>
                <span className="min-w-0 flex-1 basis-full text-fg/90 sm:basis-auto">{e.message}</span>
                <span className="flex shrink-0 gap-2 text-xs">
                  {e.token && <button className="text-sub hover:text-fg" onClick={() => nav(`/token/${e.token!.address}`)}>{e.token.symbol}</button>}
                  {e.orderId && <button className="text-sub hover:text-fg" onClick={() => open('order', e.orderId!)}>{e.orderId}</button>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </DataState>
    </Panel>
  )
}
