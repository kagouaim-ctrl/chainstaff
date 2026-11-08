import { freshPaper, paperBuy, paperFill, paperSell, paperValue, PAPER_FEE, PaperError } from '@/features/paper'
import { TOKENS } from '@/api/demo'
import type { Token } from '@/domain/types'

const t: Token = { ...TOKENS[0], priceUsd: 1, liquidityUsd: 200_000, quality: undefined }

describe('模拟盘', () => {
  it('成交价按池子深度估算价格冲击', () => {
    expect(paperFill('BUY', 1, 1_000, 200_000)).toBeCloseTo(1.01)
    expect(paperFill('SELL', 1, 1_000, 200_000)).toBeCloseTo(0.99)
    expect(paperFill('SELL', 1, 100_000, 200_000)).toBeNull()
    expect(paperFill('BUY', 1, 10, 0)).toBeNull()
  })

  it('买入扣余额和手续费，卖出算实现盈亏', () => {
    let s = freshPaper(1_000, 0)
    s = paperBuy(s, t, 100, 'manual', 1)
    expect(s.cashUsd).toBe(900)
    const pos = s.positions[0]
    expect(pos.costUsd).toBe(100)
    expect(pos.qty).toBeCloseTo((100 * (1 - PAPER_FEE)) / 1.001, 6)
    // 价格翻倍后全部卖出
    s = paperSell(s, { ...t, priceUsd: 2 }, 100, 'manual', 2)
    expect(s.positions).toHaveLength(0)
    const sell = s.trades[0]
    expect(sell.side).toBe('SELL')
    expect(sell.realizedUsd!).toBeGreaterThan(90)
    expect(s.cashUsd).toBeCloseTo(900 + sell.usd)
  })

  it('部分卖出按比例结转成本', () => {
    let s = paperBuy(freshPaper(1_000, 0), t, 200)
    s = paperSell(s, t, 25)
    expect(s.positions[0].costUsd).toBeCloseTo(150)
  })

  it('余额不足、数据异常、没有持仓时拒绝', () => {
    const s = freshPaper(100, 0)
    expect(() => paperBuy(s, t, 101)).toThrow(PaperError)
    expect(() => paperBuy(s, { ...t, quality: { level: 'bad', reasons: [] } as unknown as Token['quality'] }, 10)).toThrow(/数据异常/)
    expect(() => paperBuy(s, { ...t, priceUsd: 0 }, 10)).toThrow(/价格/)
    expect(() => paperSell(s, t, 50)).toThrow(/没有这个代币/)
  })

  it('估值时价格未知的持仓不计入', () => {
    const s = paperBuy(freshPaper(1_000, 0), t, 100)
    const known = paperValue(s, () => 1)
    expect(known.totalUsd).toBeCloseTo(900 + s.positions[0].qty)
    const unknown = paperValue(s, () => null)
    expect(unknown.unknown).toBe(1)
    expect(unknown.totalUsd).toBe(900)
    expect(unknown.pnlUsd).toBe(-100)
  })
})
