import { useEffect } from 'react'
import { Outlet, useLocation, useSearchParams } from 'react-router-dom'
import { TopBar, BottomNav, FooterBar } from './Shell'
import { DemoBanner, PauseBanner } from './Banners'
import { ResearchRunner } from '@/features/Research'
import { WalletPicker } from '@/wallet/WalletUI'
import { SearchPalette } from './Search'
import { Toast } from './Toast'
import { AgentDrawer } from '@/features/AgentDrawer'
import { OrderDrawer } from '@/features/OrderDrawer'
import { QuickBuyDialog } from '@/features/QuickBuy'
import { AlertRunner } from '@/features/alerts'

const TITLES: Record<string, string> = { '/': '发现', '/discover': '发现', '/overview': '总览', '/agents': 'AI Agent', '/activity': '动态', '/portfolio': '持仓', '/orders': '订单', '/strategy': '策略', '/new': '新币', '/smart-money': '聪明钱', '/paper': '模拟盘' }

/** 子页面（/agents/about、/wallet/地址）用上一级的标题，只有真的没有这个页面时才写「页面不存在」 */
export function titleOf(pathname: string): string {
  if (pathname.startsWith('/legal/')) return '说明'
  if (pathname.startsWith('/wallet/')) return '钱包'
  const path = pathname.replace(/\/+$/, '') || '/'
  return TITLES[path] ?? TITLES[`/${path.split('/')[1]}`] ?? '页面不存在'
}

export function Layout() {
  const { pathname } = useLocation()
  useEffect(() => {
    // 代币页自己设置标题（带价格）
    if (!pathname.startsWith('/token/')) document.title = `${titleOf(pathname)} · KAGOUAI`
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
        <ResearchRunner />
        <WalletPicker />
        <PauseBanner />
        <main className="w-full flex-1 px-4 pb-24 pt-4 md:pb-12">
          <Outlet />
        </main>
      </div>
      <FooterBar />
      {!pathname.startsWith('/token/') && <BottomNav />}
      <SearchPalette />
      <QuickBuyDialog />
      <AlertRunner />
      <Toast />
      <AgentDrawer role={agent} onClose={close('agent')} />
      <OrderDrawer id={order} onClose={close('order')} />
    </div>
  )
}
