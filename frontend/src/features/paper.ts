import { create } from 'zustand'
import type { Chain, Token } from '@/domain/types'

/**
 * 模拟盘（Paper Trading）。用真实行情价格模拟买卖，不连钱包、不上链、不花钱。
 * 只存在这台设备的浏览器里。成交价按池子深度估算价格冲击，并扣一笔假设手续费：
 *   成交均价 ≈ 现价 × (1 ± 金额 / (流动性 / 2))，手续费 PAPER_FEE。
 * 这是近似值，真实成交会因路由、滑点、优先费和 MEV 不同；页面上写明了。
 * 数据异常（被判为不可信）或没有价格、没有流动性的代币不能模拟成交。
 */
export const PAPER_FEE = 0.01
export const PAPER_START_OPTIONS = [100, 1_000, 10_000] as const
const KEY = 'kagouai.paper.v1'

export interface PaperPosition { chain: Chain; address: string; symbol: string; image?: string | null; qty: number; costUsd: number; openedAt: number }
export interface PaperTrade {
  id: string; time: number; chain: Chain; address: string; symbol: string; side: 'BUY' | 'SELL'
  qty: number; priceUsd: number; fillUsd: number; usd: number; feeUsd: number
  /** 卖出时实现的盈亏（扣费后），买入为 null */
  realizedUsd: number | null
  /** 下单来源：手动，或 Agent（以后接入） */
  by: 'manual' | 'agent'
}
export interface PaperState { startUsd: number; cashUsd: number; positions: PaperPosition[]; trades: PaperTrade[]; startedAt: number }

export const freshPaper = (startUsd = 1_000, now = Date.now()): PaperState => ({ startUsd, cashUsd: startUsd, positions: [], trades: [], startedAt: now })

/** 估算价格冲击后的成交均价。流动性未知或卖出超过池子深度时返回 null（不能模拟）。 */
export function paperFill(side: 'BUY' | 'SELL', priceUsd: number, usd: number, liquidityUsd: number): number | null {
  if (!(priceUsd > 0) || !(liquidityUsd > 0) || !(usd > 0)) return null
  const impact = usd / (liquidityUsd / 2)
  if (side === 'BUY') return priceUsd * (1 + impact)
  return impact >= 1 ? null : priceUsd * (1 - impact)
}

export class PaperError extends Error {}

const uid = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
const same = (t: { chain: Chain; address: string }) => (p: { chain: Chain; address: string }) => p.chain === t.chain && p.address === t.address

/** 不能模拟成交的原因，可以成交时为 null */
export function paperBlocked(t: Token): string | null {
  if (t.quality?.level === 'bad') return '这个代币的行情数据异常，不能模拟成交'
  if (!(t.priceUsd > 0)) return '读不到价格，不能模拟成交'
  if (!(t.liquidityUsd > 0)) return '读不到流动性，没法估算成交价'
  return null
}

/** 用 usd 美元模拟买入。返回新状态，不改原状态。 */
export function paperBuy(s: PaperState, t: Token, usd: number, by: PaperTrade['by'] = 'manual', now = Date.now()): PaperState {
  const bad = paperBlocked(t)
  if (bad) throw new PaperError(bad)
  if (!(usd > 0)) throw new PaperError('先输入金额')
  if (usd > s.cashUsd + 1e-9) throw new PaperError(`模拟余额不足，还剩 $${s.cashUsd.toFixed(2)}`)
  const fill = paperFill('BUY', t.priceUsd, usd, t.liquidityUsd)
  if (fill == null) throw new PaperError('没法估算成交价')
  const fee = usd * PAPER_FEE
  const qty = (usd - fee) / fill
  const old = s.positions.find(same(t))
  const pos: PaperPosition = old
    ? { ...old, qty: old.qty + qty, costUsd: old.costUsd + usd }
    : { chain: t.chain, address: t.address, symbol: t.symbol, image: t.image ?? null, qty, costUsd: usd, openedAt: now }
  const trade: PaperTrade = { id: uid(), time: now, chain: t.chain, address: t.address, symbol: t.symbol, side: 'BUY', qty, priceUsd: t.priceUsd, fillUsd: fill, usd, feeUsd: fee, realizedUsd: null, by }
  return { ...s, cashUsd: s.cashUsd - usd, positions: [pos, ...s.positions.filter((p) => !same(t)(p))], trades: [trade, ...s.trades].slice(0, 1000) }
}

/** 卖出持仓的 pct（0–100）。 */
export function paperSell(s: PaperState, t: Token, pct: number, by: PaperTrade['by'] = 'manual', now = Date.now()): PaperState {
  const bad = paperBlocked(t)
  if (bad) throw new PaperError(bad)
  const old = s.positions.find(same(t))
  if (!old) throw new PaperError('模拟盘里没有这个代币')
  const share = Math.min(Math.max(pct, 0), 100) / 100
  if (!share) throw new PaperError('先选择卖出比例')
  const qty = old.qty * share
  const fill = paperFill('SELL', t.priceUsd, qty * t.priceUsd, t.liquidityUsd)
  if (fill == null) throw new PaperError('卖出金额超过池子深度，没法估算成交价')
  const proceeds = qty * fill
  const fee = proceeds * PAPER_FEE
  const cost = old.costUsd * share
  const usdOut = proceeds - fee
  const trade: PaperTrade = { id: uid(), time: now, chain: t.chain, address: t.address, symbol: t.symbol, side: 'SELL', qty, priceUsd: t.priceUsd, fillUsd: fill, usd: usdOut, feeUsd: fee, realizedUsd: usdOut - cost, by }
  const rest = share >= 1 ? [] : [{ ...old, qty: old.qty - qty, costUsd: old.costUsd - cost }]
  return { ...s, cashUsd: s.cashUsd + usdOut, positions: [...rest, ...s.positions.filter((p) => !same(t)(p))], trades: [trade, ...s.trades].slice(0, 1000) }
}

/** 按现价估值。价格未知的持仓不计入市值，单独计数。 */
export function paperValue(s: PaperState, priceOf: (p: PaperPosition) => number | null | undefined) {
  let marketUsd = 0
  let unknown = 0
  for (const p of s.positions) {
    const px = priceOf(p)
    if (px == null || !(px > 0)) unknown++
    else marketUsd += p.qty * px
  }
  const realizedUsd = s.trades.reduce((n, t) => n + (t.realizedUsd ?? 0), 0)
  const totalUsd = s.cashUsd + marketUsd
  return { marketUsd, unknown, realizedUsd, totalUsd, pnlUsd: totalUsd - s.startUsd, pnlPct: (totalUsd / s.startUsd - 1) * 100 }
}

function load(): PaperState {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    if (v && typeof v.cashUsd === 'number' && typeof v.startUsd === 'number' && Array.isArray(v.positions) && Array.isArray(v.trades)) return v
  } catch { /* 读不到就重新开始 */ }
  return freshPaper()
}

interface PaperStore extends PaperState {
  buy: (t: Token, usd: number) => PaperTrade
  sell: (t: Token, pct: number) => PaperTrade
  reset: (startUsd: number) => void
}

export const usePaper = create<PaperStore>((set, get) => {
  const pick = (): PaperState => { const g = get(); return { startUsd: g.startUsd, cashUsd: g.cashUsd, positions: g.positions, trades: g.trades, startedAt: g.startedAt } }
  const save = (next: PaperState) => {
    set(next)
    try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* 隐私模式不保存 */ }
  }
  return {
    ...load(),
    buy: (t, usd) => { const next = paperBuy(pick(), t, usd); save(next); return next.trades[0] },
    sell: (t, pct) => { const next = paperSell(pick(), t, pct); save(next); return next.trades[0] },
    reset: (startUsd) => save(freshPaper(startUsd)),
  }
})
