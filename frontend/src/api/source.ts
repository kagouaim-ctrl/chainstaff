/**
 * 数据源接口。前端只依赖这个接口；后端实现后换成 HTTP + WebSocket 版本即可。
 * demoSource 是演示数据，sourceInfo.kind === 'demo' 时界面必须显示「演示」。
 * 设置 VITE_MEME_LIVE=1 或 VITE_MEME_API_URL 时用 liveSource（真实行情，有后端时还有 Agent 分析，见 live.ts）。
 */
import * as D from './demo'
import { LIVE, liveSource } from './live'
import type {
  AgentDM,
  AgentEvent, AgentInfo, AuditEntry, ChatMessage, ClosedPosition, DataSourceStatus, DecisionStep, Holder, MainAgent,
  Order, PipelineStage, Portfolio, Position, StrategyConfig, Thesis, Token, Trade, Candle, ChartMarker,
} from '@/domain/types'

export interface SourceInfo { kind: 'demo' | 'live'; label: string; now: () => Date }

export interface DataSource {
  info: SourceInfo
  tokens(): Promise<Token[]>
  token(address: string): Promise<Token | null>
  candles(address: string): Promise<{ candles: Candle[]; markers: ChartMarker[] }>
  holders(address: string): Promise<Holder[]>
  chat(address: string): Promise<ChatMessage[]>
  agentDM(): Promise<AgentDM[]>
  theses(address: string): Promise<Thesis[]>
  research(address: string): Promise<DecisionStep[]>
  trades(address: string): Promise<Trade[]>
  mainAgent(): Promise<MainAgent>
  agents(): Promise<AgentInfo[]>
  pipeline(): Promise<PipelineStage[]>
  events(): Promise<AgentEvent[]>
  orders(): Promise<Order[]>
  order(id: string): Promise<Order | null>
  portfolio(): Promise<Portfolio>
  positions(): Promise<Position[]>
  closed(): Promise<ClosedPosition[]>
  strategy(): Promise<StrategyConfig>
  audit(): Promise<AuditEntry[]>
  dataSources(): Promise<DataSourceStatus[]>
}

const wait = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), ms))

export const demoSource: DataSource = {
  info: { kind: 'demo', label: '演示数据', now: () => D.DEMO_NOW },
  tokens: () => wait(D.TOKENS),
  token: (a) => wait(D.TOKENS.find((t) => t.address === a) ?? null),
  candles: (a) => wait(D.candles(a)),
  holders: (a) => wait(D.holders(a)),
  chat: (a) => wait(D.chat(a)),
  agentDM: () => wait(D.AGENT_DM),
  theses: (a) => wait(D.theses(a)),
  research: () => wait([]),
  trades: (a) => wait(D.trades(a)),
  mainAgent: () => wait(D.MAIN_AGENT),
  agents: () => wait(D.AGENTS),
  pipeline: () => wait(D.PIPELINE),
  events: () => wait(D.EVENTS),
  orders: () => wait(D.ORDERS),
  order: (id) => wait(D.ORDERS.find((o) => o.id === id) ?? null),
  portfolio: () => wait(D.PORTFOLIO),
  positions: () => wait(D.POSITIONS),
  closed: () => wait(D.CLOSED),
  strategy: () => wait(D.STRATEGY),
  audit: () => wait(D.AUDIT),
  dataSources: () => wait(D.DATA_SOURCES),
}

let current: DataSource = LIVE ? liveSource : demoSource
export const source = () => current
export function setSource(s: DataSource) { current = s }
