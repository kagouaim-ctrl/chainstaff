import { useQuery } from '@tanstack/react-query'
import { source } from './source'

const live = () => source().info.kind === 'live'
export const useTokens = () => useQuery({ queryKey: ['tokens'], queryFn: () => source().tokens(), refetchInterval: live() ? 60_000 : false })
export const useToken = (a: string) => useQuery({ queryKey: ['token', a], queryFn: () => source().token(a), refetchInterval: live() ? 30_000 : false })
export const useCandles = (a: string) => useQuery({ queryKey: ['candles', a], queryFn: () => source().candles(a) })
export const useHolders = (a: string) => useQuery({ queryKey: ['holders', a], queryFn: () => source().holders(a) })
export const useChat = (a: string) => useQuery({ queryKey: ['chat', a], queryFn: () => source().chat(a) })
export const useAgentDM = () => useQuery({ queryKey: ['agentDM'], queryFn: () => source().agentDM() })
export const useTheses = (a: string) => useQuery({ queryKey: ['theses', a], queryFn: () => source().theses(a) })
export const useResearchSteps = (a: string) => useQuery({ queryKey: ['research', a], queryFn: () => source().research(a) })
export const useTrades = (a: string) => useQuery({ queryKey: ['trades', a], queryFn: () => source().trades(a) })
export const useMainAgent = () => useQuery({ queryKey: ['mainAgent'], queryFn: () => source().mainAgent() })
export const useAgents = () => useQuery({ queryKey: ['agents'], queryFn: () => source().agents() })
export const usePipeline = () => useQuery({ queryKey: ['pipeline'], queryFn: () => source().pipeline() })
export const useEvents = () => useQuery({ queryKey: ['events'], queryFn: () => source().events() })
export const useOrders = () => useQuery({ queryKey: ['orders'], queryFn: () => source().orders() })
export const useOrder = (id: string | null) => useQuery({ queryKey: ['order', id], queryFn: () => source().order(id!), enabled: !!id })
export const usePortfolio = () => useQuery({ queryKey: ['portfolio'], queryFn: () => source().portfolio() })
export const usePositions = () => useQuery({ queryKey: ['positions'], queryFn: () => source().positions() })
export const useClosed = () => useQuery({ queryKey: ['closed'], queryFn: () => source().closed() })
export const useStrategy = () => useQuery({ queryKey: ['strategy'], queryFn: () => source().strategy() })
export const useAudit = () => useQuery({ queryKey: ['audit'], queryFn: () => source().audit() })
export const useDataSources = () => useQuery({ queryKey: ['dataSources'], queryFn: () => source().dataSources() })
export const isDemo = () => source().info.kind === 'demo'
export const now = () => source().info.now()
