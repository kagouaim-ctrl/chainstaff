import { runToSummary, type Run } from '@/api/live'
import { headline } from '@/features/ResearchSummary'

const run = (events: Run['events'], status: Run['status'] = 'done'): Run => ({ id: 'r1', mint: 'M', status, created_at: 100, events })

describe('AI 研究摘要', () => {
  it('只用后端事件里的字段', () => {
    const s = runToSummary(run([
      { seq: 1, ts: 101, type: 'prescreen', agent: '', prescreen: { passed: true, failed: [], rules: [] } },
      { seq: 2, ts: 102, type: 'analyst_report', agent: 'Security Analyst', label: '安全', report: { score: -2, confidence: 0.8, summary: '有 mint 权限', evidence: [], red_flags: ['mint'] } },
      { seq: 3, ts: 103, type: 'debate_message', agent: 'Bull Researcher', text: '量在涨' },
      { seq: 4, ts: 104, type: 'debate_message', agent: 'Bear Researcher', text: '权限没放弃' },
      { seq: 5, ts: 105, type: 'research_plan', agent: 'Research Manager', plan: { stance: 'avoid', rationale: '风险大' } },
      { seq: 6, ts: 106, type: 'risk_review', agent: 'Risk Agent', review: { verdict: 'veto', worst_case: '归零', reasons: [] } },
      { seq: 7, ts: 107, type: 'decision', agent: '', decision: { action: 'skip', position_pct: 0, blocked_by: ['risk_agent_veto'], adjustments: [], executed: false } },
    ]))
    expect(s.decision).toEqual({ action: 'skip', positionPct: 0, blocked: '风控否决', executed: false })
    expect(s.stance).toEqual({ label: '回避', rationale: '风险大' })
    expect(s.review).toMatchObject({ label: '否决', veto: true })
    expect(s.analysts).toEqual([{ name: '安全', score: -2, confidence: 0.8, summary: '有 mint 权限', redFlags: ['mint'] }])
    expect(s.bull).toBe('量在涨')
    expect(s.bear).toBe('权限没放弃')
    expect(s.updatedAt).toBe(new Date(107_000).toISOString())
    expect(headline(s)).toEqual({ text: '不买', tone: 'warn' })
  })

  it('没跑到决定时不编结论', () => {
    const s = runToSummary(run([{ seq: 1, ts: 101, type: 'run_failed', agent: '', error: 'timeout' }], 'failed'))
    expect(s.decision).toBeNull()
    expect(s.stance).toBeNull()
    expect(s.bull).toBeNull()
    expect(s.error).toBe('timeout')
    expect(headline(s).text).toBe('没有结论（分析失败）')
    expect(headline(runToSummary(run([], 'running'))).text).toBe('还没有结论')
  })
})
