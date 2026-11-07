/**
 * 交易设置，按链分开保存（Solana 的优先费不能套到 EVM 上）。只存在浏览器本地。
 * 最大金额、最低流动性是确定性的程序限制：超过就不让下单，不靠提醒。
 */
import { create } from 'zustand'
import type { Chain } from '@/domain/types'

export type Speed = 'normal' | 'fast' | 'turbo'
export interface TradeSettings {
  /** 'auto'：按流动性自动选，不超过 maxSlippageBps */
  slippage: 'auto' | number
  maxSlippageBps: number
  speed: Speed
  /** 单笔最多花多少原生币，0 表示不限 */
  maxTradeNative: number
  /** 流动性低于这个值不让买入，0 表示不限 */
  minLiquidityUsd: number
}

const DEFAULTS: Record<Chain, TradeSettings> = {
  solana: { slippage: 'auto', maxSlippageBps: 1500, speed: 'fast', maxTradeNative: 0, minLiquidityUsd: 10_000 },
  bsc: { slippage: 'auto', maxSlippageBps: 1500, speed: 'normal', maxTradeNative: 0, minLiquidityUsd: 10_000 },
  ethereum: { slippage: 'auto', maxSlippageBps: 1000, speed: 'normal', maxTradeNative: 0, minLiquidityUsd: 10_000 },
  robinhood: { slippage: 'auto', maxSlippageBps: 1500, speed: 'normal', maxTradeNative: 0, minLiquidityUsd: 10_000 },
}

/** Solana 优先费档位：Jupiter 按网络拥堵估算，上限防止一笔花太多。 */
export const SOL_PRIORITY: Record<Speed, { level: 'medium' | 'high' | 'veryHigh'; maxLamports: number; label: string }> = {
  normal: { level: 'medium', maxLamports: 200_000, label: '普通（优先费上限 0.0002 SOL）' },
  fast: { level: 'high', maxLamports: 1_000_000, label: '快速（优先费上限 0.001 SOL）' },
  turbo: { level: 'veryHigh', maxLamports: 5_000_000, label: '极速（优先费上限 0.005 SOL）' },
}

/** 自动滑点：流动性越浅给得越宽，但不超过用户设的上限。 */
export function effectiveSlippageBps(s: TradeSettings, liquidityUsd: number): number {
  if (s.slippage !== 'auto') return Math.min(s.slippage, s.maxSlippageBps)
  const auto = liquidityUsd < 50_000 ? 1000 : liquidityUsd < 500_000 ? 500 : 200
  return Math.min(auto, s.maxSlippageBps)
}

const KEY = 'kagouai.trade-settings.v1'
function read(): Record<Chain, TradeSettings> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return Object.fromEntries((Object.keys(DEFAULTS) as Chain[]).map((c) => [c, { ...DEFAULTS[c], ...(v?.[c] ?? {}) }])) as Record<Chain, TradeSettings>
  } catch { return { ...DEFAULTS } }
}

interface State { byChain: Record<Chain, TradeSettings>; update: (c: Chain, patch: Partial<TradeSettings>) => void; reset: (c: Chain) => void }
export const useTradeSettings = create<State>((set, get) => ({
  byChain: read(),
  update: (c, patch) => {
    const byChain = { ...get().byChain, [c]: { ...get().byChain[c], ...patch } }
    try { localStorage.setItem(KEY, JSON.stringify(byChain)) } catch { /* 隐私模式不记 */ }
    set({ byChain })
  },
  reset: (c) => get().update(c, DEFAULTS[c]),
}))
