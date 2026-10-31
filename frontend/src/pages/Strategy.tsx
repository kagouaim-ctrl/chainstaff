import { useEffect, useState } from 'react'
import { useAudit, useDataSources, useStrategy } from '@/api/hooks'
import { useToast } from '@/app/store'
import { DataState, Panel, Button, Dialog, Tag, ModeTag, cx } from '@/components/ui'
import { ILock } from '@/components/icons'
import { POLICY_FIELDS, diffPolicy, needsSecondConfirm, DEFAULT_POLICY } from '@/domain/risk'
import type { RiskPolicy } from '@/domain/types'
import { clock } from '@/lib/format'

const fmt = (v: number | boolean) => (typeof v === 'boolean' ? (v ? '开启' : '关闭') : v.toLocaleString())

const UNLOCK = [
  '≥ 14 天连续模拟盘，并输出验证报告（指标、与基准对比）',
  '故障注入测试全部通过（第 10 节每一项）',
  '签名服务上线，私钥不出签名服务',
  '独立安全审查通过',
  '你手动授权；初始资金上限 $100',
]

export function Strategy() {
  const q = useStrategy()
  const audit = useAudit()
  const ds = useDataSources()
  const toast = useToast((s) => s.show)
  const [draft, setDraft] = useState<RiskPolicy | null>(null)
  const [confirm, setConfirm] = useState<0 | 1 | 2>(0)
  useEffect(() => { if (q.data && !draft) setDraft({ ...q.data.policy }) }, [q.data, draft])
  const changes = q.data && draft ? diffPolicy(q.data.policy, draft) : []
  const loosen = needsSecondConfirm(changes)

  return (
    <div className="grid grid-cols-1 gap-4 [&>*]:min-w-0 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-4">
        <Panel demo title="策略">
          <DataState q={q} rows={3}>
            {(s) => (
              <dl className="grid gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2">
                <div><dt className="text-xs text-sub">网络</dt><dd className="mt-0.5">Solana <span className="text-xs text-sub">（BSC、Base 未适配，不可选）</span></dd></div>
                <div><dt className="text-xs text-sub">监控范围</dt><dd className="mt-0.5">{s.scope}</dd></div>
                <div><dt className="text-xs text-sub">策略模板</dt><dd className="mt-0.5">{s.template} <span className="text-xs text-sub">v{s.version}</span></dd></div>
                <div><dt className="text-xs text-sub">风险预算</dt><dd className="num mt-0.5">${s.riskBudgetUsd.toLocaleString()}（模拟资金）</dd></div>
                <div className="sm:col-span-2">
                  <dt className="text-xs text-sub">交易模式</dt>
                  <dd className="mt-1.5 flex flex-wrap items-center gap-2">
                    <span className="flex items-center gap-1.5 rounded-lg border border-paper/40 bg-paper/10 px-3 py-1.5 text-paper"><span className="h-1.5 w-1.5 rounded-full bg-paper" />模拟盘（当前）</span>
                    <span className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sub" aria-disabled="true"><ILock />实盘（锁定）</span>
                  </dd>
                  <ul className="mt-2 space-y-1 text-xs text-sub">
                    <li className="text-fg/80">实盘解锁条件（全部满足，且不会自动从模拟切换到实盘）：</li>
                    {UNLOCK.map((u) => <li key={u} className="flex gap-2"><span className="text-down">✕</span>{u}</li>)}
                  </ul>
                </div>
              </dl>
            )}
          </DataState>
        </Panel>

        <Panel demo title="风控参数" right={<span className="hidden text-xs text-sub sm:inline">由确定性程序强制执行，模型无法读取或修改</span>} bodyClass="p-0">
          {draft && q.data && (
            <>
              <ul>
                {POLICY_FIELDS.map((f) => {
                  const cur = draft[f.key]
                  const changed = q.data!.policy[f.key] !== cur
                  const c = changes.find((x) => x.key === f.key)
                  return (
                    <li key={f.key} className={cx('flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-0', changed && 'bg-raised/60')}>
                      <div className="min-w-0 flex-1">
                        <label htmlFor={`f-${f.key}`} className="text-[13px] font-medium">{f.label}</label>
                        <p className="text-xs text-sub">{f.help} · 默认 {fmt(DEFAULT_POLICY[f.key])}{f.unit && ` ${f.unit}`}</p>
                      </div>
                      {c && <Tag tone={c.loosening ? 'down' : 'up'}>{c.loosening ? '放宽' : '收紧'}</Tag>}
                      {typeof cur === 'boolean' ? (
                        <input id={`f-${f.key}`} type="checkbox" checked={cur} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.checked })} className="h-4 w-4 accent-[var(--c-agent)]" />
                      ) : (
                        <span className="flex items-center gap-1.5">
                          <input id={`f-${f.key}`} type="number" value={cur} min={f.min} max={f.max} step={f.step}
                            onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                            className="num h-8 w-28 rounded-lg border border-line bg-bg px-2 text-right text-[13px] outline-none focus:border-agent" />
                          <span className="w-16 text-xs text-sub">{f.unit}</span>
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
              <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
                <span className="text-xs text-sub">{changes.length ? `${changes.length} 项改动${loosen ? '，含放宽，需二次确认' : ''}` : '没有改动'}</span>
                <Button className="ml-auto" disabled={!changes.length} onClick={() => setDraft({ ...q.data!.policy })}>撤销</Button>
                <Button variant="primary" disabled={!changes.length} onClick={() => setConfirm(1)}>保存</Button>
              </div>
            </>
          )}
        </Panel>
      </div>

      <div className="space-y-4">
        <Panel demo title="修改审计记录" bodyClass="p-0">
          <DataState q={audit} isEmpty={(d) => d.length === 0} empty="还没有修改记录。">
            {(rows) => (
              <ul>
                {rows.map((a, i) => (
                  <li key={i} className="border-b border-line px-4 py-3 text-[13px] last:border-0">
                    <div className="flex items-center gap-2"><span className="font-medium">{a.field}</span>{a.confirmedTwice && <Tag tone="warn">二次确认</Tag>}</div>
                    <div className="num mt-0.5 text-xs"><span className="text-sub">{a.from}</span> → {a.to}</div>
                    <div className="mt-0.5 text-xs text-sub">{a.actor} · {clock(a.at)}</div>
                  </li>
                ))}
              </ul>
            )}
          </DataState>
        </Panel>
        <Panel title="数据源" bodyClass="p-0">
          <DataState q={ds}>
            {(rows) => (
              <ul>
                {rows.map((d) => (
                  <li key={d.name} className="flex items-center gap-3 border-b border-line px-4 py-2.5 text-[13px] last:border-0">
                    <div className="min-w-0 flex-1"><div className="font-medium">{d.name}</div><div className="text-xs text-sub">{d.kind}</div></div>
                    <Tag tone={d.state === 'connected' ? 'up' : 'warn'}>{d.state === 'connected' ? '已接入' : '未接入'}</Tag>
                  </li>
                ))}
              </ul>
            )}
          </DataState>
        </Panel>
      </div>

      <Dialog open={confirm > 0} onClose={() => setConfirm(0)} title={confirm === 2 ? '再次确认：你正在放宽风控' : '确认保存风控参数'}
        footer={<>
          <Button onClick={() => setConfirm(0)}>取消</Button>
          {confirm === 1 && loosen
            ? <Button variant="danger" onClick={() => setConfirm(2)}>继续</Button>
            : <Button variant={loosen ? 'down' : 'primary'} onClick={() => { setConfirm(0); toast('后端未接入：参数没有保存，也没有写入审计日志。') }}>确认保存</Button>}
        </>}>
        <ul className="space-y-1.5">
          {changes.map((c) => (
            <li key={c.key} className="flex items-center gap-2 text-[13px]">
              <Tag tone={c.loosening ? 'down' : 'up'}>{c.loosening ? '放宽' : '收紧'}</Tag>{c.label}
              <span className="num ml-auto"><span className="text-sub">{fmt(c.from)}</span> → {fmt(c.to)}</span>
            </li>
          ))}
        </ul>
        {confirm === 2 && <p className="mt-3 rounded-lg border border-down/30 bg-down/10 p-2.5 text-down">放宽后单笔风险或亏损上限会变大。修改会写入审计日志，并从下一个交易方案开始生效。</p>}
        <p className="mt-3 text-xs text-sub">当前模式：<ModeTag mode="paper" /></p>
      </Dialog>
    </div>
  )
}
