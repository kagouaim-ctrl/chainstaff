import type { SecurityCheck, Verdict } from '@/domain/types'
import { Tag } from '@/components/ui'
import { IShield } from '@/components/icons'

export const VERDICT: Record<Verdict, { label: string; tone: 'sub' | 'agent' | 'down' | 'warn' | 'up' }> = {
  unresearched: { label: '未研究', tone: 'sub' },
  researching: { label: '研究中', tone: 'agent' },
  security_veto: { label: '安全否决', tone: 'down' },
  no_trade: { label: '不交易', tone: 'warn' },
  bought: { label: '已买入', tone: 'up' },
}

export function VerdictTag({ v }: { v: Verdict }) {
  return <Tag tone={VERDICT[v].tone}>{VERDICT[v].label}</Tag>
}

export interface Flag { label: string; ok: boolean | null }
export function securityFlags(s: SecurityCheck): Flag[] {
  return [
    { label: '无增发权限', ok: !s.mintAuthority },
    { label: '无冻结权限', ok: !s.freezeAuthority },
    { label: s.lp === 'burned' ? 'LP 已销毁' : s.lp === 'locked' ? 'LP 已锁定' : s.lp === 'unlocked' ? 'LP 未锁定' : 'LP 未知', ok: s.lp === 'unknown' ? null : s.lp !== 'unlocked' },
    { label: s.sellSimulation === 'pass' ? '可卖出' : s.sellSimulation === 'fail' ? '模拟卖出失败' : '卖出未验证', ok: s.sellSimulation === 'unknown' ? null : s.sellSimulation === 'pass' },
    { label: s.transferFeeBps ? `转账税 ${(s.transferFeeBps / 100).toFixed(1)}%` : '无转账税', ok: s.transferFeeBps === 0 },
  ]
}

export function securityPass(s: SecurityCheck): boolean | null {
  const f = securityFlags(s)
  if (f.slice(0, 4).some((x) => x.ok === false)) return false
  if (s.checkedAt == null || f.some((x) => x.ok === null)) return null
  return true
}

/** 列表里的紧凑安全标记。 */
export function SecurityBadge({ s }: { s: SecurityCheck }) {
  const p = securityPass(s)
  const bad = securityFlags(s).filter((x) => x.ok === false).map((x) => x.label)
  const title = s.checkedAt == null ? '尚未检查' : p === false ? bad.join('、') : p === null ? '部分项目未验证' : '安全检查通过'
  return (
    <span title={title} aria-label={`安全：${title}`} className={p === false ? 'text-down' : p === null ? 'text-warn' : 'text-up'}>
      <IShield />
    </span>
  )
}

/** 代币头部的风险速览（参考 gmgn）：一眼看到持仓集中度和合约权限，点进安全检查看细节。 */
export function RiskStrip({ s }: { s: SecurityCheck }) {
  if (s.checkedAt == null) return <div className="text-[12px] font-normal text-warn">安全检查还没完成，结果无法确认时风控默认拒绝买入</div>
  const tone = (ok: boolean | null) => ok === false ? 'text-down bg-down/10' : ok === null ? 'text-warn bg-warn/10' : 'text-sub bg-raised'
  const items: { k: string; v: string; ok: boolean | null }[] = [
    { k: '前十持有', v: `${s.top10Pct.toFixed(1)}%`, ok: s.top10Pct > 50 ? false : s.top10Pct > 30 ? null : true },
    { k: '开发者', v: `${s.devHoldPct.toFixed(1)}%`, ok: s.devHoldPct > 10 ? false : s.devHoldPct > 5 ? null : true },
    ...securityFlags(s).map((f) => ({ k: f.label, v: '', ok: f.ok })),
  ]
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="风险速览">
      {items.map((i) => (
        <li key={i.k} className={`flex h-6 items-center gap-1 rounded-md px-2 text-[12px] ${tone(i.ok)}`}>
          {i.k}{i.v && <span className="num text-fg">{i.v}</span>}
        </li>
      ))}
    </ul>
  )
}
