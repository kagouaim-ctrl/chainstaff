import { NavLink, Link, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { useApp } from './store'
import { usePortfolio, usePositions, useOrders } from '@/api/hooks'
import { Button, Chg, Dialog, ModeTag, Signed, TokenAvatar, cx } from '@/components/ui'
import { IPause, IPlay, ISearch, IUser, ILock, ICompass, IBot, IPulse, IPie, IList, ISliders } from '@/components/icons'
import { usd } from '@/lib/format'
import { TERMINAL } from '@/domain/orderMachine'

/** 一级入口：照 fomo 网页版放在顶栏（手机上是底部悬浮导航）。发现是首页。 */
export const NAV = [
  { to: '/', label: '发现', icon: ICompass, end: true, mobile: true },
  { to: '/agents', label: 'Agent', icon: IBot, mobile: true },
  { to: '/activity', label: '动态', icon: IPulse, mobile: true },
  { to: '/orders', label: '交易', icon: IList, mobile: false },
  { to: '/strategy', label: '策略', icon: ISliders, mobile: true },
]
/** 总览和持仓不在导航里，从顶栏金额进入（手机上是底部导航的「资产」）。 */
const MOBILE_EXTRA = { to: '/portfolio', label: '资产', icon: IPie, end: false, mobile: true }

export function BottomNav() {
  return (
    <nav aria-label="主导航（手机）" className="fixed inset-x-0 bottom-3 z-30 flex justify-center px-4 md:hidden">
      <div className="flex w-full max-w-sm items-center justify-around rounded-full border border-line bg-panel px-2 py-1.5">
        {[...NAV.filter((n) => n.mobile), MOBILE_EXTRA].map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} aria-label={n.label}
            className={({ isActive }) => cx('flex flex-col items-center gap-0.5 rounded-full px-3 py-1 text-[10px]', isActive ? 'text-fg' : 'text-sub')}>
            <n.icon width={20} height={20} />{n.label}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

/** 顶栏：照 fomo，高 64、不吸顶；左边 logo + 入口，中间 384×44 搜索，右边现金 / 24 小时两个盒子和头像。 */
export function TopBar() {
  const setSearchOpen = useApp((s) => s.setSearchOpen)
  return (
    <header className="relative z-20 h-16 border-b border-line bg-bg">
      <div className="flex h-16 items-center gap-4 px-4">
        <Link to="/" className="shrink-0 text-[22px] font-bold leading-none tracking-[-0.9px]" aria-label="Meme Agent 首页">memeagent</Link>
        <nav aria-label="主导航" className="hidden items-center gap-1 lg:flex">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cx('rounded-lg px-3 py-2 text-[14px] leading-5', isActive ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>
              {n.label}
            </NavLink>
          ))}
        </nav>

        <button onClick={() => setSearchOpen(true)} aria-label="搜索（快捷键 /）"
          className="ml-auto flex h-11 w-11 shrink-0 items-center justify-center gap-2 rounded-lg border border-line bg-panel text-[14px] text-sub hover:text-fg sm:w-[384px] sm:justify-start sm:px-3 xl:mx-auto">
          <ISearch width={18} height={18} />
          <span className="hidden truncate font-normal sm:inline">搜索代币、合约地址、Agent、订单...</span>
          <span className="ml-auto hidden items-center gap-1 sm:flex">
            <kbd className="rounded-md border border-line px-1.5 py-0.5 font-sans text-[12px]">粘贴</kbd>
            <kbd className="rounded-md border border-line px-1.5 py-0.5 font-sans text-[12px]">/</kbd>
          </span>
        </button>

        <div className="flex shrink-0 items-center gap-2">
          <ModeSwitchBadge />
          <div className="md:hidden"><PauseButton /></div>
          <div className="hidden md:block"><PauseButton /></div>
          <Balance />
          <span className="hidden h-9 w-9 items-center justify-center rounded-full bg-raised text-sub sm:flex" title="DI JIAHAO" aria-label="账户"><IUser /></span>
        </div>
      </div>
    </header>
  )
}

/** 顶栏金额：照 fomo 的现金 / 24 小时两个盒子；点开是总览和持仓。 */
function Balance() {
  const pf = usePortfolio()
  const pos = usePositions()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const loc = useLocation()
  useEffect(() => setOpen(false), [loc.pathname])
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown); window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])
  const p = pf.data
  const eq = p?.equity ?? []
  const day = eq.length > 24 ? eq[eq.length - 1].value - eq[eq.length - 25].value : 0
  const used = p ? p.todayLossUsd / p.todayLossLimitUsd : 0
  return (
    <div ref={ref} className="relative hidden sm:block">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label="资产：总览和持仓" className="flex items-center gap-2">
        <span className="flex h-11 items-center gap-3 rounded-lg border border-line px-3 text-left hover:bg-raised">
          <span className="leading-tight">
            <span className="num block text-[14px]">{p ? usd(p.cashUsd) : '--'}</span>
            <span className="block text-[12px] font-normal text-sub">现金</span>
          </span>
          <span className="rounded-md bg-raised px-2 py-1 text-[12px]">充值</span>
        </span>
        <span className="hidden h-11 flex-col justify-center rounded-lg border border-line px-3 text-left leading-tight hover:bg-raised md:flex">
          <span className="num text-[14px]">{p ? usd(p.totalUsd) : '--'}</span>
          <span className="text-[12px] font-normal"><Signed v={day}>{usd(day, { sign: true })}</Signed> <span className="text-sub">24小时</span></span>
        </span>
      </button>
      {open && p && (
        <div role="dialog" aria-label="资产" className="anim-enter absolute right-0 top-[52px] z-40 w-[400px] rounded-xl border border-line bg-panel p-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div className="text-[12px] font-normal text-sub">总资产 · 模拟盘</div>
              <div className="num text-[32px] font-medium leading-9 tracking-[-1.5px]">{usd(p.totalUsd)}</div>
            </div>
            <Signed v={day} className="text-[14px]">{usd(day, { sign: true })} 24小时</Signed>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {([['现金', usd(p.cashUsd), 0], ['已实现', usd(p.realizedUsd, { sign: true }), p.realizedUsd], ['未实现', usd(p.unrealizedUsd, { sign: true }), p.unrealizedUsd]] as const).map(([k, v, n]) => (
              <div key={k} className="rounded-lg border border-line px-3 py-2">
                <div className="text-[12px] font-normal text-sub">{k}</div>
                <Signed v={n} className={cx('text-[14px]', n === 0 && 'text-fg')}>{v}</Signed>
              </div>
            ))}
          </div>
          <div className="mt-3">
            <div className="flex justify-between text-[12px] font-normal text-sub"><span>今日亏损 {usd(p.todayLossUsd)} / {usd(p.todayLossLimitUsd)}</span><span>到上限当日自动暂停</span></div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-raised"><div className={cx('h-full rounded-full', used > 0.8 ? 'bg-down' : used > 0.5 ? 'bg-warn' : 'bg-up')} style={{ width: `${Math.min(100, used * 100)}%` }} /></div>
          </div>
          <div className="mt-4 flex items-center justify-between"><h3 className="text-[14px]">持仓</h3><span className="text-[12px] font-normal text-sub">{pos.data?.length ?? 0} 个</span></div>
          <ul className="mt-1">
            {(pos.data ?? []).map((x) => (
              <li key={x.token.address}>
                <Link to={`/token/${x.token.address}`} className="-mx-2 flex items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-raised">
                  <TokenAvatar symbol={x.token.symbol} hue={x.token.hue} size={28} />
                  <span className="flex-1 text-[14px]">{x.token.symbol}</span>
                  <span className="text-right">
                    <span className="num block text-[14px]">{usd(x.valueUsd)}</span>
                    <Chg v={(x.unrealizedUsd / x.costBasisUsd) * 100} className="block text-[12px]" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Link to="/overview" className="flex h-9 items-center justify-center rounded-lg bg-raised text-[14px] hover:text-fg">完整总览</Link>
            <Link to="/portfolio" className="flex h-9 items-center justify-center rounded-lg bg-raised text-[14px] hover:text-fg">全部持仓</Link>
          </div>
        </div>
      )}
    </div>
  )
}

/** 底部状态栏：照 fomo 的行情条。行情未接入时不显示假价格。 */
export function FooterBar() {
  return (
    <footer className="fixed inset-x-0 bottom-0 z-20 hidden h-8 items-center gap-4 border-t border-line bg-bg px-4 text-[12px] md:flex">
      {['BTC', 'ETH', 'SOL'].map((s) => (
        <span key={s} className="flex items-center gap-1.5"><span className="text-sub">{s}</span><span className="text-mute">--</span></span>
      ))}
      <span className="text-mute">行情未接入</span>
      <span className="ml-auto flex items-center gap-3 text-sub">
        <span className="flex items-center gap-1.5 text-warn"><span className="h-1.5 w-1.5 rounded-full bg-warn" />演示</span>
        <span>后端未接入</span><span>·</span><span>隐私</span><span>·</span><span>条款</span><span>·</span><span>帮助</span>
      </span>
    </footer>
  )
}

function ModeSwitchBadge() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="当前模式：模拟盘" className="flex items-center gap-1 whitespace-nowrap rounded-lg border border-paper/40 bg-paper/10 px-2 py-1 text-[12px] font-medium text-paper">
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
  const cls = rail ? 'flex w-[62px] flex-col items-center gap-1 rounded-xl py-2 text-[11px] font-medium' : 'h-11 px-3'
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
