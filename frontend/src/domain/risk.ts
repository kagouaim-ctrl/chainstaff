import type { RiskPolicy } from './types'

/** 风控默认值（计划第 4 节）。用户可调低；调高（放宽）需二次确认并写审计日志。 */
export const DEFAULT_POLICY: RiskPolicy = {
  maxTradePct: 2,
  maxTokenExposurePct: 5,
  maxPositions: 8,
  dailyLossPct: 5,
  maxSlippageBps: 300,
  minLiquidityUsd: 50_000,
  minTokenAgeMin: 10,
  cooldownMin: 30,
  maxTradesPerHour: 10,
  vetoOnAuthority: true,
}

export interface FieldDef {
  key: keyof RiskPolicy
  label: string
  unit: string
  /** 数值变大是放宽还是收紧 */
  looserWhen: 'higher' | 'lower' | 'false'
  min?: number
  max?: number
  step?: number
  help: string
}

export const POLICY_FIELDS: FieldDef[] = [
  { key: 'maxTradePct', label: '单笔最大金额', unit: '% 总资金', looserWhen: 'higher', min: 0.1, max: 100, step: 0.1, help: '每笔交易最多动用总资金的比例' },
  { key: 'maxTokenExposurePct', label: '单币最大敞口', unit: '% 总资金', looserWhen: 'higher', min: 0.1, max: 100, step: 0.1, help: '单个代币持仓市值上限' },
  { key: 'maxPositions', label: '同时持仓数', unit: '个', looserWhen: 'higher', min: 1, max: 50, step: 1, help: '同时持有的代币数量上限' },
  { key: 'dailyLossPct', label: '每日最大亏损', unit: '% 总资金', looserWhen: 'higher', min: 0.5, max: 50, step: 0.5, help: '触发后当日自动暂停' },
  { key: 'maxSlippageBps', label: '最大滑点', unit: 'bps', looserWhen: 'higher', min: 10, max: 5000, step: 10, help: '超过则拒绝交易' },
  { key: 'minLiquidityUsd', label: '最小流动性', unit: 'USD', looserWhen: 'lower', min: 0, step: 1000, help: '池子流动性低于此值不交易' },
  { key: 'minTokenAgeMin', label: '最小代币年龄', unit: '分钟', looserWhen: 'lower', min: 0, step: 1, help: '新池子上线不足此时长不交易' },
  { key: 'cooldownMin', label: '同币冷却', unit: '分钟', looserWhen: 'lower', min: 0, step: 1, help: '卖出后多久内不再买入同一代币' },
  { key: 'maxTradesPerHour', label: '每小时最多交易', unit: '笔', looserWhen: 'higher', min: 1, max: 100, step: 1, help: '超过频率后拒绝新指令' },
  { key: 'vetoOnAuthority', label: '安全一票否决', unit: '', looserWhen: 'false', help: '存在 Mint / Freeze 权限、LP 未锁定、模拟卖出失败 → 拒绝' },
]

export interface PolicyChange { key: keyof RiskPolicy; label: string; from: number | boolean; to: number | boolean; loosening: boolean }

export function diffPolicy(before: RiskPolicy, after: RiskPolicy): PolicyChange[] {
  const out: PolicyChange[] = []
  for (const f of POLICY_FIELDS) {
    const a = before[f.key]
    const b = after[f.key]
    if (a === b) continue
    let loosening: boolean
    if (f.looserWhen === 'false') loosening = b === false
    else if (f.looserWhen === 'higher') loosening = (b as number) > (a as number)
    else loosening = (b as number) < (a as number)
    out.push({ key: f.key, label: f.label, from: a, to: b, loosening })
  }
  return out
}

export function needsSecondConfirm(changes: PolicyChange[]): boolean {
  return changes.some((c) => c.loosening)
}
