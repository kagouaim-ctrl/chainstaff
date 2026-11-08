import { describe, it, expect, vi, afterEach } from 'vitest'
import { TOKENS } from '@/api/demo'
import { filterTokens, isBlueChip, type Filter } from '@/pages/Discover'
import { copyText } from '@/lib/copy'

const base: Filter = { tab: 'trending', tf: '1h', safeOnly: false, minMcap: 0, launchpad: 'all', watch: {}, sort: 'mcap', dir: 1 }

describe('线上问题修复', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('平台筛选按前缀比、不分大小写：Raydium 能选到 raydium-clmm', () => {
    const list = [{ ...TOKENS[0], address: 'a', launchpad: 'raydium-clmm' }, { ...TOKENS[1], address: 'b', launchpad: 'pump.fun' }]
    expect(filterTokens(list, { ...base, launchpad: 'Raydium' }).map((t) => t.address)).toEqual(['a'])
    expect(filterTokens(list, { ...base, launchpad: 'pump.fun' }).map((t) => t.address)).toEqual(['b'])
  })

  it('蓝筹：有认证，或市值、流动性、币龄都够', () => {
    const t = { ...TOKENS[0], verified: false, mcapUsd: 2e7, liquidityUsd: 6e5, ageMinutes: 40 * 1440 }
    expect(isBlueChip(t)).toBe(true)
    expect(isBlueChip({ ...t, ageMinutes: 3 * 1440 })).toBe(false)
    expect(isBlueChip({ ...t, liquidityUsd: 1e4 })).toBe(false)
    expect(isBlueChip({ ...t, mcapUsd: 1e5, verified: true })).toBe(true)
  })

  it('剪贴板被拒时不抛错，返回 false 或走旧办法', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: () => Promise.reject(new Error('denied')) } })
    document.execCommand = vi.fn(() => true)
    await expect(copyText('abc')).resolves.toBe(true)
    document.execCommand = vi.fn(() => { throw new Error('no') })
    await expect(copyText('abc')).resolves.toBe(false)
  })
})
