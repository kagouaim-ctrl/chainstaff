import { screen, within } from '@testing-library/react'
import { renderRoute } from './render'
import { LEGAL_LINKS, DOCS } from '@/pages/Legal'

describe('条款、隐私、风险披露、费用、帮助', () => {
  it.each(LEGAL_LINKS)('/legal/%s 可访问', async (k) => {
    renderRoute(`/legal/${k}`)
    expect(await screen.findByRole('heading', { level: 1, name: DOCS[k].title })).toBeInTheDocument()
  })

  it('页脚每一项都是可点击的链接', () => {
    renderRoute('/')
    const footer = screen.getByRole('contentinfo')
    for (const [k, l] of LEGAL_LINKS) expect(within(footer).getByRole('link', { name: l })).toHaveAttribute('href', `/legal/${k}`)
  })

  it('费用页写明平台手续费', async () => {
    renderRoute('/legal/fees')
    expect(await screen.findByText('平台手续费：0')).toBeInTheDocument()
  })
})
