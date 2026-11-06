/**
 * 浏览器直接读公开的链上行情接口，不经过我们的后端：DexScreener、GeckoTerminal 管行情，
 * Solana 的安全检查用 RugCheck 和 Jupiter，BSC 和 Robinhood Chain、ETH 用 GoPlus。
 * 都不需要 key，也都允许网页直接调用。每个访客用自己的调用额度。
 * 读不到的值保持 null，界面显示为未知：查不到权限不等于没有权限。
 * 和后端 tradingagents/meme/market.py 的换算一致。
 */
import type { Candle, Chain, ChartTf, Holder, SecurityCheck, Timeframe, Token, TokenLink, Trade, WindowStat } from '@/domain/types'

const DEX = 'https://api.dexscreener.com'
const GECKO = 'https://api.geckoterminal.com/api/v2/networks'
const GOPLUS = 'https://api.gopluslabs.io/api/v1/token_security'
const RUGCHECK = 'https://api.rugcheck.xyz/v1'
const JUPITER = 'https://lite-api.jup.ag/swap/v1'
const WSOL = 'So11111111111111111111111111111111111111112'
const MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/
const EVM = /^0x[0-9a-fA-F]{40}$/
/** 链的键用 DexScreener 的网络名；gecko 是 GeckoTerminal 的网络名，goplus 是 GoPlus 的链 ID。 */
const CHAINS: Record<Chain, { gecko: string; goplus: string | null }> = {
  solana: { gecko: 'solana', goplus: null },
  bsc: { gecko: 'bsc', goplus: '56' },
  robinhood: { gecko: 'robinhood', goplus: '4663' },
  ethereum: { gecko: 'eth', goplus: '1' },
}
export const CHAIN_LABEL: Record<Chain, string> = { solana: 'Solana', bsc: 'BSC', robinhood: 'Robinhood', ethereum: 'ETH' }
const EVM_CHAINS: Chain[] = ['bsc', 'robinhood', 'ethereum']
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
      // 限流时有的接口不带跨域头，浏览器只报「Failed to fetch」，所以网络错误也等一下再试一次
      const r = await fetch(url, { signal: ctl.signal }).catch(async (e) => {
        if (attempt === 0 && !ctl.signal.aborted) { await new Promise((ok) => setTimeout(ok, 2000)); return null }
        throw e
      })
      if (!r) continue
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
function cached(key: string, load: () => Promise<Json>, ttl = TTL): Promise<Json> {
  const hit = memo.get(key)
  if (hit && Date.now() - hit.at < ttl) return hit.p
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

const https = (u: unknown): string | null => typeof u === 'string' && /^https:\/\//i.test(u) ? u : null
const SOCIAL: Record<string, TokenLink['kind']> = { twitter: 'x', x: 'x', telegram: 'telegram', discord: 'discord' }
const LINK_LABEL: Record<TokenLink['kind'], string> = { website: '官网', x: 'X', telegram: 'Telegram', discord: 'Discord', other: '链接' }

/** DexScreener 里项目方登记的官网和社交账号。只收 https 链接。 */
function links(info: Json): TokenLink[] {
  const out: TokenLink[] = []
  for (const w of info?.websites ?? []) { const url = https(w.url); if (url) out.push({ kind: 'website', label: LINK_LABEL.website, url }) }
  for (const s of info?.socials ?? []) {
    const url = https(s.url)
    const kind = SOCIAL[String(s.type).toLowerCase()] ?? 'other'
    if (url) out.push({ kind, label: kind === 'other' ? String(s.type || LINK_LABEL.other) : LINK_LABEL[kind], url })
  }
  return out
}

/** 简介只在 DexScreener 的代币资料里有，列表加载时记下来。 */
const descriptions = new Map<string, string>()

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
    image: https(pairs.find((p) => p.info?.imageUrl)?.info?.imageUrl),
    description: descriptions.get(`${chain}:${mint.toLowerCase()}`) ?? null,
    links: links(pairs.find((p) => p.info?.websites?.length || p.info?.socials?.length)?.info),
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
  const tries: Chain[] = known ? [known] : EVM_CHAINS
  const found = await Promise.all(tries.map(async (c) => ({ chain: c, pairs: await pairsOf(c, addr).catch(() => [] as Json[]) })))
  const best = found.filter((f) => f.pairs.length).sort((x, y) => liq(y.pairs[0]) - liq(x.pairs[0]))[0]
  return best ?? null
}

/** GeckoTerminal 每条链的热门池子，补上 DexScreener 推广榜里很少出现的 BSC 和 Robinhood。 */
async function trending(chain: Chain): Promise<{ chain: Chain; address: string }[]> {
  const body = await getJson(`${GECKO}/${CHAINS[chain].gecko}/trending_pools`).catch(() => null)
  return (body?.data ?? []).map((p: Json) => String(p.relationships?.base_token?.data?.id ?? '').replace(`${CHAINS[chain].gecko}_`, ''))
    .filter((a: string) => isAddress(a)).map((address: string) => ({ chain, address }))
}

async function listed(path: string): Promise<{ chain: Chain; address: string }[]> {
  const rows: Json[] = (await getJson(`${DEX}${path}`).catch(() => [])) ?? []
  const ok = rows.filter((r) => isChain(r.chainId) && isAddress(r.tokenAddress ?? ''))
  for (const r of ok) if (typeof r.description === 'string' && r.description.trim()) descriptions.set(`${r.chainId}:${r.tokenAddress.toLowerCase()}`, r.description.trim().slice(0, 600))
  return ok.map((r) => ({ chain: r.chainId as Chain, address: r.tokenAddress }))
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
    const sources = await Promise.all([listed('/token-boosts/top/v1'), listed('/token-profiles/latest/v1'), ...EVM_CHAINS.map(trending)])
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

/** K 线和成交用 24 小时成交额最大的池子：流动性数字有时是假的，成交很难造假。 */
async function topPool(addr: string): Promise<{ chain: Chain; pool: string } | null> {
  const at = await locate(addr)
  const busiest = [...(at?.pairs ?? [])].sort((x, y) => (y.volume?.h24 ?? 0) - (x.volume?.h24 ?? 0))[0]
  return busiest?.pairAddress ? { chain: at!.chain, pool: busiest.pairAddress } : null
}

const GECKO_TF: Record<Exclude<ChartTf, SecondTf>, [string, number]> = { '1m': ['minute', 1], '5m': ['minute', 5], '15m': ['minute', 15], '1h': ['hour', 1], '4h': ['hour', 4], '1d': ['day', 1] }

type SecondTf = '1s' | '5s' | '15s'
const SECONDS: Record<SecondTf, number> = { '1s': 1, '5s': 5, '15s': 15 }
const isSecondTf = (tf: ChartTf): tf is SecondTf => tf in SECONDS

/**
 * 秒级 K 线：GeckoTerminal 免费接口没有秒级 K 线，只有最近 300 笔成交，所以在浏览器里用成交合成。
 * 每次拉到的成交都留下来，页面开着越久，能看到的秒级历史越长。
 */
const tradeLog = new Map<string, Map<string, { t: number; price: number; usd: number }>>()
async function secondCandles(addr: string, step: number): Promise<Candle[]> {
  const p = await topPool(addr)
  if (!p) return []
  const key = `${p.chain}:${p.pool}`
  const log = tradeLog.get(key) ?? new Map()
  tradeLog.set(key, log)
  try {
    const body = await cached(`live-trades:${key}`, () => getJson(`${GECKO}/${CHAINS[p.chain].gecko}/pools/${p.pool}/trades`), 2_500)
    for (const row of body?.data ?? []) {
      const a = row.attributes ?? {}
      const buyToken = same(a.to_token_address ?? '', addr)
      const price = num(buyToken ? a.price_to_in_usd : a.price_from_in_usd)
      const t = Date.parse(a.block_timestamp)
      if (price && Number.isFinite(t)) log.set(String(row.id), { t: Math.floor(t / 1000), price, usd: num(a.volume_in_usd) ?? 0 })
    }
  } catch (e) {
    // 被限流时先用已经攒下的成交，一笔都没有才报错
    if (!log.size) throw e
  }
  const bars = new Map<number, Candle>()
  for (const x of [...log.values()].sort((a, b) => a.t - b.t)) {
    const time = Math.floor(x.t / step) * step
    const b = bars.get(time)
    if (!b) bars.set(time, { time, open: x.price, high: x.price, low: x.price, close: x.price, volume: x.usd })
    else { b.high = Math.max(b.high, x.price); b.low = Math.min(b.low, x.price); b.close = x.price; b.volume += x.usd }
  }
  const out = [...bars.values()].sort((a, b) => a.time - b.time)
  // 和交易所一样，开盘价接上一根的收盘价，K 线才连续
  for (let i = 1; i < out.length; i++) {
    out[i].open = out[i - 1].close
    out[i].high = Math.max(out[i].high, out[i].open)
    out[i].low = Math.min(out[i].low, out[i].open)
  }
  return out
}

export async function candles(mint: string, tf: ChartTf = '5m'): Promise<Candle[]> {
  if (isSecondTf(tf)) return secondCandles(mint, SECONDS[tf])
  const p = await topPool(mint)
  if (!p) return []
  const [unit, agg] = GECKO_TF[tf]
  const body = await cached(`ohlcv:${p.pool}:${tf}`, () => getJson(`${GECKO}/${CHAINS[p.chain].gecko}/pools/${p.pool}/ohlcv/${unit}?aggregate=${agg}&limit=300&currency=usd&token=${mint}`))
  const rows: number[][] = body?.data?.attributes?.ohlcv_list ?? []
  // 按时间升序、去掉重复和不完整的 K 线：图表库遇到这些会直接报错
  const seen = new Set<number>()
  return rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }))
    .filter((c) => [c.time, c.open, c.high, c.low, c.close].every((v) => Number.isFinite(v)) && !seen.has(c.time) && !!seen.add(c.time))
    .sort((a, b) => a.time - b.time)
}

export async function trades(mint: string): Promise<Trade[]> {
  const p = await topPool(mint)
  if (!p) return []
  const body = await cached(`trades:${p.pool}`, () => getJson(`${GECKO}/${CHAINS[p.chain].gecko}/pools/${p.pool}/trades`))
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

export interface SearchHit { chain: Chain; address: string; symbol: string; name: string; liquidityUsd: number; volumeH24: number; priceUsd: number | null }

/**
 * 在 DexScreener 上按名称、符号或地址搜三条链的代币。
 * 按 24 小时总成交额排序：骗子盘常常报出离谱的流动性，但很难有真实成交。
 */
export async function search(q: string): Promise<SearchHit[]> {
  const term = q.trim()
  if (term.length < 2) return []
  const body = await getJson(`${DEX}/latest/dex/search?q=${encodeURIComponent(term)}`)
  const by = new Map<string, { p: Json; volume: number }>()
  for (const p of body?.pairs ?? []) {
    const a = p.baseToken?.address
    if (!isChain(p.chainId) || !a || !isAddress(a)) continue
    const k = `${p.chainId}:${a.toLowerCase()}`
    const cur = by.get(k)
    const volume = (cur?.volume ?? 0) + (p.volume?.h24 ?? 0)
    by.set(k, { p: !cur || liq(p) > liq(cur.p) ? p : cur.p, volume })
  }
  return [...by.values()].sort((x, y) => y.volume - x.volume).slice(0, 15).map(({ p, volume }) => {
    chainOfAddress.set(p.baseToken.address.toLowerCase(), p.chainId)
    return { chain: p.chainId, address: p.baseToken.address, symbol: p.baseToken.symbol ?? '', name: p.baseToken.name ?? '', liquidityUsd: liq(p), volumeH24: volume, priceUsd: num(p.priceUsd) }
  })
}
