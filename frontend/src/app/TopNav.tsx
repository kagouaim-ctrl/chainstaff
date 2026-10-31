import { NavLink, Link } from 'react-router-dom'
import { useState } from 'react'
import { useApp } from './store'
import { useAgents, useMainAgent, usePortfolio, useOrders } from '@/api/hooks'
import { Button, Dialog, ModeTag, cx } from '@/components/ui'
import { IPause, IPlay, ISearch, IUser, ILock } from '@/components/icons'
import { usd } from '@/lib/format'
import { TERMINAL } from '@/domain/orderMachine'

export const NAV = [
  { to: '/', label: '总览', end: true },
  { to: '/discover', label: '发现' },
  { to: '/agents', label: 'Agents' },
  { to: '/activity', label: '实时动态' },
  { to: '/portfolio', label: '持仓' },
  { to: '/orders', label: '交易记录' },
  { to: '/strategy', label: '策略' },
]

export function TopNav() {
  const agents = useAgents()
  const pf = usePortfolio()
  const ma = useMainAgent()
  const setSearchOpen = useApp((s) => s.setSearchOpen)
  const running = agents.data?.filter((a) => a.status === 'running' || a.status === 'idle').length ?? 0
  const total = agents.data?.length ?? 0
  const degraded = agents.data?.some((a) => a.status !== 'running' && a.status !== 'idle')

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-3 sm:px-5">
        <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold tracking-tight" aria-label="Meme Agent 首页">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-agent text-[13px] font-bold text-white">M</span>
          <span className="hidden lg:inline">Meme Agent</span>
        </Link>

        <nav aria-label="主导航" className="scrollbar-none -mx-1 flex min-w-0 flex-1 items-center overflow-x-auto">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cx('whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors',
                isActive ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>
              {n.label}
            </NavLink>
          ))}
        </nav>

        <button onClick={() => setSearchOpen(true)} aria-label="搜索（快捷键 /）"
          className="flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[13px] text-sub hover:text-fg xl:w-60">
          <ISearch />
          <span className="hidden truncate xl:inline">搜索代币、地址、订单</span>
          <kbd className="ml-auto hidden rounded border border-line px-1.5 text-[11px] xl:inline">/</kbd>
        </button>

        <div className="flex shrink-0 items-center gap-2">
          <ModeSwitchBadge />
          <Link to="/agents" title={`${ma.data?.name ?? '主 Agent'}：${running}/${total} 个角色正常`}
            className="hidden items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13px] text-sub hover:text-fg md:flex">
            <span className={cx('h-2 w-2 rounded-full', degraded ? 'bg-warn' : 'bg-up')} />
            <span className="num">{running}/{total}</span>
            <span className="hidden 2xl:inline">运行中</span>
          </Link>
          <PauseButton />
          <Link to="/portfolio" className="hidden items-center rounded-lg px-2 py-1.5 text-[13px] font-medium hover:bg-raised sm:flex" title="总资产（模拟盘）">
            <span className="num">{pf.data ? usd(pf.data.totalUsd) : '--'}</span>
          </Link>
          <span className="flex h-8 w-8 items-center justify-center rounded-full border border-line bg-panel text-sub" title="DI JIAHAO" aria-label="账户">
            <IUser />
          </span>
        </div>
      </div>
    </header>
  )
}

function ModeSwitchBadge() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="当前模式：模拟盘" className="flex items-center gap-1 rounded-lg border border-paper/40 bg-paper/10 px-2 py-1 text-[12px] font-semibold text-paper">
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

function PauseButton() {
  const { paused, pause, resume } = useApp()
  const orders = useOrders()
  const [open, setOpen] = useState(false)
  const inflight = orders.data?.filter((o) => !TERMINAL.includes(o.status) && o.status !== 'CREATED').length ?? 0
  if (paused) {
    return (
      <Button variant="outline" onClick={resume} aria-label="恢复交易" className="px-2 sm:px-3">
        <IPlay /><span className="hidden sm:inline">恢复</span>
      </Button>
    )
  }
  return (
    <>
      <Button variant="danger" onClick={() => setOpen(true)} aria-label="紧急暂停" className="px-2 sm:px-3">
        <IPause /><span className="hidden sm:inline">紧急暂停</span>
      </Button>
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
