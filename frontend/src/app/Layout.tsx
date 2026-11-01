import { useEffect } from 'react'
import { Outlet, useLocation, useSearchParams } from 'react-router-dom'
import { TopBar, BottomNav, FooterBar } from './Shell'
import { DemoBanner, PauseBanner } from './Banners'
import { SearchPalette } from './Search'
import { Toast } from './Toast'
import { AgentDrawer } from '@/features/AgentDrawer'
import { OrderDrawer } from '@/features/OrderDrawer'

const TITLES: Record<string, string> = { '/': '发现', '/overview': '总览', '/agents': 'Agent', '/activity': '动态', '/portfolio': '持仓', '/orders': '交易', '/strategy': '策略' }

export function Layout() {
  const { pathname } = useLocation()
  useEffect(() => {
    // 代币页自己设置标题（带价格）
    if (!pathname.startsWith('/token/')) document.title = `${TITLES[pathname] ?? '页面不存在'} · memeagent`
  }, [pathname])
  const [params, setParams] = useSearchParams()
  const agent = params.get('agent')
  const order = params.get('order')
  const close = (k: string) => () => { const p = new URLSearchParams(params); p.delete(k); setParams(p, { replace: true }) }
  return (
    <div className="min-h-full">
      <div className="flex min-h-full flex-col">
        <TopBar />
        <DemoBanner />
        <PauseBanner />
        <main className="w-full flex-1 px-4 pb-24 pt-4 md:pb-12">
          <Outlet />
        </main>
      </div>
      <FooterBar />
      <BottomNav />
      <SearchPalette />
      <Toast />
      <AgentDrawer role={agent} onClose={close('agent')} />
      <OrderDrawer id={order} onClose={close('order')} />
    </div>
  )
}
