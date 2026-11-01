import { isValidPath, canTransition, isFilled, TERMINAL, TRANSITIONS } from './orderMachine'
import { DEFAULT_POLICY, diffPolicy, needsSecondConfirm } from './risk'
import { ROLES } from './agents'

describe('订单状态机', () => {
  it('合法路径', () => {
    expect(isValidPath(['CREATED', 'VALIDATING', 'READY', 'SUBMITTED', 'PENDING', 'CONFIRMED'])).toBe(true)
    expect(isValidPath(['CREATED', 'VALIDATING', 'REJECTED'])).toBe(true)
    expect(isValidPath(['CREATED', 'VALIDATING', 'READY', 'SUBMITTED', 'UNKNOWN', 'FAILED'])).toBe(true)
  })
  it('不允许跳过风控校验直接提交', () => {
    expect(canTransition('CREATED', 'SUBMITTED')).toBe(false)
    expect(isValidPath(['CREATED', 'READY'])).toBe(false)
  })
  it('终态不能再变', () => {
    for (const s of TERMINAL) expect(TRANSITIONS[s]).toHaveLength(0)
  })
  it('提交不等于成交', () => {
    expect(isFilled('SUBMITTED')).toBe(false)
    expect(isFilled('PENDING')).toBe(false)
    expect(isFilled('UNKNOWN')).toBe(false)
    expect(isFilled('CONFIRMED')).toBe(true)
  })
})

describe('风控参数修改', () => {
  it('调低上限是收紧，不需要二次确认', () => {
    const c = diffPolicy(DEFAULT_POLICY, { ...DEFAULT_POLICY, maxTradePct: 1 })
    expect(c).toHaveLength(1)
    expect(c[0].loosening).toBe(false)
    expect(needsSecondConfirm(c)).toBe(false)
  })
  it('调高上限、降低最小流动性、关闭一票否决都算放宽', () => {
    const c = diffPolicy(DEFAULT_POLICY, { ...DEFAULT_POLICY, maxTradePct: 3, minLiquidityUsd: 10_000, vetoOnAuthority: false })
    expect(c.every((x) => x.loosening)).toBe(true)
    expect(needsSecondConfirm(c)).toBe(true)
  })
  it('没改动时为空', () => {
    expect(diffPolicy(DEFAULT_POLICY, { ...DEFAULT_POLICY })).toEqual([])
  })
})

describe('角色定义', () => {
  it('10 个角色，风控/执行/持仓/总控禁止模型', () => {
    expect(ROLES).toHaveLength(10)
    for (const r of ['risk', 'execution', 'position', 'supervisor']) {
      expect(ROLES.find((x) => x.role === r)?.noModel).toBe(true)
    }
  })
})
