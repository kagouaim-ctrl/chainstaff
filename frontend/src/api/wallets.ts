/**
 * 钱包情报：代币的主要交易员、交易员排行、单个钱包的盈亏和最近成交。
 * 数据来自 Birdeye，经 KAGOUAI-meme-agent 后端转发（密钥只在服务器上）。
 * 「聪明钱」「狙击」「老鼠仓」等标签是 Birdeye 自己打的，我们不自己给钱包贴标签。
 * 后端没配密钥时返回 503，这里抛 NotConfigured，页面显示「数据源未配置」，不显示空表冒充没有数据。
 */
import { API_URL } from './live'

export class NotConfigured extends Error {}

export interface TopTrader {
  wallet: string; tags: string[]; buys: number | null; sells: number | null; trades: number | null
  buyUsd: number | null; sellUsd: number | null; volumeUsd: number | null
  realizedPnl: number | null; unrealizedPnl: number | null; totalPnl: number | null
  holdUsd: number | null; avgBuyPrice: number | null; avgSellPrice: number | null
  firstTradeAt: number | null; lastTradeAt: number | null
}
export interface BoardRow { wallet: string; pnl: number | null; realizedPnl: number | null; unrealizedPnl: number | null; volumeUsd: number | null; trades: number | null; score: number | null }
export interface PnlSummary {
  tokens: number | null; buys: number | null; sells: number | null; trades: number | null; wins: number | null; losses: number | null; winRate: number | null
  investedUsd: number | null; soldUsd: number | null; currentUsd: number | null; realizedUsd: number | null; realizedPct: number | null; unrealizedUsd: number | null; totalUsd: number | null
}
export interface Leg { address: string | null; symbol: string | null; amount: number | null; price: number | null }
export interface WalletTrade { tx: string | null; at: number | null; source: string | null; side: 'buy' | 'sell' | null; base: Leg; quote: Leg; volumeUsd: number | null }
export interface WalletDetail { address: string; pnl: PnlSummary | null; trades: WalletTrade[] }

export const TAG_TEXT: Record<string, string> = { dev: '开发者', bundler: '捆绑', sniper: '狙击', insider: '老鼠仓', smart_trader: '聪明钱' }
export const TAGS = Object.keys(TAG_TEXT)
export const WALLETS_ONLINE = !!API_URL

async function get<T>(path: string): Promise<T> {
  if (!API_URL) throw new NotConfigured('后端未接入')
  const r = await fetch(`${API_URL}${path}`)
  if (r.status === 503) throw new NotConfigured((await r.json().catch(() => null))?.detail ?? '钱包数据源未配置')
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `后端返回 ${r.status}`)
  return r.json()
}

export const topTraders = (mint: string, timeFrame = '24h', tag?: string) =>
  get<TopTrader[]>(`/api/tokens/${encodeURIComponent(mint)}/traders?time_frame=${timeFrame}${tag ? `&tag=${tag}` : ''}`)
export const leaderboard = (period = '1W', sortBy = 'PnL') => get<BoardRow[]>(`/api/wallets/leaderboard?period=${period}&sort_by=${sortBy}`)
export const walletDetail = (address: string, duration = '30d') => get<WalletDetail>(`/api/wallets/${encodeURIComponent(address)}?duration=${duration}`)
