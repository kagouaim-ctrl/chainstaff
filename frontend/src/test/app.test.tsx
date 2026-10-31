import { screen, within, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderRoute } from './render'
import { TOKENS, ORDERS } from '@/api/demo'
import { useApp } from '@/app/store'
import { searchAll } from '@/app/Search'
import { filterTokens } from '@/pages/Discover'
import { filterEvents } from '@/pages/Activity'
import { AGENTS, EVENTS } from '@/api/demo'

vi.mock('@/features/charts', () => ({ CandleChart: () => <div>chart</div>, EquityChart: () => <div>equity</div> }))

beforeEach(() => useApp.setState({ paused: false, pausedAt: null, searchOpen: false, watch: {} }))

const PAGES: [string, string][] = [
  ['/', 'Agent 流水线'], ['/discover', '发现'], ['/agents', '角色'], ['/activity', '实时动态'],
  ['/portfolio', '当前持仓'], ['/orders', '交易记录'], ['/strategy', '风控参数'], [`/token/${TOKENS[4].address}`, '手动交易'],
]

describe('页面', () => {
  it.each(PAGES)('%s 可访问，并显示演示标识', async (path, heading) => {
    renderRoute(path)
    expect((await screen.findAllByText(heading)).length).toBeGreaterThan(0)
    // 铁律 8：演示数据必须明确标注
    expect(screen.getAllByText('演示').length).toBeGreaterThan(0)
    expect(screen.getByText('演示数据')).toBeInTheDocument()
  })

  it('全局常驻：模拟盘徽标和紧急暂停', async () => {
    renderRoute('/orders')
    expect(screen.getByRole('button', { name: '当前模式：模拟盘' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '紧急暂停' })).toBeInTheDocument()
  })

  it('没有左侧边栏，一级入口都在顶部导航', () => {
    renderRoute('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    for (const l of ['总览', '发现', 'Agents', '实时动态', '持仓', '交易记录', '策略']) expect(within(nav).getByText(l)).toBeInTheDocument()
  })
})

describe('紧急暂停', () => {
  it('需要确认，确认后显示横幅并说明已提交交易仍在追踪', async () => {
    const u = userEvent.setup()
    renderRoute('/')
    await u.click(screen.getByRole('button', { name: '紧急暂停' }))
    const dlg = screen.getByRole('dialog', { name: '确认紧急暂停？' })
    expect(within(dlg).getByText(/后端未接入/)).toBeInTheDocument()
    await u.click(within(dlg).getByRole('button', { name: '确认暂停' }))
    expect(useApp.getState().paused).toBe(true)
    expect(await screen.findByText(/仍在追踪确认/)).toBeInTheDocument()
  })
})

describe('抽屉可以通过 URL 直接访问', () => {
  it('交易详情', async () => {
    renderRoute('/orders?order=ord_7f3a')
    const d = await screen.findByRole('dialog')
    expect(await within(d).findByText('决策链')).toBeInTheDocument()
    expect(within(d).getByText('风控审批')).toBeInTheDocument()
    expect(within(d).getByText('平台手续费（含 AI 费用）')).toBeInTheDocument()
  })
  it('未确认的订单不显示为成交', async () => {
    renderRoute('/orders?order=ord_7f2c')
    const d = await screen.findByRole('dialog')
    expect(await within(d).findByText(/提交不等于成交/)).toBeInTheDocument()
    expect(within(d).getByText('未成交')).toBeInTheDocument()
  })
  it('Agent 详情：风控禁止使用模型', async () => {
    renderRoute('/agents?agent=risk')
    const d = await screen.findByRole('dialog')
    expect(await within(d).findByText('禁止使用模型')).toBeInTheDocument()
  })
})

describe('代币群聊', () => {
  it('持有者可以发言；后端未接入时不假装发送成功', async () => {
    renderRoute(`/token/${TOKENS[4].address}`)
    expect(await screen.findByPlaceholderText(/在 ZAPP 群里发言/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('发言'), { target: { value: '你好' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText(/消息没有发送/)).toBeInTheDocument()
  })
  it('没持有过的人只能看', async () => {
    renderRoute(`/token/${TOKENS[22].address}`)
    expect(await screen.findByText(/只能看，不能发言/)).toBeInTheDocument()
    expect(screen.queryByLabelText('发言')).not.toBeInTheDocument()
  })
  it('群规则写明群聊不影响交易', async () => {
    renderRoute(`/token/${TOKENS[4].address}`)
    expect(await screen.findByText(/群聊内容不会影响任何交易/)).toBeInTheDocument()
  })
})

describe('策略页风控修改', () => {
  it('放宽需要二次确认，并且不假装保存成功', async () => {
    const u = userEvent.setup()
    renderRoute('/strategy')
    const input = await screen.findByLabelText('单笔最大金额')
    fireEvent.change(input, { target: { value: '3' } })
    expect(screen.getByText('放宽')).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: '保存' }))
    await u.click(within(screen.getByRole('dialog')).getByRole('button', { name: '继续' }))
    expect(screen.getByRole('dialog', { name: '再次确认：你正在放宽风控' })).toBeInTheDocument()
    await u.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认保存' }))
    await waitFor(() => expect(screen.getByText(/参数没有保存/)).toBeInTheDocument())
  })
  it('实盘锁定', async () => {
    renderRoute('/strategy')
    expect(await screen.findByText('实盘（锁定）')).toBeInTheDocument()
  })
})

describe('筛选与搜索', () => {
  it('发现页：安全筛选只留下通过的代币；已买入标签和持仓一致', () => {
    const base = { tab: 'hot' as const, tf: '1h' as const, cat: '全部', safeOnly: true, watch: {}, sort: 'mcap' as const, dir: 1 as const }
    const safe = filterTokens(TOKENS, base)
    expect(safe.every((t) => !t.security.mintAuthority && t.security.lp !== 'unlocked' && t.security.sellSimulation === 'pass')).toBe(true)
    const bought = filterTokens(TOKENS, { ...base, safeOnly: false, tab: 'bought' })
    expect(bought.map((t) => t.symbol).sort()).toEqual(['GOOBY', 'MOCHI', 'NOODL', 'ZAPP'])
    const sorted = filterTokens(TOKENS, { ...base, safeOnly: false })
    for (let i = 1; i < sorted.length; i++) expect(sorted[i - 1].mcapUsd).toBeGreaterThanOrEqual(sorted[i].mcapUsd)
  })
  it('搜索支持代币符号、合约地址、Agent、订单 ID', () => {
    expect(searchAll('zapp', TOKENS, AGENTS, ORDERS)[0]).toMatchObject({ kind: 'token', title: 'ZAPP' })
    expect(searchAll(TOKENS[4].address, TOKENS, AGENTS, ORDERS)[0]).toMatchObject({ kind: 'token', title: 'ZAPP' })
    expect(searchAll('风控', TOKENS, AGENTS, ORDERS)[0]).toMatchObject({ kind: 'agent' })
    expect(searchAll('ord_7f3a', TOKENS, AGENTS, ORDERS)[0]).toMatchObject({ kind: 'order' })
    expect(searchAll('', TOKENS, AGENTS, ORDERS)).toEqual([])
  })
  it('实时动态按 Agent 和状态筛选', () => {
    const r = filterEvents(EVENTS, { agent: 'risk', type: 'all', status: 'error', token: '' })
    expect(r.length).toBeGreaterThan(0)
    expect(r.every((e) => e.agent === 'risk' && e.status === 'error')).toBe(true)
  })
})
