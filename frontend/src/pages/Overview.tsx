import { Link } from 'react-router-dom'
import { useEvents, useMainAgent, useOrders, usePipeline, usePortfolio, now } from '@/api/hooks'
import { DataState, Panel, Stat, Tag, ModeTag, Signed, cx, StatusDot, AGENT_STATUS, TokenAvatar } from '@/components/ui'
import { usd, relTime, clock } from '@/lib/format'
import { STATUS_LABEL, STATUS_TONE } from '@/domain/orderMachine'
import { ROLE_MAP } from '@/domain/agents'
import { useDrawerLink } from '@/app/links'
import { IChevron } from '@/components/icons'

export function Overview() {
  const pf = usePortfolio()
  const ma = useMainAgent()
  return (
    <div className="space-y-4">
      <Panel demo bodyClass="p-4 sm:p-5" title={
        <span className="flex items-center gap-2">
          总览
          {ma.data && <span className="hidden items-center gap-1.5 text-xs font-normal text-sub sm:flex"><StatusDot status={ma.data.status} pulse />主 Agent「{ma.data.name}」{AGENT_STATUS[ma.data.status].label} · 策略 {ma.data.strategyName} v{ma.data.strategyVersion}</span>}
        </span>
      } right={<ModeTag mode="paper" />}>
        <DataState q={pf} rows={2}>
          {(p) => {
            const used = p.todayLossUsd / p.todayLossLimitUsd
            return (
              <div className="grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-3 xl:grid-cols-5">
                <Stat label="总资产（模拟）" value={usd(p.totalUsd)} sub={`可用 ${usd(p.cashUsd)}`} />
                <Stat label="已实现盈亏" value={<Signed v={p.realizedUsd}>{usd(p.realizedUsd, { sign: true })}</Signed>} sub="仅计入已最终确认的成交" />
                <Stat label="未实现盈亏" value={<Signed v={p.unrealizedUsd}>{usd(p.unrealizedUsd, { sign: true })}</Signed>} sub="估算，未扣除卖出成本" />
                <Stat label="交易成本" value={usd(p.costsUsd)} sub="网络费 + 滑点 + 平台手续费" />
                <div className="col-span-2 md:col-span-1">
                  <div className="text-xs text-sub">风险状态 · 今日亏损</div>
                  <div className="num mt-1 text-xl font-semibold sm:text-2xl">{usd(p.todayLossUsd)} <span className="text-sm font-normal text-sub">/ {usd(p.todayLossLimitUsd)}</span></div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-raised" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(used * 100)} aria-label="今日亏损已用比例">
                    <div className={cx('h-full rounded-full', used > 0.8 ? 'bg-down' : used > 0.5 ? 'bg-warn' : 'bg-up')} style={{ width: `${Math.min(100, used * 100)}%` }} />
                  </div>
                  <div className="mt-1 text-xs text-sub">已用 {(used * 100).toFixed(0)}%，到上限当日自动暂停</div>
                </div>
              </div>
            )
          }}
        </DataState>
      </Panel>

      <Pipeline />

      <div className="grid grid-cols-1 gap-4 [&>*]:min-w-0 xl:grid-cols-2">
        <RecentOrders />
        <SystemEvents />
      </div>
    </div>
  )
}

function Pipeline() {
  const q = usePipeline()
  const open = useDrawerLink()
  return (
    <Panel demo title="Agent 流水线" right={<span className="hidden text-xs text-sub sm:inline">每一环：当前处理数 · 最近一个代币</span>} bodyClass="p-0">
      <DataState q={q} rows={1}>
        {(stages) => (
          <ol className="scrollbar-none flex overflow-x-auto">
            {stages.map((s, i) => (
              <li key={s.key} className="flex min-w-[132px] flex-1 items-stretch">
                <button onClick={() => open('agent', s.agent)} className="group flex flex-1 flex-col justify-start px-4 py-4 text-left transition-colors hover:bg-raised">
                  <div className="flex items-center gap-1.5 text-xs text-sub">
                    <span className="num text-[11px] text-sub/70">{i + 1}</span>{s.label}
                    {ROLE_MAP[s.agent].short !== s.label && <span className="text-sub/60">· {ROLE_MAP[s.agent].short}</span>}
                  </div>
                  <div className={cx('num mt-1.5 text-2xl font-semibold', s.inFlight ? 'text-fg' : 'text-sub')}>{s.inFlight}</div>
                  <div className="mt-1 truncate text-xs text-sub">{s.lastToken ? <><span className="text-fg/80">{s.lastToken}</span> · {s.lastAt ? relTime(s.lastAt, now()) : ''}</> : '—'}</div>
                  <div className={cx('mt-2 h-0.5 w-8 rounded', s.inFlight > 0 ? 'bg-agent' : 'bg-transparent')} />
                </button>
                {i < stages.length - 1 && <span className="flex items-center text-line" aria-hidden><IChevron /></span>}
              </li>
            ))}
          </ol>
        )}
      </DataState>
    </Panel>
  )
}

function RecentOrders() {
  const q = useOrders()
  const open = useDrawerLink()
  return (
    <Panel demo title="最近交易" right={<Link to="/orders" className="text-xs text-sub hover:text-fg">全部</Link>} bodyClass="p-0">
      <DataState q={q} isEmpty={(d) => d.length === 0} empty="还没有交易。Agent 研究出符合策略的机会后会出现在这里。">
        {(orders) => (
          <ul>
            {orders.slice(0, 6).map((o) => (
              <li key={o.id}>
                <button onClick={() => open('order', o.id)} className="flex w-full items-center gap-3 border-b border-line px-4 py-3 text-left last:border-0 hover:bg-raised">
                  <TokenAvatar symbol={o.token.symbol} hue={o.token.hue} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-[13px]">
                      <span className={o.side === 'BUY' ? 'text-up' : 'text-down'}>{o.side === 'BUY' ? '买入' : '卖出'}</span>
                      <span className="font-medium">{o.token.symbol}</span>
                      <ModeTag mode={o.mode} />
                    </div>
                    <div className="truncate text-xs text-sub">{o.failureReason ?? `${o.id} · ${clock(o.createdAt)}`}</div>
                  </div>
                  <span className="num text-[13px]">{usd(o.requestedUsd)}</span>
                  <Tag tone={STATUS_TONE[o.status]} className="w-16 justify-center">{STATUS_LABEL[o.status]}</Tag>
                </button>
              </li>
            ))}
          </ul>
        )}
      </DataState>
    </Panel>
  )
}

function SystemEvents() {
  const q = useEvents()
  return (
    <Panel demo title="系统事件" right={<Link to="/activity" className="text-xs text-sub hover:text-fg">实时动态</Link>} bodyClass="p-0">
      <DataState q={q} isEmpty={(d) => d.length === 0}>
        {(evs) => (
          <ul>
            {evs.filter((e) => e.status !== 'info').slice(0, 6).map((e) => (
              <li key={e.id} className="flex items-start gap-3 border-b border-line px-4 py-3 text-[13px] last:border-0">
                <span className={cx('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', e.status === 'error' ? 'bg-down' : e.status === 'warn' ? 'bg-warn' : 'bg-up')} />
                <div className="min-w-0 flex-1">
                  <div className="text-fg/90">{e.message}</div>
                  <div className="text-xs text-sub">{ROLE_MAP[e.agent].short} · {e.type} · {relTime(e.at, now())}</div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </DataState>
    </Panel>
  )
}
