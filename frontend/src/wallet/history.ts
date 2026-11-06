import type { Chain } from '@/domain/types'

/** 在这台设备上成交过的交易，用来在 K 线上标出自己的买卖点。只存在浏览器里，链上记录才是准的。 */
export interface MyTrade { chain: Chain; address: string; side: 'BUY' | 'SELL'; time: number; id: string }

const KEY = 'memeagent.trades'
export function readTrades(): MyTrade[] { try { return JSON.parse(localStorage.getItem(KEY) ?? '[]') } catch { return [] } }
export function saveTrade(t: MyTrade) { try { localStorage.setItem(KEY, JSON.stringify([t, ...readTrades()].slice(0, 500))) } catch { /* 隐私模式不记 */ } }
