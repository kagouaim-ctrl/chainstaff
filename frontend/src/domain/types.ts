/** 前端领域类型。和后端（未实现）对齐用，字段名按计划第 7 节数据模型。 */

export type Mode = 'backtest' | 'paper' | 'live'
export type Timeframe = '5m' | '1h' | '4h' | '24h'

export type AgentRole =
  | 'scout' | 'onchain' | 'security' | 'sentiment' | 'quant'
  | 'strategy' | 'risk' | 'execution' | 'position' | 'supervisor'

export type AgentStatus = 'running' | 'idle' | 'degraded' | 'paused' | 'error'
export type Impl = 'code' | 'code+model' | 'model+code'

export interface AgentInfo {
  role: AgentRole
  name: string
  duty: string
  impl: Impl
  noModel?: boolean // 风控/执行/持仓/总控：禁止模型
  status: AgentStatus
  currentTask: string | null
  lastHeartbeatAt: string
  startedAt: string
  errorCount24h: number
  lastOutput: string
  model: string | null
  modelCostTodayUsd: number
  upstream: AgentRole[]
  downstream: AgentRole[]
}

/** 用户的主 Agent（= 交易 Agent，同时担任总策划）。MVP 每个用户只有一个。 */
export interface MainAgent {
  id: string
  name: string
  owner: string
  mode: Mode
  strategyName: string
  strategyVersion: number
  status: AgentStatus
  createdAt: string
  chatIntervalHours: number
}

export type Verdict = 'unresearched' | 'researching' | 'security_veto' | 'no_trade' | 'bought'

export interface SecurityCheck {
  mintAuthority: boolean
  freezeAuthority: boolean
  lp: 'burned' | 'locked' | 'unlocked' | 'unknown'
  sellSimulation: 'pass' | 'fail' | 'unknown'
  transferFeeBps: number
  top10Pct: number
  devHoldPct: number
  checkedAt: string | null
}

export interface WindowStat { change: number; volume: number; txns: number; buyers: number; sellers: number }

export interface Token {
  chain: 'solana'
  address: string
  symbol: string
  name: string
  hue: number
  priceUsd: number
  mcapUsd: number
  liquidityUsd: number
  holders: number
  ageMinutes: number
  stats: Record<Timeframe, WindowStat>
  security: SecurityCheck
  verdict: Verdict
  tags: string[]
  graduated: boolean
  watch?: boolean
  dataSource: string
  updatedAt: string
}

export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number }

export type MarkerKind = 'discovered' | 'no_trade' | 'risk_rejected' | 'buy' | 'sell'
export interface ChartMarker { time: number; kind: MarkerKind; label: string; orderId?: string }

export type OrderStatus =
  | 'CREATED' | 'VALIDATING' | 'REJECTED' | 'READY' | 'SUBMITTED'
  | 'PENDING' | 'CONFIRMED' | 'FAILED' | 'EXPIRED' | 'UNKNOWN'

export interface OrderEvent { at: string; status: OrderStatus; reason: string }

export interface RiskCheckItem { rule: string; limit: string; actual: string; pass: boolean }
export interface RiskApproval {
  id: string
  result: 'APPROVED' | 'REJECTED' | 'PAUSED'
  decidedAt: string
  validUntil: string
  paramsHash: string
  checks: RiskCheckItem[]
}

export interface DecisionStep { at: string; agent: AgentRole; title: string; detail: string; ref?: string }

export interface Fees { networkUsd: number; priorityUsd: number; platformUsd: number; slippageUsd: number }

export interface Order {
  id: string
  idempotencyKey: string
  mode: Mode
  side: 'BUY' | 'SELL'
  token: Pick<Token, 'address' | 'symbol' | 'name' | 'hue'>
  status: OrderStatus
  createdAt: string
  requestedUsd: number
  requestedAmount: number | null
  filledAmount: number | null
  referencePrice: number
  fillPrice: number | null
  fees: Fees | null
  txSignature: string | null
  finality: 'confirmed' | 'finalized' | null
  events: OrderEvent[]
  risk: RiskApproval
  decisionChain: DecisionStep[]
  failureReason: string | null
}

export interface Position {
  token: Pick<Token, 'address' | 'symbol' | 'name' | 'hue'>
  mode: Mode
  amount: number
  avgCostUsd: number
  priceUsd: number
  costBasisUsd: number
  valueUsd: number
  unrealizedUsd: number
  takeProfit: number | null
  stopLoss: number | null
  reconciliation: 'ok' | 'pending' | 'mismatch'
  openedAt: string
}

export interface ClosedPosition {
  token: Pick<Token, 'address' | 'symbol' | 'name' | 'hue'>
  mode: Mode
  realizedUsd: number
  feesUsd: number
  openedAt: string
  closedAt: string
  finalized: boolean
}

export interface Portfolio {
  mode: Mode
  totalUsd: number
  cashUsd: number
  realizedUsd: number
  unrealizedUsd: number
  costsUsd: number
  todayLossUsd: number
  todayLossLimitUsd: number
  equity: { time: number; value: number }[]
}

export type EventStatus = 'info' | 'ok' | 'warn' | 'error'
export interface AgentEvent {
  id: string
  at: string
  agent: AgentRole
  type: string
  status: EventStatus
  message: string
  token?: Pick<Token, 'address' | 'symbol'>
  orderId?: string
}

export interface PipelineStage { key: string; label: string; agent: AgentRole; inFlight: number; lastToken: string | null; lastAt: string | null }

export interface Holder {
  rank: number
  label: string
  kind: 'agent' | 'wallet'
  agentName?: string
  ownerName?: string
  amountUsd: number
  pct: number
  avgCost: number
  pnlPct: number
  heldMinutes: number
}

/** 代币群聊。每个代币一个群，不买也能看；只有持有过的人和持有中的主 Agent 能发言。 */
export interface ChatMessage {
  id: string
  at: string
  author: {
    kind: 'agent' | 'human'
    name: string
    owner?: string
    holding: { mode: Mode; amountUsd: number; pnlPct: number } | null
    sold?: boolean
  }
  kind: 'digest' | 'reply' | 'human'
  replyTo?: string
  text: string
  evidence?: string[]
}

export interface RiskPolicy {
  maxTradePct: number
  maxTokenExposurePct: number
  maxPositions: number
  dailyLossPct: number
  maxSlippageBps: number
  minLiquidityUsd: number
  minTokenAgeMin: number
  cooldownMin: number
  maxTradesPerHour: number
  vetoOnAuthority: boolean
}

export interface StrategyConfig {
  chain: 'solana'
  scope: string
  template: string
  riskBudgetUsd: number
  mode: Mode
  policy: RiskPolicy
  version: number
}

export interface AuditEntry { at: string; actor: string; field: string; from: string; to: string; confirmedTwice: boolean }

export interface DataSourceStatus { name: string; kind: string; state: 'connected' | 'mock' | 'not_connected'; note: string }
