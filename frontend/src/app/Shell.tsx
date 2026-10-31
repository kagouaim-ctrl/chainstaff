import { NavLink, Link, useLocation } from 'react-router-dom'
import { useState } from 'react'
import { useApp } from './store'
import { useAgents, useMainAgent, usePortfolio, useOrders } from '@/api/hooks'
import { Button, Dialog, ModeTag, cx } from '@/components/ui'
import { IPause, IPlay, ISearch, IUser, ILock, ICompass, IGrid, IBot, IPulse, IPie, IList, ISliders } from '@/components/icons'
import { usd } from '@/lib/format'
import { TERMINAL } from '@/domain/orderMachine'

/** 一级入口（参考 fomo 网页版：左侧窄栏；手机上是底部悬浮导航）。发现是首页。 */
export const NAV = [
  { to: '/', label: '发现', icon: ICompass, end: true, mobile: true },
  { to: '/overview', label: '总览', icon: IGrid, mobile: false },
  { to: '/agents', label: 'Agent', icon: IBot, mobile: true },
  { to: '/activity', label: '动态', icon: IPulse, mobile: true },
  { to: '/portfolio', label: '持仓', icon: IPie, mobile: true },
  { to: '/orders', label: '交易', icon: IList, mobile: false },
  { to: '/strategy', label: '策略', icon: ISliders, mobile: true },
]

export function SideRail() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-[76px] flex-col items-center border-r border-line bg-bg py-3 md:flex" aria-label="侧边栏">
      <Link to="/" aria-label="Meme Agent 首页" className="mb-4 flex h-9 w-9 items-center justify-center rounded-xl bg-agent text-sm font-bold text-white">M</Link>
      <nav aria-label="主导航" className="flex flex-1 flex-col items-center gap-1">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}
            className={({ isActive }) => cx('flex w-[62px] flex-col items-center gap-1 rounded-xl py-2 text-[11px] font-medium transition-colors',
              isActive ? 'bg-raised text-fg' : 'text-sub hover:bg-raised/60 hover:text-fg')}>
            <n.icon width={20} height={20} />{n.label}
          </NavLink>
        ))}
      </nav>
      <PauseButton rail />
    </aside>
  )
}

export function BottomNav() {
  return (
    <nav aria-label="主导航（手机）" className="fixed inset-x-0 bottom-3 z-30 flex justify-center px-3 md:hidden">
      <div className="flex w-full max-w-sm items-center justify-around rounded-full border border-line bg-panel/95 px-2 py-1.5 shadow-2xl backdrop-blur">
        {NAV.filter((n) => n.mobile).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} aria-label={n.label}
            className={({ isActive }) => cx('flex flex-col items-center gap-0.5 rounded-full px-3 py-1 text-[10px]', isActive ? 'text-fg' : 'text-sub')}>
            <n.icon width={20} height={20} />{n.label}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

const TITLES: Record<string, string> = { '/': '发现', '/overview': '总览', '/agents': 'Agent', '/activity': '实时动态', '/portfolio': '持仓', '/orders': '交易记录', '/strategy': '策略' }

export function TopBar() {
  const agents = useAgents()
  const pf = usePortfolio()
  const ma = useMainAgent()
  const loc = useLocation()
  const setSearchOpen = useApp((s) => s.setSearchOpen)
  const ok = agents.data?.filter((a) => a.status === 'running' || a.status === 'idle').length ?? 0
  const total = agents.data?.length ?? 0
  const degraded = agents.data?.some((a) => a.status !== 'running' && a.status !== 'idle')
  const title = TITLES[loc.pathname] ?? (loc.pathname.startsWith('/token/') ? '代币' : '')

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
      <div className="flex h-16 items-center gap-3 px-3 sm:px-5">
        <Link to="/" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-agent text-[13px] font-bold text-white md:hidden" aria-label="首页">M</Link>
        <h1 className="hidden min-w-[88px] text-lg font-semibold tracking-tight sm:block">{title}</h1>

        <button onClick={() => setSearchOpen(true)} aria-label="搜索（快捷键 /）"
          className="ml-auto flex h-10 w-10 shrink-0 items-center justify-center gap-2 rounded-xl border border-line bg-panel text-[13px] text-sub hover:text-fg sm:mx-auto sm:w-full sm:max-w-[384px] sm:shrink sm:justify-start sm:px-3">
          <ISearch />
          <span className="hidden truncate sm:inline">搜索代币、合约地址、Agent、订单</span>
          <kbd className="ml-auto hidden rounded border border-line px-1.5 text-[11px] sm:inline">/</kbd>
        </button>

        <div className="flex shrink-0 items-center gap-2 sm:gap-4">
          <ModeSwitchBadge />
          <Link to="/agents" title={`${ma.data?.name ?? '主 Agent'}：${ok}/${total} 个角色正常`} className="hidden items-center gap-1.5 text-[13px] text-sub hover:text-fg lg:flex">
            <span className={cx('h-2 w-2 rounded-full', degraded ? 'bg-warn' : 'bg-up')} />
            <span className="num">{ok}/{total}</span>
          </Link>
          <Link to="/portfolio" className="hidden text-right leading-tight sm:block" title="模拟盘">
            <div className="text-[11px] text-sub">可用</div>
            <div className="num text-[13px] font-semibold">{pf.data ? usd(pf.data.cashUsd) : '--'}</div>
          </Link>
          <Link to="/portfolio" className="hidden text-right leading-tight sm:block" title="模拟盘">
            <div className="text-[11px] text-sub">总资产</div>
            <div className="num text-[13px] font-semibold">{pf.data ? usd(pf.data.totalUsd) : '--'}</div>
          </Link>
          <div className="md:hidden"><PauseButton /></div>
          <span className="hidden h-9 w-9 items-center justify-center rounded-full border border-line bg-panel text-sub sm:flex" title="DI JIAHAO" aria-label="账户"><IUser /></span>
        </div>
      </div>
    </header>
  )
}

function ModeSwitchBadge() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="当前模式：模拟盘" className="flex items-center gap-1 whitespace-nowrap rounded-lg border border-paper/40 bg-paper/10 px-2 py-1 text-[12px] font-semibold text-paper">
        <span className="h-1.5 w-1.5 rounded-full bg-paper" />模拟盘
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="当前是模拟盘"
        footer={<Button variant="outline" onClick={() => setOpen(false)}>知道了</Button>}>
        <p>所有订单都按真实报价模拟成交，不动用真实资金。模拟、实盘、回测数据分开存储，颜色不同：<ModeTag mode="paper" /> <ModeTag mode="live" /> <ModeTag mode="backtest" /></p>
        <p className="mt-3 flex items-center gap-1.5 text-sub"><ILock /> 实盘已锁定。解锁条件见「策略」页。</p>
      </Dialog>
    </>
  )
}

function PauseButton({ rail }: { rail?: boolean }) {
  const { paused, pause, resume } = useApp()
  const orders = useOrders()
  const [open, setOpen] = useState(false)
  const inflight = orders.data?.filter((o) => !TERMINAL.includes(o.status) && o.status !== 'CREATED').length ?? 0
  const cls = rail ? 'flex w-[62px] flex-col items-center gap-1 rounded-xl py-2 text-[11px] font-medium' : 'h-9 px-2.5'
  if (paused) return <Button variant="outline" onClick={resume} aria-label="恢复交易" className={cls}><IPlay width={rail ? 20 : 16} height={rail ? 20 : 16} />{rail && '恢复'}</Button>
  return (
    <>
      <Button variant="danger" onClick={() => setOpen(true)} aria-label="紧急暂停" className={cls}><IPause width={rail ? 20 : 16} height={rail ? 20 : 16} />{rail && '暂停'}</Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="确认紧急暂停？"
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
          <Button variant="down" onClick={() => { pause(); setOpen(false) }}>确认暂停</Button>
        </>}>
        <ul className="list-disc space-y-1.5 pl-5">
          <li>暂停后 1 秒内不再产生新的交易指令，策略到风控之后的所有新方案都会被拒绝。</li>
          <li>已提交的 <b className="num">{inflight}</b> 笔交易会继续追踪确认，不会被遗忘或重发。</li>
          <li>研究任务可以继续运行，但不会下单。</li>
          <li>恢复前会先对账链上余额和订单状态。</li>
        </ul>
        <p className="mt-3 rounded-lg border border-dashed border-warn/50 p-2.5 text-warn">后端未接入：现在点确认只会在本页面显示暂停状态，不会真正通知任何服务。</p>
      </Dialog>
    </>
  )
}
