import { useState } from 'react'
import { useAgents, useEvents, now } from '@/api/hooks'
import { Drawer, DataState, StatusDot, AGENT_STATUS, Tag, KV, DemoTag, cx } from '@/components/ui'
import { ROLE_MAP, IMPL_LABEL } from '@/domain/agents'
import type { AgentRole } from '@/domain/types'
import { relTime, duration, usd, clock } from '@/lib/format'
import { useDrawerLink } from '@/app/links'

export function AgentDrawer({ role, onClose }: { role: string | null; onClose: () => void }) {
  const q = useAgents()
  const ev = useEvents()
  const open = useDrawerLink()
  const [showJson, setShowJson] = useState(false)
  const def = role && role in ROLE_MAP ? ROLE_MAP[role as AgentRole] : null
  return (
    <Drawer open={!!role} onClose={onClose} title={def?.name ?? `未知 Agent：${role}`}>
      <DataState q={q}>
        {(agents) => {
          const a = agents.find((x) => x.role === role)
          if (!a || !def) return <p className="p-5 text-sm text-sub">没有这个 Agent。</p>
          const logs = (ev.data ?? []).filter((e) => e.agent === a.role)
          return (
            <div className="space-y-6 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <StatusDot status={a.status} pulse /><span className="text-sm">{AGENT_STATUS[a.status].label}</span>
                <Tag tone="agent">{IMPL_LABEL[a.impl]}</Tag>
                {a.noModel && <Tag tone="sub">禁止使用模型</Tag>}
                <DemoTag />
              </div>
              <section>
                <h3 className="mb-1 text-sm font-semibold">角色说明</h3>
                <p className="text-[13px] text-fg/85">{def.duty}</p>
                <p className="mt-1 text-xs text-sub">运行方式：{def.runMode}</p>
              </section>
              <section className="grid gap-x-8 sm:grid-cols-2">
                <KV k="当前任务" v={a.currentTask ?? '空闲'} />
                <KV k="最近心跳" v={relTime(a.lastHeartbeatAt, now())} />
                <KV k="运行时长" v={duration(now().getTime() - new Date(a.startedAt).getTime())} />
                <KV k="24h 错误" v={<span className={a.errorCount24h ? 'text-warn' : ''}>{a.errorCount24h}</span>} />
                <KV k="模型" v={a.model ?? (a.noModel ? '无（纯代码）' : '未调用')} />
                <KV k="今日模型成本" v={usd(a.modelCostTodayUsd)} />
              </section>
              <section>
                <h3 className="mb-1 text-sm font-semibold">最近输出</h3>
                <p className="rounded-lg border border-line bg-bg p-3 text-[13px]">{a.lastOutput}</p>
                <button className="mt-2 text-xs text-agent hover:underline" onClick={() => setShowJson((v) => !v)} aria-expanded={showJson}>
                  {showJson ? '收起' : '展开'}任务输入 / 输出 JSON
                </button>
                {showJson && (
                  <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-[11px] leading-relaxed text-fg/80">
{JSON.stringify({ input: { task: a.currentTask, upstream: a.upstream }, output: { summary: a.lastOutput, status: a.status } }, null, 2)}
                  </pre>
                )}
              </section>
              <section>
                <h3 className="mb-2 text-sm font-semibold">上下游</h3>
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="text-sub">上游</span>
                  {a.upstream.length ? a.upstream.map((u) => <button key={u} onClick={() => open('agent', u)} className="rounded-md bg-raised px-2 py-0.5 hover:text-agent">{ROLE_MAP[u].short}</button>) : <span className="text-sub">无</span>}
                  <span className="ml-3 text-sub">下游</span>
                  {a.downstream.length ? a.downstream.map((u) => <button key={u} onClick={() => open('agent', u)} className="rounded-md bg-raised px-2 py-0.5 hover:text-agent">{ROLE_MAP[u].short}</button>) : <span className="text-sub">无</span>}
                </div>
              </section>
              <section>
                <h3 className="mb-2 text-sm font-semibold">运行日志与历史任务</h3>
                {logs.length === 0 ? <p className="text-[13px] text-sub">暂无日志。</p> : (
                  <ul className="space-y-2">
                    {logs.map((e) => (
                      <li key={e.id} className="flex gap-3 text-[13px]">
                        <span className="num w-[108px] shrink-0 text-xs text-sub">{clock(e.at)}</span>
                        <span className={cx(e.status === 'error' ? 'text-down' : e.status === 'warn' ? 'text-warn' : 'text-fg/85')}>{e.message}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )
        }}
      </DataState>
    </Drawer>
  )
}
