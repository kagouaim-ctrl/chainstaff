import type { SecurityCheck, Token, Verdict } from '@/domain/types'
import { qualityText } from '@/api/pairs'
import { Tag } from '@/components/ui'
import { IShield } from '@/components/icons'

export const VERDICT: Record<Verdict, { label: string; tone: 'sub' | 'agent' | 'down' | 'warn' | 'up' }> = {
  unresearched: { label: '未研究', tone: 'sub' },
  researching: { label: '研究中', tone: 'agent' },
  security_veto: { label: '安全否决', tone: 'down' },
  no_trade: { label: '不交易', tone: 'warn' },
  buy_signal: { label: '建议买入', tone: 'up' },
  bought: { label: '已买入', tone: 'up' },
}

export function VerdictTag({ v }: { v: Verdict }) {
  return <Tag tone={VERDICT[v].tone}>{VERDICT[v].label}</Tag>
}

export interface Flag { label: string; ok: boolean | null }
export function securityFlags(s: SecurityCheck): Flag[] {
  return [
    { label: s.mintAuthority === null ? '增发权限未知' : s.mintAuthority ? '有增发权限' : '无增发权限', ok: s.mintAuthority === null ? null : !s.mintAuthority },
    { label: s.freezeAuthority === null ? '冻结权限未知' : s.freezeAuthority ? '有冻结权限' : '无冻结权限', ok: s.freezeAuthority === null ? null : !s.freezeAuthority },
    { label: s.lp === 'burned' ? 'LP 已销毁' : s.lp === 'locked' ? 'LP 已锁定' : s.lp === 'unlocked' ? 'LP 未锁定' : 'LP 未知', ok: s.lp === 'unknown' ? null : s.lp !== 'unlocked' },
    { label: s.sellSimulation === 'pass' ? '可卖出' : s.sellSimulation === 'fail' ? '卖出失败' : '卖出未验证', ok: s.sellSimulation === 'unknown' ? null : s.sellSimulation === 'pass' },
    { label: s.transferFeeBps === null ? '转账税未知' : s.transferFeeBps ? `转账税 ${(s.transferFeeBps / 100).toFixed(1)}%` : '无转账税', ok: s.transferFeeBps === null ? null : s.transferFeeBps === 0 },
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
    { k: '前十持有', v: s.top10Pct === null ? '未知' : `${s.top10Pct.toFixed(1)}%`, ok: s.top10Pct === null ? null : s.top10Pct > 50 ? false : s.top10Pct > 30 ? null : true },
    { k: '开发者', v: s.devHoldPct === null ? '未知' : `${s.devHoldPct.toFixed(1)}%`, ok: s.devHoldPct === null ? null : s.devHoldPct > 10 ? false : s.devHoldPct > 5 ? null : true },
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

/** 数据可信度。bad：数值不展示、不能交易、不能交给 AI 研究。 */
export const dataBad = (t: Token) => t.quality?.level === 'bad'
export const qualityTitle = (t: Token) => (t.quality?.flags ?? []).map(qualityText).join('；')

/** 数据异常或可疑时的标记；可信时不显示。悬停看具体原因。 */
export function QualityBadge({ t }: { t: Token }) {
  const q = t.quality
  if (!q || q.level === 'ok') return null
  const bad = q.level === 'bad'
  return (
    <span title={qualityTitle(t)} aria-label={`${bad ? '数据异常' : '数据可疑'}：${qualityTitle(t)}`}
      className={`inline-flex h-[18px] shrink-0 items-center rounded px-1 text-[11px] font-medium ${bad ? 'bg-warn/15 text-warn' : 'bg-raised text-warn'}`}>
      {bad ? '数据异常' : '可疑'}
    </span>
  )
}

/** 数据异常时，数值位置显示「异常」而不是可能错误的数字。 */
export function Masked({ t, children }: { t: Token; children: React.ReactNode }) {
  return dataBad(t) ? <span className="text-warn" title={qualityTitle(t)}>异常</span> : <>{children}</>
}
