import { screen } from '@testing-library/react'
import { renderRoute } from './render'
import { leaderboard, NotConfigured } from '@/api/wallets'

describe('钱包情报', () => {
  it('没有后端时不发请求，直接报未配置', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    await expect(leaderboard()).rejects.toBeInstanceOf(NotConfigured)
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  })
  it('排行页在未配置时说明原因，不显示空表', async () => {
    renderRoute('/smart-money')
    expect(await screen.findByText(/BIRDEYE_API_KEY/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
  it('钱包页同样说明未配置', async () => {
    renderRoute('/wallet/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM')
    expect(await screen.findByText(/BIRDEYE_API_KEY/)).toBeInTheDocument()
  })
  it('新币页可访问', async () => {
    renderRoute('/new')
    expect(await screen.findByRole('heading', { level: 1, name: '新币' })).toBeInTheDocument()
  })
})
