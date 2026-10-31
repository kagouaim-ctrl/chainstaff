import { useAgents, useMainAgent, now } from '@/api/hooks'
import { useDrawerLink } from '@/app/links'
import { DataState, Panel, StatusDot, AGENT_STATUS, Tag, ModeTag, DemoTag, cx } from '@/components/ui'
import { IBot } from '@/components/icons'
import { IMPL_LABEL } from '@/domain/agents'
import { relTime, duration, usd } from '@/lib/format'

export function Agents() {
  const q = useAgents()
  const ma = useMainAgent()
  const open = useDrawerLink()
  return (
    <div className="space-y-4">
      <DataState q={ma} rows={1}>
        {(m) => (
          <section className="flex flex-wrap items-center gap-4 rounded-card border border-line bg-panel p-4">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-agent/15 text-agent"><IBot width={22} height={22} /></span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold">{m.name}</h1>
                <span className="text-xs text-sub">你的主 Agent · 交易 + 总策划</span>
                <ModeTag mode={m.mode} /><DemoTag />
              </div>
              <p className="mt-0.5 text-xs text-sub">策略 {m.strategyName} v{m.strategyVersion} · 运行 {duration(now().getTime() - new Date(m.createdAt).getTime())} · 群聊每 {m.chatIntervalHours} 小时发言一次</p>
            </div>
            <span className="flex items-center gap-1.5 text-[13px]"><StatusDot status={m.status} pulse />{AGENT_STATUS[m.status].label}</span>
          </section>
        )}
      </DataState>
      <Panel demo title="角色" right={<span className="text-xs text-sub">风控、执行、持仓、总控是纯代码，模型无法读取或修改风控参数</span>} bodyClass="p-3 sm:p-4">
        <DataState q={q} rows={6}>
          {(agents) => (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
              {agents.map((a) => (
                <li key={a.role}>
                  <button onClick={() => open('agent', a.role)} className="flex h-full w-full flex-col rounded-xl border border-line bg-bg/40 p-4 text-left transition-colors hover:border-fg/20 hover:bg-raised">
                    <div className="flex items-center gap-2">
                      <StatusDot status={a.status} pulse />
                      <span className="font-medium">{a.name}</span>
                      <span className={cx('ml-auto text-xs', a.status === 'degraded' ? 'text-warn' : 'text-sub')}>{AGENT_STATUS[a.status].label}</span>
                    </div>
                    <p className="mt-1.5 line-clamp-2 text-xs text-sub">{a.duty}</p>
                    <div className="mt-3 text-[13px]">
                      <span className="text-sub">当前：</span>{a.currentTask ?? <span className="text-sub">空闲</span>}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[13px] text-fg/80">{a.lastOutput}</p>
                    <div className="mt-auto grid grid-cols-2 gap-y-1 pt-3 text-[11px] text-sub">
                      <span>心跳 {relTime(a.lastHeartbeatAt, now())}</span>
                      <span>运行 {duration(now().getTime() - new Date(a.startedAt).getTime())}</span>
                      <span className={a.errorCount24h ? 'text-warn' : ''}>24h 错误 {a.errorCount24h}</span>
                      <span>{a.model ? `${a.model} · ${usd(a.modelCostTodayUsd)}` : a.noModel ? '无模型' : '未调用模型'}</span>
                    </div>
                    <div className="mt-2"><Tag tone="agent" className="text-[11px]">{IMPL_LABEL[a.impl]}</Tag></div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </DataState>
      </Panel>
    </div>
  )
}
