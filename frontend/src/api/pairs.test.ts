import { describe, expect, it } from 'vitest'
import { choosePair, checkPair, consensusPrice } from './pairs'

const DAY = 24 * 60 * 60_000
const NOW = Date.parse('2026-11-07T00:00:00Z')
const pair = (o: { dex: string; price: number; liq: number; vol: number; tx: number; mcap?: number; fdv?: number; ch24?: number; ch1?: number; vol1?: number; tx1?: number; age?: number }) => ({
  dexId: o.dex, pairAddress: `${o.dex}-${o.price}-${o.liq}`, priceUsd: String(o.price), liquidity: { usd: o.liq },
  volume: { h24: o.vol, h1: o.vol1 ?? o.vol / 24 }, txns: { h24: { buys: o.tx / 2, sells: o.tx / 2 }, h1: { buys: (o.tx1 ?? o.tx / 24) / 2, sells: (o.tx1 ?? o.tx / 24) / 2 } },
  marketCap: o.mcap, fdv: o.fdv, priceChange: { h24: o.ch24 ?? 0, h1: o.ch1 ?? 0 }, pairCreatedAt: NOW - (o.age ?? 400) * DAY,
  baseToken: { address: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', symbol: 'Bonk' }, quoteToken: { symbol: 'SOL' },
})

/** 审计时 BONK 的真实情况：一个 Meteora 池子报的流动性最高，24 小时只成交 $30，价格是真实价格的 5000 倍。 */
const BONK = [
  pair({ dex: 'meteora', price: 0.01397, liq: 900_000, vol: 30, tx: 2, mcap: 1_229_700_000_000, fdv: 1_241_000_000_000, ch24: 494_336 }),
  pair({ dex: 'orca', price: 0.000002816, liq: 279_830, vol: 118_407, tx: 7024, mcap: 247_834_838, fdv: 250_280_380, ch24: -3.71 }),
  pair({ dex: 'meteora', price: 0.000002808, liq: 241_292, vol: 138_499, tx: 1393, mcap: 247_163_926, fdv: 249_602_847, ch24: -4.29 }),
  pair({ dex: 'raydium', price: 0.000002814, liq: 27_889, vol: 2_606, tx: 367, mcap: 247_637_198, fdv: 250_080_790, ch24: -3.9 }),
]

describe('choosePair', () => {
  it('不选流动性最大但价格离谱的池子（BONK 回归）', () => {
    const c = choosePair(BONK, NOW)
    expect(c.main.dexId).toBe('orca')
    expect(Number(c.main.priceUsd)).toBeCloseTo(0.000002816)
    expect(c.main.marketCap).toBeLessThan(1e9)
    expect(c.excluded).toBe(1)
    expect(c.pools).toHaveLength(3)
    expect(c.quality.flags).toContain('conflicting_pools')
    expect(c.quality.level).toBe('ok')
  })

  it('总流动性不含被排除的池子', () => {
    const c = choosePair(BONK, NOW)
    expect(c.pools.reduce((s, p) => s + p.liquidity.usd, 0)).toBe(279_830 + 241_292 + 27_889)
  })

  it('共识价格按成交额加权', () => {
    expect(consensusPrice(BONK)).toBeCloseTo(0.000002808, 9)
  })

  it('没有任何可用价格时返回 bad', () => {
    const c = choosePair([{ ...BONK[1], priceUsd: null }], NOW)
    expect(c.main).toBeNull()
    expect(c.quality).toEqual({ level: 'bad', flags: ['no_price'] })
  })

  it('只有一个池子时也检查它自己是否离谱', () => {
    const c = choosePair([BONK[0]], NOW)
    expect(c.quality.level).toBe('bad')
    expect(c.quality.flags).toEqual(expect.arrayContaining(['extreme_change', 'mcap_liquidity_gap']))
  })
})

describe('checkPair', () => {
  const ok = BONK[1]
  it('市值大于完全稀释估值是 bad', () => {
    expect(checkPair({ ...ok, marketCap: 3e8, fdv: 2.5e8 }, [ok], 0, 400 * 1440).flags).toContain('mcap_above_fdv')
  })
  it('老币涨 100 倍以上是 bad，新币不算', () => {
    expect(checkPair({ ...ok, priceChange: { h24: 20_000 } }, [ok], 0, 400 * 1440).level).toBe('bad')
    expect(checkPair({ ...ok, priceChange: { h24: 20_000 } }, [ok], 0, 30).flags).not.toContain('extreme_change')
  })
  it('没有成交却大涨是 suspect', () => {
    const q = checkPair({ ...ok, priceChange: { h1: 80 }, volume: { h24: 1000, h1: 0 }, txns: { h24: { buys: 1, sells: 1 }, h1: { buys: 0, sells: 0 } } }, [ok], 0, 400 * 1440)
    expect(q.flags).toContain('move_without_volume')
    expect(q.level).toBe('suspect')
  })
  it('和其他池子推算的流通量不一致是 bad', () => {
    const other = { ...ok, pairAddress: 'x' }
    expect(checkPair({ ...ok, marketCap: 247_834_838 * 10 }, [ok, other], 0, 400 * 1440).flags).toContain('implied_supply_mismatch')
  })
  it('正常代币是 ok', () => {
    expect(checkPair(ok, BONK.slice(1), 0, 400 * 1440)).toEqual({ level: 'ok', flags: [] })
  })
})
