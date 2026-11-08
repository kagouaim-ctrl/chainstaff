import { describe, expect, it } from 'vitest'
import { fromUnits, toUnits } from './chains'
import { base58 } from './actions'

describe('金额换算', () => {
  it('不经过浮点，小数位截断而不是四舍五入', () => {
    expect(toUnits('0.1', 9)).toBe(100_000_000n)
    expect(toUnits('1.123456789123', 9)).toBe(1_123_456_789n)
    expect(toUnits('2', 18)).toBe(2_000_000_000_000_000_000n)
    expect(fromUnits(1_500_000_000n, 9)).toBe('1.5')
    expect(fromUnits(1n, 18, 6)).toBe('0')
  })
  it('拒绝不是数字的金额', () => {
    expect(() => toUnits('1e5', 9)).toThrow()
    expect(() => toUnits('-1', 9)).toThrow()
  })
})

describe('base58', () => {
  it('和 Solana 的编码一致，保留前导零', () => {
    expect(base58(new Uint8Array(32))).toBe('11111111111111111111111111111111')
    expect(base58(Uint8Array.from([0, 0, 1]))).toBe('112')
    expect(base58(new TextEncoder().encode('hello world'))).toBe('StV1DL6CwTryKyV')
  })
})

import { ema, sma } from '@/features/charts'
describe('指标', () => {
  it('周期不够的地方是空的', () => {
    expect(sma([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.5, 3.5])
    const e = ema([1, 2, 3, 4], 2)
    expect(e[0]).toBeNull()
    expect(e[1]).toBe(1.5)
    expect(e[3]).toBeCloseTo(3.5, 1)
  })
})

import { explainFailure } from '@/features/LiveTradePanel'
import { effectiveSlippageBps } from './settings'
describe('交易失败的原因', () => {
  it('翻译常见报错', () => {
    expect(explainFailure('User rejected the request')).toContain('取消')
    expect(explainFailure('Program error: 0x1771')).toContain('滑点')
    expect(explainFailure('insufficient funds for gas')).toContain('余额不够')
    expect(explainFailure('奇怪的错误')).toBe('没有完成：奇怪的错误')
  })
  it('自动滑点按流动性，不超过上限', () => {
    const s = { slippage: 'auto' as const, maxSlippageBps: 800, speed: 'fast' as const, maxTradeNative: 0, minLiquidityUsd: 0 }
    expect(effectiveSlippageBps(s, 10_000)).toBe(800)
    expect(effectiveSlippageBps(s, 1_000_000)).toBe(200)
    expect(effectiveSlippageBps({ ...s, slippage: 3000 }, 1)).toBe(800)
  })
})
