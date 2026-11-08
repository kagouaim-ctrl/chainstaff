import { render } from '@testing-library/react'
import { FlashTd } from '@/features/columns'

const table = (v: number | null) => <table><tbody><tr><FlashTd v={v} className="c">{v}</FlashTd></tr></tbody></table>

describe('数值变化闪烁', () => {
  it('第一次不闪，涨了闪绿，跌了闪红', () => {
    const r = render(table(1))
    const td = () => r.container.querySelector('td')!
    expect(td().className).not.toMatch(/flash/)
    r.rerender(table(2))
    expect(td().className).toMatch(/flash-up/)
    r.rerender(table(1.5))
    expect(td().className).toMatch(/flash-down/)
  })
  it('值读不到时不闪', () => {
    const r = render(table(1))
    r.rerender(table(null))
    expect(r.container.querySelector('td')!.className).not.toMatch(/flash/)
  })
})
