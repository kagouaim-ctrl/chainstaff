/**
 * 浏览器直接读公开的链上行情接口，不经过我们的后端：DexScreener、GeckoTerminal 管行情，
 * Solana 的安全检查用 RugCheck 和 Jupiter，BSC 和 Robinhood Chain 用 GoPlus。
 * 都不需要 key，也都允许网页直接调用。每个访客用自己的调用额度。
 * 读不到的值保持 null，界面显示为未知：查不到权限不等于没有权限。
 * 和后端 tradingagents/meme/market.py 的换算一致。
 */
import type { Candle, Chain, Holder, SecurityCheck, Timeframe, Token, Trade, WindowStat } from '@/domain/types'

const DEX = 'https://api.dexscreener.com'
const GECKO = 'https://api.geckoterminal.com/api/v2/networks'
const GOPLUS = 'https://api.gopluslabs.io/api/v1/token_security'
const RUGCHECK = 'https://api.rugcheck.xyz/v1'
const JUPITER = 'https://lite-api.jup.ag/swap/v1'
const WSOL = 'So11111111111111111111111111111111111111112'
const MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/
const EVM = /^0x[0-9a-fA-F]{40}$/
/** DexScreener 和 GeckoTerminal 的网络名正好相同；goplus 是 GoPlus 的链 ID。 */
const CHAINS: Record<Chain, { goplus: string | null }> = { solana: { goplus: null }, bsc: { goplus: '56' }, robinhood: { goplus: '4663' } }
export const CHAIN_LABEL: Record<Chain, string> = { solana: 'Solana', bsc: 'BSC', robinhood: 'Robinhood' }
const isChain = (c: string): c is Chain => c in CHAINS
// EVM 上的「死地址」：打到这里的代币和 LP 就是销毁了
const BURN = new Set(['0x000000000000000000000000000000000000dead', '0x0000000000000000000000000000000000000000'])
const LIST_MAX = 36
/** 每条链最多列多少个，免得 Solana 的推广代币把别的链挤掉。 */
const PER_CHAIN = 12
const LP_LOCKED_MIN_PCT = 80
const SELL_QUOTE_USD = 100
const BONDING_DEXES = new Set(['pumpfun', 'launchlab', 'moonshot', 'boop'])
const WINDOWS: [Timeframe, string][] = [['5m', 'm5'], ['1h', 'h1'], ['6h', 'h6'], ['24h', 'h24']]
const TTL = 30_000

export const isMint = (a: string) => MINT.test(a)
export const isAddress = (a: string) => MINT.test(a) || EVM.test(a)
const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

/* eslint-disable @typescript-eslint/no-explicit-any -- 外部接口的原始 JSON */
type Json = any

async function getJson(url: string, { quiet404 = false } = {}): Promise<Json> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 12_000)
    try {
      const r = await fetch(url, { signal: ctl.signal })
      if (r.status === 429 && attempt === 0) { await new Promise((ok) => setTimeout(ok, 2000)); continue }
      if (quiet404 && (r.status === 404 || r.status === 400)) return null
      if (!r.ok) throw new Error(`${new URL(url).host} 返回 ${r.status}`)
      return await r.json()
    } finally { clearTimeout(timer) }
  }
  throw new Error(`${new URL(url).host} 限流了，请稍后再试`)
}

/** 同一个地址的几个面板（代币、持有者、K 线）共用一次请求。 */
const memo = new Map<string, { at: number; p: Promise<Json> }>()
function cached(key: string, load: () => Promise<Json>): Promise<Json> {
  const hit = memo.get(key)
  if (hit && Date.now() - hit.at < TTL) return hit.p
  const p = load().catch((e) => { memo.delete(key); throw e })
  memo.set(key, { at: Date.now(), p })
  return p
}

const num = (v: unknown): number | null => { const n = Number(v); return v == null || v === '' || !Number.isFinite(n) ? null : n }
const hue = (s: string) => [...s].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7)
const liq = (p: Json) => p?.liquidity?.usd ?? 0
const byDepth = (ps: Json[]) => [...ps].sort((a, b) => liq(b) - liq(a))

function stats(p: Json): Record<Timeframe, WindowStat> {
  const out = {} as Record<Timeframe, WindowStat>
  for (const [label, k] of WINDOWS) {
    const t = p.txns?.[k] ?? {}
    out[label] = { change: p.priceChange?.[k] ?? 0, volume: p.volume?.[k] ?? 0, txns: (t.buys ?? 0) + (t.sells ?? 0), buyers: t.buys ?? 0, sellers: t.sells ?? 0 }
  }
  return out
}

/** RugCheck 报告里的池子和锁仓账户不算持有者。 */
function people(report: Json): Json[] {
  const known = report?.knownAccounts ?? {}
  return (report?.topHolders ?? []).filter((h: Json) => !['AMM', 'LOCKER'].includes(known[h.owner]?.type))
}

function lpLockedPct(report: Json): number | null {
  let weighted = 0, total = 0
  for (const m of report?.markets ?? []) {
    const usd = (m.lp?.quoteUSD ?? 0) + (m.lp?.baseUSD ?? 0)
    if (usd > 0 && m.lp?.lpLockedPct != null) { weighted += m.lp.lpLockedPct * usd; total += usd }
  }
  return total ? weighted / total : null
}

function solanaSecurity(report: Json, sell: Json | undefined): SecurityCheck {
  if (!report) return { mintAuthority: null, freezeAuthority: null, lp: 'unknown', sellSimulation: sell === undefined ? 'unknown' : sell ? 'pass' : 'fail', transferFeeBps: null, top10Pct: null, devHoldPct: null, checkedAt: null }
  const locked = lpLockedPct(report)
  const holders = people(report)
  const supply = num(report.token?.supply)
  const creator = num(report.creatorBalance)
  const fee = num(report.transferFee?.pct)
  return {
    mintAuthority: !!report.mintAuthority,
    freezeAuthority: !!report.freezeAuthority,
    lp: locked == null ? 'unknown' : locked >= LP_LOCKED_MIN_PCT ? 'locked' : 'unlocked',
    sellSimulation: sell === undefined ? 'unknown' : sell ? 'pass' : 'fail',
    transferFeeBps: fee == null ? null : Math.round(fee * 100),
    top10Pct: holders.length ? holders.slice(0, 10).reduce((s, h) => s + (h.pct ?? 0), 0) : null,
    devHoldPct: supply && creator != null ? (creator / supply) * 100 : null,
    checkedAt: new Date().toISOString(),
  }
}

const flag = (v: unknown) => v === '1' ? true : v === '0' ? false : null

/** GoPlus 对 EVM 代币做了买卖模拟和合约检查。查不到的项保持未知。 */
export function evmSecurity(g: Json): SecurityCheck {
  if (!g) return { mintAuthority: null, freezeAuthority: null, lp: 'unknown', sellSimulation: 'unknown', transferFeeBps: null, top10Pct: null, devHoldPct: null, checkedAt: null }
  const pausable = flag(g.transfer_pausable), blacklist = flag(g.is_blacklisted)
  const honeypot = flag(g.is_honeypot), stuck = flag(g.cannot_sell_all)
  const lpHolders: Json[] = g.lp_holders ?? []
  const lpLocked = lpHolders.reduce((s, h) => s + (h.is_locked === 1 || BURN.has(String(h.address).toLowerCase()) ? Number(h.percent) || 0 : 0), 0)
  const taxes = [g.buy_tax, g.sell_tax, g.transfer_tax].map(num).filter((x): x is number => x != null)
  // 合约（池子、锁仓）和死地址不算真人持有者
  const people = (g.holders ?? []).filter((h: Json) => h.is_contract !== 1 && !BURN.has(String(h.address).toLowerCase()))
  const creator = num(g.creator_percent)
  return {
    mintAuthority: flag(g.is_mintable),
    freezeAuthority: pausable === null && blacklist === null ? null : !!(pausable || blacklist),
    lp: lpHolders.length ? (lpLocked >= LP_LOCKED_MIN_PCT / 100 ? 'locked' : 'unlocked') : 'unknown',
    sellSimulation: honeypot || stuck ? 'fail' : honeypot === false ? 'pass' : 'unknown',
    transferFeeBps: taxes.length ? Math.round(Math.max(...taxes) * 10_000) : null,
    top10Pct: g.holders ? people.slice(0, 10).reduce((s: number, h: Json) => s + (Number(h.percent) || 0) * 100, 0) : null,
    devHoldPct: creator == null ? null : creator * 100,
    checkedAt: new Date().toISOString(),
  }
}

function tokenView(chain: Chain, pairs: Json[], report: Json, sell?: Json): Token {
  const top = pairs[0]
  const mint: string = top.baseToken.address
  const launchpad = chain === 'solana' && mint.endsWith('pump') ? 'pump.fun' : chain === 'solana' && mint.endsWith('bonk') ? 'bonk.fun' : (top.dexId ?? '未知')
  const evm = chain !== 'solana'
  return {
    chain, address: mint, symbol: top.baseToken.symbol || mint.slice(0, 6), name: top.baseToken.name ?? '', hue: hue(mint),
    priceUsd: num(top.priceUsd) ?? 0, mcapUsd: top.marketCap ?? top.fdv ?? 0,
    liquidityUsd: pairs.reduce((s, p) => s + liq(p), 0),
    holders: evm ? num(report?.holder_count) : report?.totalHolders ?? null,
    ageMinutes: top.pairCreatedAt ? (Date.now() - top.pairCreatedAt) / 60_000 : 0,
    stats: stats(top), security: evm ? evmSecurity(report) : solanaSecurity(report, sell), verdict: 'unresearched', tags: [],
    graduated: !BONDING_DEXES.has(top.dexId), bondingPct: null, verified: false, launchpad,
    dataSource: `DexScreener、${evm ? 'GoPlus' : 'RugCheck'}（浏览器直接读取）`, updatedAt: new Date().toISOString(),
  }
}

/** 代币的安全报告：Solana 用 RugCheck，EVM 用 GoPlus。失败时是 null（未知），失败不缓存，下次再查。 */
function report(chain: Chain, addr: string): Promise<Json> {
  const id = CHAINS[chain].goplus
  if (!id) return cached(`rug:${addr}`, () => getJson(`${RUGCHECK}/tokens/${addr}/report`, { quiet404: true })).catch(() => null)
  return cached(`goplus:${chain}:${addr}`, async () => {
    const body = await getJson(`${GOPLUS}/${id}?contract_addresses=${addr}`)
    const found = body?.result?.[addr.toLowerCase()]
    if (!found) throw new Error('GoPlus 没有返回结果')
    return found
  }).catch(() => null)
}
const pairsOf = (chain: Chain, addr: string) => cached(`pairs:${chain}:${addr}`, async () =>
  byDepth(((await getJson(`${DEX}/token-pairs/v1/${chain}/${addr}`)) ?? []).filter((p: Json) => same(p.baseToken?.address ?? '', addr))))

/** 列表里见过的代币记住它在哪条链上；EVM 地址在 BSC 和 Robinhood 上都试一下，取流动性大的。 */
const chainOfAddress = new Map<string, Chain>()
async function locate(addr: string): Promise<{ chain: Chain; pairs: Json[] } | null> {
  if (MINT.test(addr)) { const pairs = await pairsOf('solana', addr); return pairs.length ? { chain: 'solana', pairs } : null }
  if (!EVM.test(addr)) return null
  const known = chainOfAddress.get(addr.toLowerCase())
  const tries: Chain[] = known ? [known] : ['bsc', 'robinhood']
  const found = await Promise.all(tries.map(async (c) => ({ chain: c, pairs: await pairsOf(c, addr).catch(() => [] as Json[]) })))
  const best = found.filter((f) => f.pairs.length).sort((x, y) => liq(y.pairs[0]) - liq(x.pairs[0]))[0]
  return best ?? null
}

/** GeckoTerminal 每条链的热门池子，补上 DexScreener 推广榜里很少出现的 BSC 和 Robinhood。 */
async function trending(chain: Chain): Promise<{ chain: Chain; address: string }[]> {
  const body = await getJson(`${GECKO}/${chain}/trending_pools`).catch(() => null)
  return (body?.data ?? []).map((p: Json) => String(p.relationships?.base_token?.data?.id ?? '').replace(`${chain}_`, ''))
    .filter((a: string) => isAddress(a)).map((address: string) => ({ chain, address }))
}

async function listed(path: string): Promise<{ chain: Chain; address: string }[]> {
  const rows: Json[] = (await getJson(`${DEX}${path}`).catch(() => [])) ?? []
  return rows.filter((r) => isChain(r.chainId) && isAddress(r.tokenAddress ?? '')).map((r) => ({ chain: r.chainId as Chain, address: r.tokenAddress }))
}

/** 一次最多几个并发，免得一个页面把 RugCheck 打满。 */
async function pool<T, R>(items: T[], n: number, f: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  let i = 0
  await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; out[k] = await f(items[k]) } }))
  return out
}

/** 发现页：DexScreener 付费推广和最新资料（付费，不代表推荐），加上 BSC 和 Robinhood 在 GeckoTerminal 的热门池子。 */
export async function tokens(): Promise<Token[]> {
  return cached('list', async () => {
    const seen = new Set<string>()
    const sources = await Promise.all([listed('/token-boosts/top/v1'), listed('/token-profiles/latest/v1'), trending('bsc'), trending('robinhood')])
    const count: Record<string, number> = {}
    const picks = sources.flat()
      .filter((p) => { const k = `${p.chain}:${p.address.toLowerCase()}`; return !seen.has(k) && !!seen.add(k) })
      .filter((p) => (count[p.chain] = (count[p.chain] ?? 0) + 1) <= PER_CHAIN)
      .slice(0, LIST_MAX)
    if (!picks.length) throw new Error('DexScreener 暂时没有返回代币')
    const out: Token[] = []
    for (const chain of Object.keys(CHAINS) as Chain[]) {
      const addrs = picks.filter((p) => p.chain === chain).map((p) => p.address)
      if (!addrs.length) continue
      const pairs: Json[] = (await getJson(`${DEX}/tokens/v1/${chain}/${addrs.slice(0, 30).join(',')}`).catch(() => [])) ?? []
      const by = new Map<string, Json[]>()
      for (const p of pairs) {
        const a = addrs.find((x) => same(x, p.baseToken?.address ?? ''))
        if (a) by.set(a, [...(by.get(a) ?? []), p])
      }
      const found = addrs.filter((a) => by.has(a))
      // GoPlus 免费档一次只查一个地址、频率很低，所以 EVM 代币的安全检查留到打开代币页时再查
      const reports = chain === 'solana' ? await pool(found, 4, (a) => report(chain, a)) : found.map(() => null)
      found.forEach((a, i) => { chainOfAddress.set(a.toLowerCase(), chain); out.push(tokenView(chain, byDepth(by.get(a)!), reports[i])) })
    }
    // 保持 DexScreener 的顺序
    const order = new Map(picks.map((p, i) => [`${p.chain}:${p.address.toLowerCase()}`, i]))
    return out.sort((x, y) => (order.get(`${x.chain}:${x.address.toLowerCase()}`) ?? 0) - (order.get(`${y.chain}:${y.address.toLowerCase()}`) ?? 0))
  })
}

async function sellRoute(mint: string, priceUsd: number | null, decimals: number | null): Promise<boolean | undefined> {
  if (!priceUsd || decimals == null) return undefined
  const amount = Math.floor((SELL_QUOTE_USD / priceUsd) * 10 ** decimals)
  if (!Number.isSafeInteger(amount) || amount <= 0) return undefined
  const quote = await getJson(`${JUPITER}/quote?inputMint=${mint}&outputMint=${WSOL}&amount=${amount}&slippageBps=500`, { quiet404: true }).catch(() => undefined)
  return quote === undefined ? undefined : !!quote
}

export async function token(addr: string): Promise<Token | null> {
  const at = await locate(addr)
  if (!at) return null
  const rep = await report(at.chain, addr)
  const sell = at.chain === 'solana'
    ? await cached(`sell:${addr}`, () => sellRoute(addr, num(at.pairs[0].priceUsd), num(rep?.token?.decimals)))
    : undefined
  return tokenView(at.chain, at.pairs, rep, sell)
}

async function topPool(addr: string): Promise<{ chain: Chain; pool: string } | null> {
  const at = await locate(addr)
  return at?.pairs[0]?.pairAddress ? { chain: at.chain, pool: at.pairs[0].pairAddress } : null
}

export async function candles(mint: string): Promise<Candle[]> {
  const p = await topPool(mint)
  if (!p) return []
  const body = await cached(`ohlcv:${p.pool}`, () => getJson(`${GECKO}/${p.chain}/pools/${p.pool}/ohlcv/minute?aggregate=5&limit=288&currency=usd`))
  const rows: number[][] = body?.data?.attributes?.ohlcv_list ?? []
  return rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume })).reverse()
}

export async function trades(mint: string): Promise<Trade[]> {
  const p = await topPool(mint)
  if (!p) return []
  const body = await cached(`trades:${p.pool}`, () => getJson(`${GECKO}/${p.chain}/pools/${p.pool}/trades`))
  return (body?.data ?? []).map((row: Json, i: number) => {
    const t = row.attributes ?? {}
    const w: string = t.tx_from_address ?? ''
    const buy = t.kind === 'buy'
    return {
      id: `${t.tx_hash ?? ''}-${i}`, at: t.block_timestamp, side: buy ? 'BUY' : 'SELL',
      who: w ? `${w.slice(0, 4)}…${w.slice(-4)}` : '未知', kind: 'wallet',
      usd: num(t.volume_in_usd) ?? 0, price: num(buy ? t.price_to_in_usd : t.price_from_in_usd) ?? 0,
    }
  })
}

/** 最大的持有者，不含池子、锁仓和死地址。没人公开成本，所以成本和盈亏是 null。 */
export async function holders(addr: string): Promise<Holder[]> {
  const at = await locate(addr)
  if (!at) return []
  const rep = await report(at.chain, addr)
  const price = num(at.pairs[0]?.priceUsd) ?? 0
  const label = (o: string) => `${o.slice(0, 4)}…${o.slice(-4)}`
  if (at.chain !== 'solana') {
    return (rep?.holders ?? []).filter((h: Json) => h.is_contract !== 1 && !BURN.has(String(h.address).toLowerCase())).map((h: Json, i: number) => ({
      rank: i + 1, label: label(String(h.address)), kind: 'wallet' as const,
      amountUsd: (num(h.balance) ?? 0) * price, pct: (Number(h.percent) || 0) * 100, avgCost: null, pnlPct: null, heldMinutes: null,
    }))
  }
  return people(rep).map((h, i) => ({
    rank: i + 1, label: label(h.owner ?? ''), kind: 'wallet',
    amountUsd: (h.uiAmount ?? 0) * price, pct: h.pct ?? 0, avgCost: null, pnlPct: null, heldMinutes: null,
  }))
}
