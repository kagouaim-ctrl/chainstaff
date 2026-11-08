import { toEn } from '@/lib/i18n'

describe('英文界面', () => {
  it('整段匹配词表，保留首尾空格', () => {
    expect(toEn('发现')).toBe('Discover')
    expect(toEn(' 买入 ')).toBe(' Buy ')
  })
  it('数字短语和万/亿单位', () => {
    expect(toEn('25 天')).toBe('25d')
    expect(toEn('$2.46亿')).toBe('$246M')
    expect(toEn('$6.79万')).toBe('$67.9K')
    expect(toEn('3 分钟前')).toBe('3m ago')
  })
  it('词表里没有的保持原文，英文不动', () => {
    expect(toEn('一段没收录的话')).toBe('一段没收录的话')
    expect(toEn('PEPE')).toBe('PEPE')
  })
})
