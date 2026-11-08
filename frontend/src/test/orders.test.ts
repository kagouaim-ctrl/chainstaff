import { filterTrades, tradesCsv } from '@/pages/Orders'
import { readTrades, saveTrade, type MyTrade } from '@/wallet/history'

const a: MyTrade = { chain: 'solana', address: 'Mint1111111111111111111111111111111', side: 'BUY', time: Date.UTC(2026, 10, 7), id: 'sig1', symbol: 'WIF, "x"', inAmount: '0.1', inSymbol: 'SOL', outAmount: '1234.5', outSymbol: 'WIF', usd: 8.93 }
const b: MyTrade = { chain: 'bsc', address: '0x' + '1'.repeat(40), side: 'SELL', time: Date.UTC(2026, 10, 6), id: '0xhash' }

describe('本设备成交记录', () => {
  beforeEach(() => localStorage.clear())
  it('按链和方向筛选', () => {
    expect(filterTrades([a, b], 'all', 'all')).toHaveLength(2)
    expect(filterTrades([a, b], 'bsc', 'all')).toEqual([b])
    expect(filterTrades([a, b], 'all', 'BUY')).toEqual([a])
    expect(filterTrades([a, b], 'solana', 'SELL')).toEqual([])
  })
  it('CSV 转义逗号和引号，旧记录缺的字段留空', () => {
    const lines = tradesCsv([a, b]).split('\n')
    expect(lines).toHaveLength(3)
    expect(lines[1]).toContain('"WIF, ""x"""')
    expect(lines[1]).toContain('0.1,SOL,1234.5,WIF,8.93,sig1')
    expect(lines[2]).toMatch(/卖出,,0x1+,,,,,,0xhash$/)
  })
  it('存储被改坏时不报错，也不显示坏数据', () => {
    localStorage.setItem('memeagent.trades', '{"x":1}')
    expect(readTrades()).toEqual([])
    localStorage.setItem('memeagent.trades', JSON.stringify([a, null, { id: 3 }]))
    expect(readTrades()).toEqual([a])
    saveTrade(b)
    expect(readTrades().map((t) => t.id)).toEqual(['0xhash', 'sig1'])
  })
})
