/**
 * 交易对选择和异常数据检测。
 *
 * 一个代币常有几十个池子。DexScreener 报的「流动性」可以被人造出来（例如 BONK 曾有一个 Meteora 池子，
 * 报的流动性最高，24 小时成交只有 $30，价格却是真实价格的 5000 倍，页面因此显示市值 $1.2 万亿）。
 * 所以不能直接选流动性最大的池子：
 *
 * 1. 先用各池子的价格算一个「共识价格」：按 24 小时成交额加权的中位数。成交很难造假，没成交的池子权重很低。
 * 2. 和共识价格差太多的池子直接排除，它们的流动性也不算进代币的总流动性。
 * 3. 剩下的池子按 流动性、成交额、成交笔数 一起打分，选最高的作为主交易对。
 * 4. 对选出的主交易对再做一遍异常检查，结果写进 quality：bad 的数据不展示数值、不能交易、不能进 AI 研究。
 *
 * 和后端 tradingagents/meme/pairs.py 的规则保持一致。
 */
import type { DataQuality, QualityFlag } from '@/domain/types'

/* eslint-disable @typescript-eslint/no-explicit-any -- DexScreener 的原始 JSON */
type Json = any

/** 价格和共识价格相差超过这个倍数，就认为这个池子的价格不可信。 */
export const PRICE_TOLERANCE = 2
const num = (v: unknown): number | null => { const n = Number(v); return v == null || v === '' || !Number.isFinite(n) ? null : n }
const liq = (p: Json): number => num(p?.liquidity?.usd) ?? 0
const vol = (p: Json, k = 'h24'): number => num(p?.volume?.[k]) ?? 0
const txns = (p: Json, k = 'h24'): number => (num(p?.txns?.[k]?.buys) ?? 0) + (num(p?.txns?.[k]?.sells) ?? 0)
const price = (p: Json): number | null => { const x = num(p?.priceUsd); return x != null && x > 0 ? x : null }

/** 加权中位数。权重全为 0 时退回普通中位数。 */
function weightedMedian(xs: { v: number; w: number }[]): number | null {
  if (!xs.length) return null
  const sorted = [...xs].sort((a, b) => a.v - b.v)
  const total = sorted.reduce((s, x) => s + x.w, 0)
  if (total <= 0) return sorted[Math.floor((sorted.length - 1) / 2)].v
  let acc = 0
  for (const x of sorted) { acc += x.w; if (acc >= total / 2) return x.v }
  return sorted[sorted.length - 1].v
}

/** 共识价格：成交额加权；成交额都是 0 时用成交笔数，再不行用流动性。 */
export function consensusPrice(pairs: Json[]): number | null {
  const priced = pairs.filter((p) => price(p) != null)
  const by = (w: (p: Json) => number) => priced.map((p) => ({ v: price(p)!, w: w(p) }))
  if (priced.some((p) => vol(p) > 0)) return weightedMedian(by((p) => vol(p)))
  if (priced.some((p) => txns(p) > 0)) return weightedMedian(by((p) => txns(p)))
  return weightedMedian(by(liq))
}

const within = (a: number, b: number) => a / b <= PRICE_TOLERANCE && b / a <= PRICE_TOLERANCE

/** 主交易对的分数：流动性、成交额和笔数都要有，单看一项容易被造假。 */
export function pairScore(p: Json): number {
  return Math.log10(1 + liq(p)) + Math.log10(1 + vol(p)) + 0.5 * Math.log10(1 + txns(p))
}

export interface PairChoice {
  /** 主交易对。没有任何可用池子时为 null。 */
  main: Json | null
  /** 价格和共识一致的池子，按分数从高到低。总流动性只算这些。 */
  pools: Json[]
  /** 价格和共识冲突、被排除的池子数量 */
  excluded: number
  quality: DataQuality
}

const FLAG_TEXT: Record<QualityFlag, string> = {
  conflicting_pools: '有池子的价格和其他池子相差太大，已排除',
  mcap_above_fdv: '市值大于完全稀释估值，数据自相矛盾',
  implied_supply_mismatch: '市值和价格推算出的供应量与其他池子不一致',
  extreme_change: '24 小时涨幅超过 100 倍，且不是新币',
  move_without_volume: '价格大幅变化，但这段时间没有成交',
  mcap_liquidity_gap: '市值相对流动性大得离谱，价格很可能不可成交',
  no_activity: '24 小时没有任何成交',
  no_price: '没有可用价格',
}
export const qualityText = (f: QualityFlag) => FLAG_TEXT[f]

/** 让数据变成「异常、不展示」的检查；其余只降低可信度。 */
const BAD: QualityFlag[] = ['mcap_above_fdv', 'implied_supply_mismatch', 'extreme_change', 'mcap_liquidity_gap', 'no_price']

/**
 * 检查主交易对本身是否可信。ageMinutes 是池子的年龄：刚开盘的币从初始价起算，涨几百倍是正常的。
 */
export function checkPair(main: Json | null, pools: Json[], excluded: number, ageMinutes: number | null): DataQuality {
  const flags: QualityFlag[] = []
  if (!main || price(main) == null) return { level: 'bad', flags: ['no_price'] }
  if (excluded > 0) flags.push('conflicting_pools')
  const mcap = num(main.marketCap), fdv = num(main.fdv), p = price(main)!
  if (mcap != null && fdv != null && fdv > 0 && mcap > fdv * 1.01) flags.push('mcap_above_fdv')
  // 同一个代币各池子的「市值 / 价格」就是流通量，应该一致；某个池子差很多，说明它的价格或市值是错的
  const supply = mcap != null ? mcap / p : null
  const others = pools.filter((x) => x !== main && num(x.marketCap) != null && price(x) != null).map((x) => num(x.marketCap)! / price(x)!)
  if (supply != null && others.length) {
    const typical = weightedMedian(others.map((v) => ({ v, w: 1 })))!
    if (typical > 0 && !within(supply, typical)) flags.push('implied_supply_mismatch')
  }
  const ch24 = num(main.priceChange?.h24)
  if (ch24 != null && ch24 > 10_000 && (ageMinutes == null || ageMinutes > 24 * 60)) flags.push('extreme_change')
  const ch1 = num(main.priceChange?.h1)
  if (ch1 != null && Math.abs(ch1) > 50 && vol(main, 'h1') === 0 && txns(main, 'h1') === 0) flags.push('move_without_volume')
  const shown = mcap ?? fdv
  const totalLiq = pools.reduce((s, x) => s + liq(x), 0)
  // 流动性不到市值的万分之一，又几乎没有成交：这个市值不可能成交出来
  if (shown != null && shown > 1_000_000 && totalLiq < shown / 10_000 && vol(main) < shown / 100_000) flags.push('mcap_liquidity_gap')
  if (vol(main) === 0 && txns(main) === 0) flags.push('no_activity')
  const level = flags.some((f) => BAD.includes(f)) ? 'bad' : flags.some((f) => f !== 'conflicting_pools') ? 'suspect' : 'ok'
  return { level, flags }
}

/** 从一个代币的全部池子里选出主交易对，并排除价格冲突的池子。 */
export function choosePair(pairs: Json[], now = Date.now()): PairChoice {
  const priced = pairs.filter((p) => price(p) != null)
  const ref = consensusPrice(priced)
  const pools = ref == null ? [] : priced.filter((p) => within(price(p)!, ref)).sort((a, b) => pairScore(b) - pairScore(a))
  const main = pools[0] ?? null
  const age = main?.pairCreatedAt ? (now - main.pairCreatedAt) / 60_000 : null
  return { main, pools, excluded: priced.length - pools.length, quality: checkPair(main, pools, priced.length - pools.length, age) }
}
