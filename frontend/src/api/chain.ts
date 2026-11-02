/**
 * 浏览器直接读公开的链上行情接口，不经过我们的后端：DexScreener、GeckoTerminal、RugCheck、Jupiter。
 * 都不需要 key，也都允许网页直接调用。每个访客用自己的调用额度。
 * 读不到的值保持 null，界面显示为未知：查不到权限不等于没有权限。
 * 和后端 tradingagents/meme/market.py 的换算一致。
 */
import type { Candle, Holder, SecurityCheck, Timeframe, Token, Trade, WindowStat } from '@/domain/types'

const DEX = 'https://api.dexscreener.com'
const GECKO = 'https://api.geckoterminal.com/api/v2/networks/solana'
const RUGCHECK = 'https://api.rugcheck.xyz/v1'
const JUPITER = 'https://lite-api.jup.ag/swap/v1'
const WSOL = 'So11111111111111111111111111111111111111112'
const MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/
const LIST_MAX = 30
const LP_LOCKED_MIN_PCT = 80
const SELL_QUOTE_USD = 100
const BONDING_DEXES = new Set(['pumpfun', 'launchlab', 'moonshot', 'boop'])
const WINDOWS: [Timeframe, string][] = [['5m', 'm5'], ['1h', 'h1'], ['6h', 'h6'], ['24h', 'h24']]
const TTL = 30_000

export const isMint = (a: string) => MINT.test(a)

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

function security(report: Json, sell: Json | undefined): SecurityCheck {
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

function tokenView(pairs: Json[], report: Json, sell?: Json): Token {
  const top = pairs[0]
  const mint: string = top.baseToken.address
  const launchpad = mint.endsWith('pump') ? 'pump.fun' : mint.endsWith('bonk') ? 'bonk.fun' : (top.dexId ?? '未知')
  return {
    chain: 'solana', address: mint, symbol: top.baseToken.symbol || mint.slice(0, 6), name: top.baseToken.name ?? '', hue: hue(mint),
    priceUsd: num(top.priceUsd) ?? 0, mcapUsd: top.marketCap ?? top.fdv ?? 0,
    liquidityUsd: pairs.reduce((s, p) => s + liq(p), 0),
    holders: report?.totalHolders ?? null,
    ageMinutes: top.pairCreatedAt ? (Date.now() - top.pairCreatedAt) / 60_000 : 0,
    stats: stats(top), security: security(report, sell), verdict: 'unresearched', tags: [],
    graduated: !BONDING_DEXES.has(top.dexId), bondingPct: null, verified: false, launchpad,
    dataSource: 'DexScreener、RugCheck（浏览器直接读取）', updatedAt: new Date().toISOString(),
  }
}

const report = (mint: string) => cached(`rug:${mint}`, () => getJson(`${RUGCHECK}/tokens/${mint}/report`, { quiet404: true }).catch(() => null))
const pairsOf = (mint: string) => cached(`pairs:${mint}`, async () =>
  byDepth(((await getJson(`${DEX}/token-pairs/v1/solana/${mint}`)) ?? []).filter((p: Json) => p.baseToken?.address === mint)))

async function solanaMints(path: string): Promise<string[]> {
  const rows: Json[] = (await getJson(`${DEX}${path}`).catch(() => [])) ?? []
  return rows.filter((r) => r.chainId === 'solana' && isMint(r.tokenAddress ?? '')).map((r) => r.tokenAddress)
}

/** 一次最多几个并发，免得一个页面把 RugCheck 打满。 */
async function pool<T, R>(items: T[], n: number, f: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  let i = 0
  await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; out[k] = await f(items[k]) } }))
  return out
}

/** 发现页：付费推广最多的代币，加上最新提交资料的代币。两者都是付费的，不代表推荐。 */
export async function tokens(): Promise<Token[]> {
  return cached('list', async () => {
    const mints = [...new Set([...(await solanaMints('/token-boosts/top/v1')), ...(await solanaMints('/token-profiles/latest/v1'))])].slice(0, LIST_MAX)
    if (!mints.length) throw new Error('DexScreener 暂时没有返回代币')
    const pairs: Json[] = (await getJson(`${DEX}/tokens/v1/solana/${mints.join(',')}`)) ?? []
    const by = new Map<string, Json[]>()
    for (const p of pairs) { const a = p.baseToken?.address; if (mints.includes(a)) by.set(a, [...(by.get(a) ?? []), p]) }
    const listed = mints.filter((m) => by.has(m))
    const reports = await pool(listed, 4, report)
    return listed.map((m, i) => tokenView(byDepth(by.get(m)!), reports[i]))
  })
}

async function sellRoute(mint: string, priceUsd: number | null, decimals: number | null): Promise<boolean | undefined> {
  if (!priceUsd || decimals == null) return undefined
  const amount = Math.floor((SELL_QUOTE_USD / priceUsd) * 10 ** decimals)
  if (!Number.isSafeInteger(amount) || amount <= 0) return undefined
  const quote = await getJson(`${JUPITER}/quote?inputMint=${mint}&outputMint=${WSOL}&amount=${amount}&slippageBps=500`, { quiet404: true }).catch(() => undefined)
  return quote === undefined ? undefined : !!quote
}

export async function token(mint: string): Promise<Token | null> {
  if (!isMint(mint)) return null
  const pairs = await pairsOf(mint)
  if (!pairs.length) return null
  const rep = await report(mint)
  const sell = await cached(`sell:${mint}`, () => sellRoute(mint, num(pairs[0].priceUsd), num(rep?.token?.decimals)))
  return tokenView(pairs, rep, sell)
}

async function topPool(mint: string): Promise<string | null> {
  if (!isMint(mint)) return null
  return (await pairsOf(mint))[0]?.pairAddress ?? null
}

export async function candles(mint: string): Promise<Candle[]> {
  const p = await topPool(mint)
  if (!p) return []
  const body = await cached(`ohlcv:${p}`, () => getJson(`${GECKO}/pools/${p}/ohlcv/minute?aggregate=5&limit=288&currency=usd`))
  const rows: number[][] = body?.data?.attributes?.ohlcv_list ?? []
  return rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume })).reverse()
}

export async function trades(mint: string): Promise<Trade[]> {
  const p = await topPool(mint)
  if (!p) return []
  const body = await cached(`trades:${p}`, () => getJson(`${GECKO}/pools/${p}/trades`))
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

/** 最大的持有者，不含池子。没人公开成本，所以成本和盈亏是 null。 */
export async function holders(mint: string): Promise<Holder[]> {
  if (!isMint(mint)) return []
  const [rep, pairs] = await Promise.all([report(mint), pairsOf(mint)])
  const price = num(pairs[0]?.priceUsd) ?? 0
  return people(rep).map((h, i) => ({
    rank: i + 1, label: `${(h.owner ?? '').slice(0, 4)}…${(h.owner ?? '').slice(-4)}`, kind: 'wallet',
    amountUsd: (h.uiAmount ?? 0) * price, pct: h.pct ?? 0, avgCost: null, pnlPct: null, heldMinutes: null,
  }))
}
