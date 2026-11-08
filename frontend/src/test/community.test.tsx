import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { TOKENS } from '@/api/demo'
import type { Post } from '@/api/social'

const posts: Post[] = [
  { id: 1, chain: 'solana', token: 'x', wallet: '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM', kind: 'thesis', text: '会涨 <b>不是 HTML</b>', stance: 'bull', priceAt: 0.5, createdAt: 1_700_000_000 },
  { id: 2, chain: 'solana', token: 'x', wallet: 'So11111111111111111111111111111111111111112', kind: 'thesis', text: '会跌', stance: 'bear', priceAt: null, createdAt: 1_700_000_000 },
]
vi.mock('@/api/social', async (orig) => ({ ...(await orig<typeof import('@/api/social')>()), theses: vi.fn(async () => posts), messages: vi.fn(async () => []) }))

import { CommunityChat, CommunityTheses } from '@/features/Community'
import { messages } from '@/api/social'

const wrap = (el: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{el}</MemoryRouter></QueryClientProvider>)

describe('社区观点', () => {
  const t = { ...TOKENS[0], priceUsd: 1, quality: { level: 'ok' as const, flags: [] } }
  it('显示发布时价格和至今涨跌，看多且上涨是判断对', async () => {
    wrap(<CommunityTheses t={t} />)
    expect(await screen.findByText('+100.0%')).toBeInTheDocument()
    expect(screen.getByText('（目前判断对）')).toBeInTheDocument()
  })
  it('没有记录价格的观点直说，不编涨跌', async () => {
    wrap(<CommunityTheses t={t} />)
    expect(await screen.findByText('发布时价格不可信，未记录')).toBeInTheDocument()
  })
  it('内容按纯文本显示', async () => {
    wrap(<CommunityTheses t={t} />)
    expect(await screen.findByText('会涨 <b>不是 HTML</b>')).toBeInTheDocument()
  })
  it('没登录时群聊只能看，发言要先签名登录，并说明不是交易', async () => {
    wrap(<CommunityChat t={t} />)
    expect(await screen.findByText('群里还没有人说话。')).toBeInTheDocument()
    expect(screen.getByText('签名只证明钱包归属，不是交易，不花钱。')).toBeInTheDocument()
    expect(messages).toHaveBeenCalled()
  })
})
