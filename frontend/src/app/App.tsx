import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider, type RouteObject } from 'react-router-dom'
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

export const routes: RouteObject[] = [
  {
    path: '/', element: <Layout />, children: [
      { index: true, element: <Overview /> },
      { path: 'discover', element: <Discover /> },
      { path: 'token/:address', element: <TokenPage /> },
      { path: 'agents', element: <Agents /> },
      { path: 'activity', element: <Activity /> },
      { path: 'portfolio', element: <Portfolio /> },
      { path: 'orders', element: <Orders /> },
      { path: 'strategy', element: <Strategy /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]

export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })

export function App() {
  const router = createBrowserRouter(routes)
  return <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>
}
