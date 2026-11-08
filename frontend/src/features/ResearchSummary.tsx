import type { RunSummary } from '@/api/live'
import { Tag } from '@/components/ui'
import { clock } from '@/lib/format'

const STATUS: Record<RunSummary['status'], { label: string; tone: 'agent' | 'up' | 'down' }> = {
  running: { label: '分析中', tone: 'agent' },
  done: { label: '已完成', tone: 'up' },
  failed: { label: '没跑完', tone: 'down' },
}

/** 最终结论一句话。只用后端给的字段，没有决定时如实说明。 */
export function headline(s: RunSummary): { text: string; tone: 'up' | 'warn' | 'down' | 'sub' } {
  if (s.decision?.action === 'buy') return { text: `建议买入，仓位 ${s.decision.positionPct}%`, tone: 'up' }
  if (s.decision) return { text: '不买', tone: 'warn' }
  if (s.status === 'failed') return { text: '没有结论（分析失败）', tone: 'down' }
  if (s.status === 'running') return { text: '还没有结论', tone: 'sub' }
  return { text: '没有结论', tone: 'sub' }
}

/** 代币页「Agent 研究」顶部的摘要卡片：结论、理由、多空观点、风控和各分析师评分。 */
export function ResearchSummaryCard({ s }: { s: RunSummary }) {
  const h = headline(s)
  const st = STATUS[s.status]
  return (
    <section aria-label="AI 研究摘要" className="space-y-3 rounded-xl border border-agent/40 bg-agent/5 p-4 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">AI 研究结论</span>
        <Tag tone={h.tone}>{h.text}</Tag>
        <Tag tone={st.tone}>{st.label}</Tag>
        <span className="num ml-auto text-xs text-sub" title={`开始 ${clock(s.startedAt)}`}>更新于 {clock(s.updatedAt)}</span>
      </div>

      {s.decision && s.decision.action !== 'buy' && s.decision.blocked && <p className="text-fg/85">原因：{s.decision.blocked}</p>}
      {s.decision && <p className="text-xs text-sub">{s.decision.executed ? '这次分析下了单。' : '只给出分析和决定，没有下单。'}</p>}
      {s.error && <p className="text-down">这次分析没跑完：{s.error}</p>}
      {s.prescreen && !s.prescreen.passed && <p className="text-warn">硬规则预筛没通过：{s.prescreen.failed.join('；') || '见下方记录'}</p>}

      {s.stance && <p><span className="text-sub">研究主管：</span>{s.stance.label}。{s.stance.rationale}</p>}
      {s.review && <p><span className="text-sub">风控：</span><span className={s.review.veto ? 'text-down' : ''}>{s.review.label}</span>{s.review.worstCase && `。最坏情况：${s.review.worstCase}`}</p>}

      {(s.bull || s.bear) && (
        <div className="grid gap-2 sm:grid-cols-2">
          {s.bull && <div className="rounded-lg border border-up/30 p-2.5"><div className="mb-1 text-xs font-medium text-up">看多理由</div><p className="text-fg/85">{s.bull}</p></div>}
          {s.bear && <div className="rounded-lg border border-down/30 p-2.5"><div className="mb-1 text-xs font-medium text-down">看空理由</div><p className="text-fg/85">{s.bear}</p></div>}
        </div>
      )}

      {s.analysts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px]">
            <thead><tr className="text-left text-xs text-sub"><th className="py-1 font-normal">分析师</th><th className="py-1 text-right font-normal">评分</th><th className="py-1 text-right font-normal">置信度</th><th className="py-1 pl-3 font-normal">要点</th></tr></thead>
            <tbody>
              {s.analysts.map((a) => (
                <tr key={a.name} className="border-t border-line align-top">
                  <td className="whitespace-nowrap py-1.5 pr-2">{a.name}</td>
                  <td className={`num py-1.5 text-right ${a.score > 0 ? 'text-up' : a.score < 0 ? 'text-down' : ''}`}>{a.score > 0 ? '+' : ''}{a.score}</td>
                  <td className="num py-1.5 text-right">{Math.round(a.confidence * 100)}%</td>
                  <td className="py-1.5 pl-3 text-fg/85">{a.summary}{a.redFlags.length > 0 && <span className="text-warn"> 风险：{a.redFlags.join('；')}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-sub">由多个模型分析公开数据得出，可能出错，不构成投资建议。完整过程见下方记录。</p>
    </section>
  )
}
