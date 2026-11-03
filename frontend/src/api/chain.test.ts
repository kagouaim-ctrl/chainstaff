import { describe, expect, it } from 'vitest'
import { evmSecurity, isAddress } from './chain'

const goplus = {
  is_mintable: '0', transfer_pausable: '0', is_blacklisted: '0', is_honeypot: '0', cannot_sell_all: '0',
  buy_tax: '0.04', sell_tax: '0.0408', transfer_tax: '0', creator_percent: '0.02', holder_count: '4122',
  lp_holders: [{ address: '0xlock', percent: '0.97', is_locked: 1 }, { address: '0xother', percent: '0.03', is_locked: 0 }],
  holders: [
    { address: '0x000000000000000000000000000000000000dead', percent: '0.9', is_contract: 0 },
    { address: '0xpool', percent: '0.08', is_contract: 1 },
    { address: '0xa', percent: '0.01', is_contract: 0 },
  ],
}

describe('GoPlus 的 EVM 安全检查', () => {
  it('税率、LP 锁仓、持有者都按真人算，死地址和合约不算', () => {
    const s = evmSecurity(goplus)
    expect(s).toMatchObject({ mintAuthority: false, freezeAuthority: false, lp: 'locked', sellSimulation: 'pass', transferFeeBps: 408 })
    expect(s.top10Pct).toBeCloseTo(1)
    expect(s.devHoldPct).toBeCloseTo(2)
  })

  it('貔貅盘是卖出失败，可暂停转账算有冻结权限', () => {
    expect(evmSecurity({ ...goplus, is_honeypot: '1' }).sellSimulation).toBe('fail')
    expect(evmSecurity({ ...goplus, transfer_pausable: '1' }).freezeAuthority).toBe(true)
  })

  it('查不到就是未知，不是安全', () => {
    const s = evmSecurity(null)
    expect(s.mintAuthority).toBeNull()
    expect(s.sellSimulation).toBe('unknown')
    expect(evmSecurity({}).mintAuthority).toBeNull()
  })

  it('认得两种地址', () => {
    expect(isAddress('DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263')).toBe(true)
    expect(isAddress('0x76347aAc4f2BEe7469Cea69D996e894866e7846d')).toBe(true)
    expect(isAddress('0x123')).toBe(false)
  })
})
