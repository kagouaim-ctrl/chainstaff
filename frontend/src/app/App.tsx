import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, createHashRouter, RouterProvider, type RouteObject } from 'react-router-dom'
import { Layout } from './Layout'
import { Overview } from '@/pages/Overview'
import { Discover } from '@/pages/Discover'
import { TokenPage } from '@/pages/TokenPage'
import { Agents } from '@/pages/Agents'
import { Activity } from '@/pages/Activity'
import { Portfolio } from '@/pages/Portfolio'
import { Orders } from '@/pages/Orders'
import { Strategy } from '@/pages/Strategy'
import { NotFound } from '@/pages/NotFound'
import { MyWallet } from '@/pages/MyWallet'
import { AgentBeta } from '@/pages/AgentBeta'
import { Legal } from '@/pages/Legal'
import { NewTokens } from '@/pages/NewTokens'
import { SmartMoney, WalletPage } from '@/features/WalletIntel'
import { LIVE } from '@/api/live'

export const routes: RouteObject[] = [
  {
    path: '/', element: <Layout />, children: [
      { index: true, element: <Discover /> },
      { path: 'overview', element: <Overview /> },
      { path: 'discover', element: <Discover /> },
      { path: 'token/:address', element: <TokenPage /> },
      { path: 'new', element: <NewTokens /> },
      { path: 'smart-money', element: <SmartMoney /> },
      { path: 'wallet/:address', element: <WalletPage /> },
      // 实时模式下没有 Agent 后端时不展示空的角色列表，改成说明页；持仓显示真实钱包而不是模拟盘
      { path: 'agents', element: <Agents /> },
      { path: 'agents/about', element: <AgentBeta /> },
      { path: 'activity', element: <Activity /> },
      // 实时模式：先是钱包的真实持仓，下面保留原来的资产总览
      { path: 'portfolio', element: LIVE ? <><MyWallet /><div className="mt-10 border-t border-line pt-6"><Portfolio /></div></> : <Portfolio /> },
      { path: 'legal/:doc', element: <Legal /> },
      { path: 'orders', element: <Orders /> },
      { path: 'strategy', element: <Strategy /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]

export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })

export function App() {
  // 官网是纯静态托管，深链接没有回退规则，所以挂在子路径时用 hash 路由（BASE 不是 / 时自动启用）
  const router = import.meta.env.BASE_URL === '/' ? createBrowserRouter(routes) : createHashRouter(routes)
  return <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>
}
