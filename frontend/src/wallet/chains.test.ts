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
