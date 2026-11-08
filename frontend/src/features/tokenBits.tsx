import type { SecurityCheck, Token, Verdict } from '@/domain/types'
import { qualityText } from '@/api/pairs'
import { Chg, Tag } from '@/components/ui'
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

export interface Flag { label: string; ok: boolean | null; note?: string }
export function securityFlags(s: SecurityCheck): Flag[] {
  return [
    { label: s.mintAuthority === null ? '增发权限未知' : s.mintAuthority ? '有增发权限' : '无增发权限', ok: s.mintAuthority === null ? null : !s.mintAuthority },
    { label: s.freezeAuthority === null ? '冻结权限未知' : s.freezeAuthority ? '有冻结权限' : '无冻结权限', ok: s.freezeAuthority === null ? null : !s.freezeAuthority },
    s.lp === 'na'
      ? { label: 'LP 锁定不适用', ok: true, note: s.lpNote ?? '这类池子没有可锁定的 LP 代币' }
      : { label: s.lp === 'burned' ? 'LP 已销毁' : s.lp === 'locked' ? 'LP 已锁定' : s.lp === 'unlocked' ? 'LP 未锁定' : 'LP 未知', ok: s.lp === 'unknown' ? null : s.lp !== 'unlocked' },
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

export type RiskLevel = 'high' | 'warn' | 'pass' | 'unknown'
export interface RiskItem { label: string; level: 'high' | 'warn' }

/**
 * 安全分三档，每一档都列出具体原因（参考 GMGN 把安全拆成独立项）：
 * high：会直接让钱拿不回来——卖不出去、能增发、能冻结、税率 ≥ 10%、LP 未锁定
 * warn：需要注意——前十持有 > 30%、开发者持有 > 5%、有买卖税、部分项目查不到
 * pass：查到的项目都没问题
 * unknown：还没有安全检查结果
 */
export function riskItems(s: SecurityCheck): RiskItem[] {
  const out: RiskItem[] = []
  if (s.sellSimulation === 'fail') out.push({ label: '卖出失败（可能是貔貅盘）', level: 'high' })
  if (s.mintAuthority) out.push({ label: '有增发权限', level: 'high' })
  if (s.freezeAuthority) out.push({ label: '有冻结权限', level: 'high' })
  if (s.lp === 'unlocked') out.push({ label: 'LP 未锁定', level: 'high' })
  if (s.transferFeeBps != null && s.transferFeeBps >= 1000) out.push({ label: `买卖税 ${(s.transferFeeBps / 100).toFixed(1)}%`, level: 'high' })
  else if (s.transferFeeBps) out.push({ label: `买卖税 ${(s.transferFeeBps / 100).toFixed(1)}%`, level: 'warn' })
  if (s.top10Pct != null && s.top10Pct > 50) out.push({ label: `前十持有 ${s.top10Pct.toFixed(1)}%`, level: 'high' })
  else if (s.top10Pct != null && s.top10Pct > 30) out.push({ label: `前十持有 ${s.top10Pct.toFixed(1)}%`, level: 'warn' })
  if (s.devHoldPct != null && s.devHoldPct > 5) out.push({ label: `开发者持有 ${s.devHoldPct.toFixed(1)}%`, level: s.devHoldPct > 20 ? 'high' : 'warn' })
  const unknown = securityFlags(s).filter((f) => f.ok === null).map((f) => f.label)
  if (unknown.length) out.push({ label: unknown.join('、'), level: 'warn' })
  return out
}

export function riskLevel(s: SecurityCheck): RiskLevel {
  if (s.checkedAt == null) return 'unknown'
  const items = riskItems(s)
  return items.some((i) => i.level === 'high') ? 'high' : items.length ? 'warn' : 'pass'
}

const RISK_UI: Record<RiskLevel, { label: string; cls: string }> = {
  high: { label: '高危', cls: 'text-down' },
  warn: { label: '注意', cls: 'text-warn' },
  pass: { label: '通过', cls: 'text-up' },
  unknown: { label: '未检查', cls: 'text-sub' },
}

/** 列表里的紧凑安全标记：颜色分三档，悬停列出每一项原因。 */
export function SecurityBadge({ s }: { s: SecurityCheck }) {
  const lv = riskLevel(s)
  const items = riskItems(s)
  const title = lv === 'unknown' ? '还没有安全检查结果（打开代币页会检查）' : lv === 'pass' ? '查到的安全项目都通过' : items.map((i) => `${i.level === 'high' ? '高危' : '注意'}：${i.label}`).join('\n')
  return (
    <span title={title} aria-label={`安全${RISK_UI[lv].label}：${title}`} className={`inline-flex items-center gap-0.5 text-[11px] ${RISK_UI[lv].cls}`}>
      <IShield />{lv !== 'unknown' && lv !== 'pass' && <span>{RISK_UI[lv].label}</span>}
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

/** 开盘不足 24 小时：涨幅从初始价算起，几百倍、几千倍很常见，和老币的涨幅不能比。 */
export const isNewPair = (t: Token) => t.ageMinutes > 0 && t.ageMinutes < 24 * 60
/** 疑似已 Rug：24 小时跌 90% 以上，流动性也只剩不到 $1 万。默认不出现在热门里。 */
export const rugLike = (t: Token) => t.stats['24h'].change <= -90 && t.liquidityUsd < 10_000

/** 列表里的涨跌：新币的大涨幅换成「新币」标记，疑似 Rug 加标记，数据异常显示「异常」。 */
export function ChangeCell({ t, v, className }: { t: Token; v: number; className?: string }) {
  if (dataBad(t)) return <span className="text-warn" title={qualityTitle(t)}>异常</span>
  if (isNewPair(t) && v > 1000) return <span title={`开盘不足 24 小时，涨幅从初始价算起：+${Math.round(v).toLocaleString()}%`} className="rounded bg-agent/15 px-1.5 py-0.5 text-[12px] text-agent">新币</span>
  if (rugLike(t)) return <span title={`24 小时 ${v.toFixed(1)}%，流动性不足 $1 万`} className="rounded bg-down/15 px-1.5 py-0.5 text-[12px] text-down">疑似 Rug</span>
  return <Chg v={v} className={className} />
}
