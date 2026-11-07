import { riskItems, riskLevel, rugLike, isNewPair } from './tokenBits'
import { visibleCols, COLS } from './columns'
import { TOKENS } from '@/api/demo'
import type { SecurityCheck, Token } from '@/domain/types'

const clean: SecurityCheck = { mintAuthority: false, freezeAuthority: false, lp: 'burned', sellSimulation: 'pass', transferFeeBps: 0, top10Pct: 12, devHoldPct: 1, checkedAt: '2026-11-07T00:00:00Z' }

describe('安全三档', () => {
  it('全部通过是 pass', () => expect(riskLevel(clean)).toBe('pass'))
  it('卖不出去、能增发、LP 未锁定是高危，并列出原因', () => {
    expect(riskLevel({ ...clean, sellSimulation: 'fail' })).toBe('high')
    expect(riskItems({ ...clean, mintAuthority: true, lp: 'unlocked' }).map((i) => i.label)).toEqual(['有增发权限', 'LP 未锁定'])
  })
  it('LP 不适用的池子不算风险', () => expect(riskLevel({ ...clean, lp: 'na', lpNote: '集中流动性池' })).toBe('pass'))
  it('持仓集中、小额税、查不到的项目是注意', () => {
    expect(riskLevel({ ...clean, top10Pct: 35 })).toBe('warn')
    expect(riskLevel({ ...clean, transferFeeBps: 300 })).toBe('warn')
    expect(riskLevel({ ...clean, mintAuthority: null })).toBe('warn')
  })
  it('没有检查结果是 unknown，不是通过', () => expect(riskLevel({ ...clean, checkedAt: null })).toBe('unknown'))
})

describe('新币和疑似 Rug', () => {
  const base = TOKENS[0]
  const with24 = (t: Token, change: number, liq: number, ageMinutes = 5000): Token => ({ ...t, ageMinutes, liquidityUsd: liq, stats: { ...t.stats, '24h': { ...t.stats['24h'], change } } })
  it('开盘不足 24 小时是新币', () => { expect(isNewPair(with24(base, 0, 1e5, 60))).toBe(true); expect(isNewPair(with24(base, 0, 1e5, 3000))).toBe(false) })
  it('跌 90% 以上且流动性不足 1 万是疑似 Rug', () => { expect(rugLike(with24(base, -95, 5000))).toBe(true); expect(rugLike(with24(base, -95, 50_000))).toBe(false) })
})

describe('列', () => {
  it('整列没有数据的可选列自动隐藏', () => {
    const rows = TOKENS.map((t) => ({ ...t, holders: null }))
    expect(visibleCols(['price', 'holders'], rows, '24h').map((c) => c.key)).toEqual(['price'])
    expect(COLS.txns.value(TOKENS[0], '1h')).toBe(TOKENS[0].stats['1h'].txns)
  })
})
