import { describe as group, expect, it } from 'vitest'
import { describe, runToChat, runToEvents, runToSteps, runToTheses, type Run, type RunEvent } from './live'
import { securityFlags, securityPass } from '@/features/tokenBits'
import type { SecurityCheck } from '@/domain/types'

const report = { score: 40, confidence: 0.6, summary: '买盘稳定', evidence: ['24 小时成交额 $5 万'], red_flags: ['前十持有偏高'] }
const events: RunEvent[] = [
  { seq: 1, ts: 1000, type: 'data_ready', agent: '' },
  { seq: 2, ts: 1001, type: 'prescreen', agent: '', prescreen: { passed: true, failed: [], rules: [{ key: 'liquidity', label: '流动性', status: 'pass', detail: 'x' }] } },
  { seq: 3, ts: 1002, type: 'analyst_report', agent: 'Market Analyst', label: '量价分析师', report },
  { seq: 4, ts: 1003, type: 'debate_message', agent: 'Bull Researcher', label: '多头研究员', round: 1, text: '能买' },
  { seq: 5, ts: 1004, type: 'debate_message', agent: 'Bear Researcher', label: '空头研究员', round: 1, text: '别买' },
  { seq: 6, ts: 1005, type: 'decision', agent: '', decision: { action: 'buy', position_pct: 2, blocked_by: [], adjustments: [], executed: false } },
]
const run: Run = { id: 'r', mint: 'M', status: 'done', created_at: 1000, events }

group('把后端分析转成界面内容', () => {
  it('分析师和辩论进群聊，依据放在 basis，预筛和决定是系统消息', () => {
    const chat = runToChat(run)
    expect(chat.map((m) => m.type)).toEqual(['system', 'text', 'text', 'text', 'system'])
    expect(chat[1].author?.name).toBe('量价分析师')
    expect(chat[1].author?.kind).toBe('agent')
    expect(chat[1].author?.holding).toBeNull()
    expect(chat[1].basis).toEqual(['24 小时成交额 $5 万'])
    expect(chat[1].text).toContain('前十持有偏高')
  })

  it('建议买入时明确说没有下单', () => {
    expect(describe(events[5], events)?.text).toContain('没有下单')
  })

  it('拦截原因用预筛规则的名字说明', () => {
    const rejected: RunEvent = { seq: 7, ts: 1, type: 'decision', agent: '', decision: { action: 'skip', position_pct: 0, blocked_by: ['prescreen:liquidity', 'risk_agent_veto'], adjustments: [], executed: false } }
    const text = describe(rejected, events)?.text
    expect(text).toContain('预筛没通过：流动性')
    expect(text).toContain('风控否决')
  })

  it('多空最后发言变成观点，决策链和动态包含每一步', () => {
    expect(runToTheses(run).map((t) => [t.stance, t.text])).toEqual([['bull', '能买'], ['bear', '别买']])
    expect(runToSteps(run).map((s) => s.agent)).toEqual(['risk', 'quant', 'strategy', 'strategy', 'risk'])
    expect(runToEvents(run, () => 'SYM')[0].token).toEqual({ address: 'M', symbol: 'SYM' })
  })

  it('失败的分析只显示错误类型', () => {
    const failed: RunEvent = { seq: 9, ts: 1, type: 'run_failed', agent: '', error: 'AuthenticationError' }
    expect(describe(failed, [])?.status).toBe('error')
    expect(describe(failed, [])?.text).toContain('AuthenticationError')
  })
})

group('查不到的安全数据', () => {
  it('显示未知，不算通过', () => {
    const s: SecurityCheck = { mintAuthority: null, freezeAuthority: null, lp: 'unknown', sellSimulation: 'unknown', transferFeeBps: null, top10Pct: null, devHoldPct: null, checkedAt: '2026-11-01T00:00:00Z' }
    expect(securityFlags(s).every((f) => f.ok === null)).toBe(true)
    expect(securityPass(s)).toBeNull()
  })
})
