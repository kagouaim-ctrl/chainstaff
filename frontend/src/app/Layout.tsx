import { Outlet, useSearchParams } from 'react-router-dom'
import { TopNav } from './TopNav'
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
    <div className="flex min-h-full flex-col">
      <TopNav />
      <DemoBanner />
      <PauseBanner />
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-3 py-4 sm:px-5 sm:py-5">
        <Outlet />
      </main>
      <SearchPalette />
      <Toast />
      <AgentDrawer role={agent} onClose={close('agent')} />
      <OrderDrawer id={order} onClose={close('order')} />
    </div>
  )
}
