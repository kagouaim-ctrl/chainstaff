/**
 * 真实数据源：KAGOUAI-meme-agent 后端。设置 VITE_MEME_API_URL 时启用。
 * 行情、安全检查和 Agent 分析是真的；交易、持仓、私聊还没有后端，这里返回空，绝不返回样例。
 * 后端内容引用了陌生人写的代币名称和简介，只能当文本显示。
 */
import { ROLES } from '@/domain/agents'
import { DEFAULT_POLICY } from '@/domain/risk'
import type {
  AgentEvent, AgentInfo, AgentRole, ChatAuthor, ChatMessage, DataSourceStatus, DecisionStep, EventStatus, Holder,
  Portfolio, StrategyConfig, Thesis, Token, Trade, Candle,
} from '@/domain/types'
import type { DataSource } from './source'

export const API_URL = (import.meta.env.VITE_MEME_API_URL ?? '').replace(/\/+$/, '')

/** 后端事件。字段见 tradingagents/meme/graph.py。 */
export interface RunEvent {
  seq: number
  ts: number
  type: string
  agent: string
  label?: string
  round?: number
  text?: string
  error?: string
  report?: { score: number; confidence: number; summary: string; evidence: string[]; red_flags: string[] }
  plan?: Record<string, unknown> | null
  review?: { verdict: string; worst_case: string; reasons: string[] } | null
  decision?: { action: 'buy' | 'skip'; position_pct: number; blocked_by: string[]; adjustments: string[]; executed: boolean }
  prescreen?: { passed: boolean; failed: string[]; rules: { key: string; label: string; status: string; detail: string }[] }
}
export interface Run { id: string; mint: string; status: 'running' | 'done' | 'failed'; created_at: number; events: RunEvent[] }

const ROLE_OF: Record<string, AgentRole> = {
  'Market Analyst': 'quant', 'Onchain Analyst': 'onchain', 'Security Analyst': 'security',
  'Social Analyst': 'sentiment', 'Narrative Analyst': 'sentiment',
  'Bull Researcher': 'strategy', 'Bear Researcher': 'strategy', 'Research Manager': 'strategy', Trader: 'strategy',
  'Risk Agent': 'risk',
}
const roleOf = (e: RunEvent): AgentRole => ROLE_OF[e.agent] ?? (e.type === 'decision' || e.type === 'prescreen' ? 'risk' : 'scout')
const at = (e: RunEvent) => new Date(e.ts * 1000).toISOString()
const STANCE: Record<string, string> = { buy: '买入', watch: '观望', avoid: '回避' }
const VERDICT: Record<string, string> = { approve: '批准', reduce: '要求降仓', veto: '否决' }
const BLOCKED: Record<string, string> = {
  trade_plan_unreadable: '交易方案无法解析', trader_skipped: '交易员决定不买', risk_review_unreadable: '风控回答无法解析',
  risk_agent_veto: '风控否决', no_stop_loss: '没有止损', stop_loss_too_wide: '止损太宽', no_max_hold: '没有最长持有时间',
  max_hold_too_long: '持有时间太长', no_position_size: '没有仓位',
}

function blockedText(codes: string[], run: RunEvent[]): string {
  const rules = Object.fromEntries((run.find((e) => e.type === 'prescreen')?.prescreen?.rules ?? []).map((r) => [r.key, r.label]))
  return codes.map((c) => {
    if (c.startsWith('prescreen:')) return `预筛没通过：${rules[c.slice(10)] ?? c.slice(10)}`
    if (c.startsWith('unknown:')) return `缺少关键数据：${rules[c.slice(8)] ?? c.slice(8)}`
    return BLOCKED[c] ?? c
  }).join('；')
}

/** 一条事件用人话怎么说。没有可说的内容时返回 null。 */
export function describe(e: RunEvent, run: RunEvent[]): { title: string; text: string; status: EventStatus } | null {
  switch (e.type) {
    case 'prescreen': {
      const p = e.prescreen!
      const failed = p.rules.filter((r) => r.status === 'fail').map((r) => `${r.label}（${r.detail}）`)
      return p.passed
        ? { title: '硬规则预筛', text: `通过了 ${p.rules.length} 项硬规则检查，交给分析师。`, status: 'ok' }
        : { title: '硬规则预筛', text: `没通过，不调用模型：${failed.join('；')}`, status: 'warn' }
    }
    case 'analyst_report': {
      const r = e.report!
      const flags = r.red_flags.length ? ` 风险：${r.red_flags.join('；')}` : ''
      return { title: e.label ?? e.agent, text: `评分 ${r.score > 0 ? '+' : ''}${r.score}。${r.summary}${flags}`, status: r.red_flags.length ? 'warn' : 'info' }
    }
    case 'debate_message': return { title: e.label ?? e.agent, text: e.text ?? '', status: 'info' }
    case 'research_plan': {
      const p = e.plan as { stance: string; rationale: string }
      return { title: '研究主管', text: `结论：${STANCE[p.stance] ?? p.stance}。${p.rationale}`, status: 'info' }
    }
    case 'trade_plan': {
      const p = e.plan as { action: string; position_pct?: number; stop_loss_pct?: number; max_hold_minutes?: number; reasoning: string } | null
      if (!p) return { title: '交易员', text: '方案无法解析，按不买处理。', status: 'warn' }
      return p.action === 'buy'
        ? { title: '交易员', text: `想买，仓位 ${p.position_pct}%，止损 ${p.stop_loss_pct}%，最长持有 ${p.max_hold_minutes} 分钟。${p.reasoning}`, status: 'info' }
        : { title: '交易员', text: `不买。${p.reasoning}`, status: 'info' }
    }
    case 'risk_review':
      return e.review
        ? { title: '风控', text: `${VERDICT[e.review.verdict] ?? e.review.verdict}。最坏情况：${e.review.worst_case}`, status: e.review.verdict === 'veto' ? 'warn' : 'info' }
        : { title: '风控', text: '回答无法解析，按否决处理。', status: 'warn' }
    case 'decision': {
      const d = e.decision!
      return d.action === 'buy'
        ? { title: '最终决定', text: `建议买入，仓位 ${d.position_pct}%。没有下单。`, status: 'ok' }
        : { title: '最终决定', text: `不买。${blockedText(d.blocked_by, run)}`, status: 'warn' }
    }
    case 'run_failed': return { title: '分析失败', text: `这次分析没跑完（${e.error}）。`, status: 'error' }
    default: return null
  }
}

const hueOf = (s: string) => [...s].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7)
function authorOf(e: RunEvent, name: string): ChatAuthor {
  const id = `agent:${e.agent || 'engine'}`
  return { id, kind: 'agent', name, avatar: { hue: hueOf(id), text: name.slice(0, 1) }, holding: null }
}

/** 代币群里显示 Agent 之间的讨论。 */
export function runToChat(run: Run): ChatMessage[] {
  const out: ChatMessage[] = []
  for (const e of run.events) {
    const said = describe(e, run.events)
    if (!said) continue
    if (e.type === 'prescreen' || e.type === 'decision' || e.type === 'run_failed') {
      out.push({ id: `${run.id}-${e.seq}`, at: at(e), type: 'system', text: `${said.title}：${said.text}` })
    } else {
      out.push({
        id: `${run.id}-${e.seq}`, at: at(e), type: 'text', author: authorOf(e, said.title), text: said.text,
        basis: e.report?.evidence.length ? e.report.evidence : undefined,
      })
    }
  }
  return out
}

/** 多空研究员的最后发言就是这个代币的观点。 */
export function runToTheses(run: Run): Thesis[] {
  const last = (who: string) => [...run.events].reverse().find((e) => e.type === 'debate_message' && e.agent === who)
  return (['Bull Researcher', 'Bear Researcher'] as const).flatMap((who) => {
    const e = last(who)
    if (!e) return []
    return [{
      id: `${run.id}-${e.seq}`, at: at(e), author: authorOf(e, e.label ?? who),
      stance: who === 'Bull Researcher' ? 'bull' as const : 'bear' as const, text: e.text ?? '', likes: 0, replies: 0,
    }]
  })
}

export function runToSteps(run: Run): DecisionStep[] {
  return run.events.flatMap((e) => {
    const said = describe(e, run.events)
    return said ? [{ at: at(e), agent: roleOf(e), title: said.title, detail: said.text, ref: e.report?.evidence.join('；') || undefined }] : []
  })
}

export function runToEvents(run: Run, symbolOf: (mint: string) => string): AgentEvent[] {
  return run.events.flatMap((e) => {
    const said = describe(e, run.events)
    if (!said) return []
    return [{
      id: `${run.id}-${e.seq}`, at: at(e), agent: roleOf(e), type: said.title, status: said.status, message: said.text,
      token: { address: run.mint, symbol: symbolOf(run.mint) },
    }]
  })
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(API_URL + path)
  if (!r.ok) throw new Error(`请求失败 ${r.status}`)
  return r.json() as Promise<T>
}

async function latestRun(mint: string): Promise<Run | null> {
  const rows = await get<Run[]>(`/api/analyses?mint=${encodeURIComponent(mint)}&limit=1`)
  return rows[0] ? get<Run>(`/api/analyses/${encodeURIComponent(rows[0].id)}`) : null
}

/** 开始分析。访问码由访客输入，页面里不带任何密钥。 */
export async function startRun(mint: string, code: string): Promise<{ id: string }> {
  const r = await fetch(`${API_URL}/api/analyses`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${code}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ mint }),
  })
  const body = await r.json().catch(() => ({}))
  if (r.status === 401) throw new Error('访问码不对')
  if (!r.ok) throw new Error(body.detail ?? `请求失败 ${r.status}`)
  return body
}

/** 看一次分析的事件。连接断了会从断点续上；结束或放弃时调用 onEnd。 */
export function watchRun(id: string, onEvent: () => void, onEnd: (status: string) => void): () => void {
  let seq = 0, tries = 0, stopped = false
  let socket: WebSocket | null = null
  const connect = () => {
    socket = new WebSocket(`${API_URL.replace(/^http/, 'ws')}/ws/analyses/${encodeURIComponent(id)}?after=${seq}`)
    socket.onmessage = (m) => {
      const e = JSON.parse(m.data)
      tries = 0
      if (e.type === 'end') { stopped = true; onEnd(e.status); socket?.close(); return }
      seq = e.seq
      onEvent()
    }
    socket.onclose = () => {
      if (stopped) return
      if (tries++ < 5) setTimeout(connect, 2000)
      else { stopped = true; onEnd('lost') }
    }
  }
  connect()
  return () => { stopped = true; socket?.close() }
}

const EMPTY_PORTFOLIO: Portfolio = {
  mode: 'paper', totalUsd: 0, cashUsd: 0, realizedUsd: 0, unrealizedUsd: 0, costsUsd: 0, todayLossUsd: 0, todayLossLimitUsd: 0, equity: [],
}
const STRATEGY: StrategyConfig = { chain: 'solana', scope: '尚未配置', template: '尚未配置', riskBudgetUsd: 0, mode: 'paper', policy: DEFAULT_POLICY, version: 0 }
const DATA_SOURCES: DataSourceStatus[] = [
  { name: 'DexScreener', kind: '行情、交易对', state: 'connected', note: '无需 key' },
  { name: 'GeckoTerminal', kind: 'K 线、成交', state: 'connected', note: '免费档有频率限制，超限时这部分显示缺失' },
  { name: 'Solana RPC', kind: 'Mint / Freeze 权限', state: 'connected', note: '公共节点不提供持有者查询，RugCheck 不可用时持有者数据缺失' },
  { name: 'RugCheck', kind: '风险项、持有者、LP', state: 'connected', note: '无需 key' },
  { name: 'Jupiter', kind: '卖出报价', state: 'connected', note: '只查报价，不是真实卖出模拟' },
  { name: 'X / Telegram', kind: '社交内容', state: 'not_connected', note: '没有接入，情绪分析师只能看链接和付费推广' },
  { name: '交易执行', kind: '下单、持仓', state: 'not_connected', note: '只出决定，不下单' },
]

async function allRuns(): Promise<Run[]> { return get<Run[]>('/api/analyses/events') }

async function agents(): Promise<AgentInfo[]> {
  const runs = await allRuns()
  const running = runs.some((r) => r.status === 'running')
  const steps = runs.flatMap((r) => r.events.map((e) => ({ role: roleOf(e), said: describe(e, r.events), at: at(e) })))
  const started = new Date().toISOString()
  return ROLES.map((d) => {
    const mine = steps.filter((s) => s.role === d.role && s.said)
    const noBackend = d.role === 'execution' || d.role === 'position' || d.role === 'supervisor'
    return {
      role: d.role, name: d.name, duty: d.duty, impl: d.impl, noModel: d.noModel,
      status: noBackend ? 'paused' : d.role === 'sentiment' ? 'degraded' : running ? 'running' : 'idle',
      currentTask: null,
      lastHeartbeatAt: mine[mine.length - 1]?.at ?? started,
      startedAt: started,
      errorCount24h: 0,
      lastOutput: noBackend ? '还没有接入' : mine[mine.length - 1]?.said?.text ?? '还没有分析过代币',
      model: null,
      modelCostTodayUsd: 0,
      upstream: d.upstream, downstream: d.downstream,
    }
  })
}

export const liveSource: DataSource = {
  info: { kind: 'live', label: '实时数据', now: () => new Date() },
  tokens: () => get<Token[]>('/api/tokens'),
  token: (a) => get<Token | null>(`/api/tokens/${encodeURIComponent(a)}`),
  candles: async (a) => ({ candles: await get<Candle[]>(`/api/tokens/${encodeURIComponent(a)}/candles`), markers: [] }),
  holders: (a) => get<Holder[]>(`/api/tokens/${encodeURIComponent(a)}/holders`),
  trades: (a) => get<Trade[]>(`/api/tokens/${encodeURIComponent(a)}/trades`),
  chat: async (a) => { const run = await latestRun(a); return run ? runToChat(run) : [] },
  theses: async (a) => { const run = await latestRun(a); return run ? runToTheses(run) : [] },
  research: async (a) => { const run = await latestRun(a); return run ? runToSteps(run) : [] },
  agentDM: async () => [],
  mainAgent: async () => ({
    id: 'ma_live', name: 'Meme Agent', owner: '', mode: 'paper', strategyName: '尚未配置', strategyVersion: 0,
    status: 'idle', createdAt: new Date().toISOString(), chatIntervalHours: 4,
  }),
  agents,
  pipeline: async () => [],
  events: async () => {
    const runs = await allRuns()
    const tokens = await get<Token[]>('/api/tokens').catch(() => [] as Token[])
    const symbolOf = (mint: string) => tokens.find((t) => t.address === mint)?.symbol ?? `${mint.slice(0, 4)}…`
    return runs.flatMap((r) => runToEvents(r, symbolOf)).sort((x, y) => y.at.localeCompare(x.at))
  },
  orders: async () => [],
  order: async () => null,
  portfolio: async () => EMPTY_PORTFOLIO,
  positions: async () => [],
  closed: async () => [],
  strategy: async () => STRATEGY,
  audit: async () => [],
  dataSources: async () => DATA_SOURCES,
}
