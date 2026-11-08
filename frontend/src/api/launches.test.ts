import { geckoPair, launches, looksGraduated, pumpProgress, PUMP_INITIAL_REAL_TOKENS } from './launches'
import { tokenView } from './chain'
import { applyFilters } from '@/pages/NewTokens'
import { titleOf } from '@/app/Layout'

function curve(realTokens: bigint, complete = false): Uint8Array {
  const b = new Uint8Array(49)
  const v = new DataView(b.buffer)
  v.setBigUint64(24, realTokens, true)
  b[48] = complete ? 1 : 0
  return b
}

describe('pump.fun 内盘进度', () => {
  it('还没人买是 0%', () => expect(pumpProgress(curve(PUMP_INITIAL_REAL_TOKENS))?.pct).toBe(0))
  it('卖掉一半是 50%', () => expect(pumpProgress(curve(PUMP_INITIAL_REAL_TOKENS / 2n))?.pct).toBe(50))
  it('链上读到的真实账户：剩 553,781,946,691,870 是 30.2%', () => expect(pumpProgress(curve(553_781_946_691_870n))?.pct).toBe(30.2))
  it('已完成就是 100% 且标记毕业', () => expect(pumpProgress(curve(0n, true))).toEqual({ pct: 100, complete: true }))
  it('数据太短或数字不合理时不给进度', () => {
    expect(pumpProgress(new Uint8Array(20))).toBeNull()
    expect(pumpProgress(curve(PUMP_INITIAL_REAL_TOKENS * 2n))).toBeNull()
  })
})

const pool = {
  attributes: {
    address: 'Pool1111111111111111111111111111111111111111', name: 'MEW / SOL', pool_created_at: new Date(Date.now() - 10 * 60_000).toISOString(),
    base_token_price_usd: '0.00001', fdv_usd: '10000', market_cap_usd: null, reserve_in_usd: '5000',
    price_change_percentage: { m5: '1', h1: '20', h6: '20', h24: '20' },
    transactions: { m5: { buys: 3, sells: 1 }, h1: { buys: 30, sells: 10 }, h6: { buys: 30, sells: 10 }, h24: { buys: 30, sells: 10 } },
    volume_usd: { m5: '100', h1: '2000', h6: '2000', h24: '2000' },
  },
  relationships: { base_token: { data: { id: 'solana_Mint1111111111111111111111111111111pump' } }, quote_token: { data: { id: 'solana_So11111111111111111111111111111111111111112' } }, dex: { data: { id: 'pump-fun' } } },
}
const included = new Map([['solana_Mint1111111111111111111111111111111pump', { attributes: { symbol: 'MEW', name: 'Mew', image_url: 'http://not-https.example/x.png' } }]])

describe('GeckoTerminal 池子换算', () => {
  it('转成交易对后能走同一套校验，市值缺失时用 FDV，不当成 0', () => {
    const pair = geckoPair('solana', pool, included)
    expect(pair.marketCap).toBeNull()
    const t = tokenView('solana', [pair], null)!
    expect(t.symbol).toBe('MEW')
    expect(t.mcapUsd).toBe(10000)
    expect(t.stats['1h'].txns).toBe(40)
    expect(t.graduated).toBe(false)
    expect(t.quality?.level).toBe('ok')
    expect(t.image).toBeNull()
    expect(t.ageMinutes).toBeGreaterThan(9)
  })
  it('没有基础代币的池子丢掉', () => expect(geckoPair('solana', { attributes: {} }, included)).toBeNull())
})

describe('刚毕业的判断', () => {
  it('pump.fun 代币在 PumpSwap、bonk 代币在 Raydium 上是毕业', () => {
    expect(looksGraduated('solana', 'pumpswap', 'Abcpump')).toBe(true)
    expect(looksGraduated('solana', 'raydium', 'Abcbonk')).toBe(true)
    expect(looksGraduated('solana', 'pumpswap', 'Abcxyz')).toBe(false)
    expect(looksGraduated('solana', 'meteora', 'Abcpump')).toBe(false)
  })
  it('four.meme 代币在 PancakeSwap 上是毕业', () => {
    expect(looksGraduated('bsc', 'pancakeswap_v2', '0xabc4444')).toBe(true)
    expect(looksGraduated('bsc', 'pancakeswap-v3-bsc', '0xabc1234')).toBe(false)
  })
})

describe('新币筛选', () => {
  it('按流动性、1 小时笔数和数据异常筛', () => {
    const t = tokenView('solana', [geckoPair('solana', pool, included)], null)!
    expect(applyFilters([t], { minLiq: 1000, hideBad: true, minTxns1h: 10 })).toHaveLength(1)
    expect(applyFilters([t], { minLiq: 10_000, hideBad: true, minTxns1h: 0 })).toHaveLength(0)
    expect(applyFilters([t], { minLiq: 0, hideBad: true, minTxns1h: 50 })).toHaveLength(0)
    const bad = { ...t, quality: { level: 'bad' as const, flags: ['no_price' as const] } }
    expect(applyFilters([bad], { minLiq: 0, hideBad: true, minTxns1h: 0 })).toHaveLength(0)
    expect(applyFilters([bad], { minLiq: 0, hideBad: false, minTxns1h: 0 })).toHaveLength(1)
  })
})

describe('读不到数据时', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('GeckoTerminal 全部失败时报错，而不是显示「没有新币」', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 429 })))
    await expect(launches('bsc')).rejects.toThrow(/新币数据暂时读不到/)
  })
})

describe('页面标题', () => {
  it('子页面不显示「页面不存在」', () => {
    expect(titleOf('/agents/about')).toBe('AI Agent')
    expect(titleOf('/new')).toBe('新币')
    expect(titleOf('/wallet/abc')).toBe('钱包')
    expect(titleOf('/nope')).toBe('页面不存在')
  })
})
