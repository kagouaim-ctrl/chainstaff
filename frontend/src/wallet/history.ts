import type { Chain } from '@/domain/types'

/**
 * 在这台设备上成交过的交易，用来在 K 线上标出自己的买卖点、在交易页列出成交记录。
 * 只存在浏览器里，链上记录才是准的。
 * 金额来自下单前最后一次报价（实际成交在滑点范围内可能略有不同），旧记录没有这些字段。
 */
export interface MyTrade {
  chain: Chain; address: string; side: 'BUY' | 'SELL'; time: number; id: string
  symbol?: string
  /** 付出的数量和单位，例如 0.1 SOL；卖出时是代币数量 */
  inAmount?: string; inSymbol?: string
  /** 报价得到的数量和单位 */
  outAmount?: string; outSymbol?: string
  /** 报价时这笔交易的美元价值，读不到为 null */
  usd?: number | null
}

const KEY = 'memeagent.trades'
export function readTrades(): MyTrade[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((t) => t && typeof t.id === 'string' && typeof t.address === 'string') : []
  } catch { return [] }
}
export function saveTrade(t: MyTrade) { try { localStorage.setItem(KEY, JSON.stringify([t, ...readTrades()].slice(0, 500))) } catch { /* 隐私模式不记 */ } }
