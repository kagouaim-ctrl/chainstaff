import { Outlet, useSearchParams } from 'react-router-dom'
import { SideRail, TopBar, BottomNav } from './Shell'
import { DemoBanner, PauseBanner } from './Banners'
import { SearchPalette } from './Search'
import { Toast } from './Toast'
import { AgentDrawer } from '@/features/AgentDrawer'
import { OrderDrawer } from '@/features/OrderDrawer'

export function Layout() {
  const [params, setParams] = useSearchParams()
  const agent = params.get('agent')
  const order = params.get('order')
  const close = (k: string) => () => { const p = new URLSearchParams(params); p.delete(k); setParams(p, { replace: true }) }
  return (
    <div className="min-h-full md:pl-[76px]">
      <SideRail />
      <div className="flex min-h-full flex-col">
        <TopBar />
        <DemoBanner />
        <PauseBanner />
        <main className="w-full flex-1 px-3 pb-24 pt-4 sm:px-5 md:pb-6">
          <Outlet />
        </main>
      </div>
      <BottomNav />
      <SearchPalette />
      <Toast />
      <AgentDrawer role={agent} onClose={close('agent')} />
      <OrderDrawer id={order} onClose={close('order')} />
    </div>
  )
}
