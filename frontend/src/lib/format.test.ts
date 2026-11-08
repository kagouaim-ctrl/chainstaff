import { compactCN, usd, price, pct, age, relTime, shortAddr } from './format'

describe('format', () => {
  it('万 / 亿 缩写', () => {
    expect(compactCN(9999)).toBe('9999')
    expect(compactCN(12_345)).toBe('1.23万')
    expect(compactCN(320_000_000)).toBe('3.2亿')
    expect(compactCN(-50_000)).toBe('-5万')
    expect(compactCN(13_112_900)).toBe('1311万')
    expect(compactCN(1_234_500)).toBe('123.5万')
  })
  it('美元金额', () => {
    expect(usd(1234.5)).toBe('$1,234.50')
    expect(usd(2_500_000)).toBe('$250万')
    expect(usd(12.3, { sign: true })).toBe('+$12.30')
    expect(usd(-12.3)).toBe('-$12.30')
    expect(usd(null)).toBe('--')
    expect(usd(0.0011)).toBe('$0.0011')
    expect(usd(0)).toBe('$0.00')
  })
  it('极小价格用下标零', () => {
    expect(price(0.00001234)).toBe('$0.0₄1234')
    expect(price(0.0123)).toBe('$0.0123')
    expect(price(1.5)).toBe('$1.5')
    expect(price(undefined)).toBe('--')
  })
  it('百分比带符号', () => {
    expect(pct(3.456)).toBe('+3.46%')
    expect(pct(-1)).toBe('-1.00%')
    expect(pct(0)).toBe('0.00%')
  })
  it('年龄与相对时间', () => {
    expect(age(12)).toBe('12 分钟')
    expect(age(130)).toBe('2 小时')
    expect(age(3000)).toBe('2 天')
    const now = new Date('2026-10-31T10:00:00Z')
    expect(relTime('2026-10-31T09:58:00Z', now)).toBe('2 分钟前')
    expect(relTime('2026-10-31T07:00:00Z', now)).toBe('3 小时前')
  })
  it('地址缩写', () => {
    expect(shortAddr('7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU')).toBe('7xKX…gAsU')
  })
})
