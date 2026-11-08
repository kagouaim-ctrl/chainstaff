/**
 * 新币发现：刚创建、快毕业（内盘进度高）、刚毕业。
 *
 * 数据都来自公开接口，浏览器直接读：
 * - GeckoTerminal 的 new_pools（每条链最新创建的池子）和按 DEX 列池子；
 * - Solana 上 pump.fun 的内盘进度从链上读：GeckoTerminal 给的 pump.fun「池子地址」就是发射曲线账户，
 *   用 publicnode 的 getMultipleAccounts 读出剩余可卖代币，算出进度，不靠估算。
 * 其他发射平台（four.meme、Meteora DBC、LaunchLab 等）读不到进度，进度显示为未知，不编。
 *
 * 「刚毕业」是按迁移目标判断的：pump.fun 代币在 PumpSwap 上 24 小时内新建的池子、
 * bonk.fun 代币在 Raydium 上 24 小时内新建的池子、four.meme 代币在 PancakeSwap 上 24 小时内新建的池子。
 * 有人在毕业前手动建池会被误算，所以页面上写明了判断依据。
 */
import type { Token } from '@/domain/types'
import { BONDING_DEXES, cached, chainOfAddress, getJson, tokenView } from './chain'

const GECKO = 'https://api.geckoterminal.com/api/v2/networks'
const SOLANA_RPC = 'https://solana-rpc.publicnode.com'
const PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
/** pump.fun 发射曲线一开始可卖的代币数量（含 6 位小数），卖完就毕业 */
export const PUMP_INITIAL_REAL_TOKENS = 793_100_000_000_000n
const DAY_MS = 24 * 60 * 60 * 1000
const TTL = 25_000

export type LaunchChain = 'solana' | 'bsc'
export const LAUNCH_CHAINS: LaunchChain[] = ['solana', 'bsc']
const NET: Record<LaunchChain, string> = { solana: 'solana', bsc: 'bsc' }

/** GeckoTerminal 的 DEX 名到发射平台名 */
const LAUNCHPAD: Record<string, string> = {
  'pump-fun': 'pump.fun', 'raydium-launchlab': 'LaunchLab', 'letsbonk-fun': 'bonk.fun', 'meteora-dbc': 'Meteora DBC',
  'boop-fun': 'boop', moonshot: 'Moonshot', 'four-meme': 'four.meme',
}

export interface Launches {
  /** 刚创建、还在内盘上 */
  fresh: Token[]
  /** 内盘进度最高、还没毕业。BSC 读不到进度，按 24 小时成交排 */
  soon: Token[]
  /** 24 小时内迁移到外盘 */
  graduated: Token[]
  /** 这条链的「快毕业」是否有真实进度 */
  hasProgress: boolean
  updatedAt: string
  /** 有几页没读到（被限流等），列表可能不全 */
  partial?: boolean
}

/* eslint-disable @typescript-eslint/no-explicit-any -- 外部接口的原始 JSON */
type Json = any

/** GeckoTerminal 的池子转成 DexScreener 的交易对格式，好复用同一套选池和数据校验。 */
export function geckoPair(chain: LaunchChain, pool: Json, tokens: Map<string, Json>): Json | null {
  const a = pool?.attributes
  const baseId = pool?.relationships?.base_token?.data?.id
  const quoteId = pool?.relationships?.quote_token?.data?.id
  const dex = pool?.relationships?.dex?.data?.id
  if (!a || !baseId) return null
  const base = tokens.get(baseId)?.attributes
  const address = String(baseId).replace(`${NET[chain]}_`, '')
  const [nameBase, nameQuote] = String(a.name ?? '').split(' / ')
  const t = a.transactions ?? {}
  const tx = (k: string) => ({ buys: t[k]?.buys ?? 0, sells: t[k]?.sells ?? 0 })
  const pc = a.price_change_percentage ?? {}
  const v = a.volume_usd ?? {}
  const created = Date.parse(a.pool_created_at ?? '')
  return {
    chainId: chain, dexId: dex ?? 'unknown', pairAddress: a.address,
    baseToken: { address, symbol: base?.symbol ?? nameBase ?? '', name: base?.name ?? '' },
    quoteToken: { symbol: tokens.get(quoteId)?.attributes?.symbol ?? nameQuote ?? '' },
    priceUsd: a.base_token_price_usd,
    priceChange: { m5: num(pc.m5), h1: num(pc.h1), h6: num(pc.h6), h24: num(pc.h24) },
    volume: { m5: num(v.m5), h1: num(v.h1), h6: num(v.h6), h24: num(v.h24) },
    txns: { m5: tx('m5'), h1: tx('h1'), h6: tx('h6'), h24: tx('h24') },
    liquidity: { usd: num(a.reserve_in_usd) },
    fdv: opt(a.fdv_usd), marketCap: opt(a.market_cap_usd),
    pairCreatedAt: Number.isFinite(created) ? created : undefined,
    info: base?.image_url && /^https:\/\//.test(base.image_url) ? { imageUrl: base.image_url } : undefined,
  }
}

const num = (x: unknown): number => { const n = Number(x); return Number.isFinite(n) ? n : 0 }
const opt = (x: unknown): number | null => x == null || x === '' ? null : Number.isFinite(Number(x)) ? Number(x) : null

/** pump.fun 发射曲线账户的进度（0–100）和是否已完成。读不到的地址不在结果里。 */
export function pumpProgress(data: Uint8Array): { pct: number; complete: boolean } | null {
  // 8 字节账户标识，然后是 5 个 u64：虚拟代币、虚拟报价、剩余可卖代币、已收报价、总供应；再一个 bool 表示已完成
  if (data.length < 49) return null
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength)
  const realTokens = view.getBigUint64(24, true)
  const complete = data[48] === 1
  if (complete) return { pct: 100, complete }
  if (realTokens > PUMP_INITIAL_REAL_TOKENS) return null
  const left = Number((realTokens * 10_000n) / PUMP_INITIAL_REAL_TOKENS) / 100
  return { pct: Math.round((100 - left) * 10) / 10, complete }
}

function b64(s: string): Uint8Array {
  const bin = atob(s)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

/** publicnode 免费节点一次最多查 10 个账户 */
const RPC_BATCH = 10

async function readPumpCurves(addrs: string[]): Promise<Map<string, { pct: number; complete: boolean }>> {
  const out = new Map<string, { pct: number; complete: boolean }>()
  const chunks: string[][] = []
  for (let i = 0; i < addrs.length; i += RPC_BATCH) chunks.push(addrs.slice(i, i + RPC_BATCH))
  await Promise.all(chunks.map(async (chunk) => {
    const r = await fetch(SOLANA_RPC, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getMultipleAccounts', params: [chunk, { encoding: 'base64' }] }),
    }).then((x) => x.json()).catch(() => null)
    const values: Json[] = r?.result?.value ?? []
    values.forEach((v, k) => {
      if (v?.owner !== PUMP_PROGRAM || !Array.isArray(v.data)) return
      const p = pumpProgress(b64(v.data[0]))
      if (p) out.set(chunk[k], p)
    })
  }))
  return out
}

interface PoolPage { pools: Json[]; tokens: Map<string, Json>; error?: string }

async function pools(chain: LaunchChain, path: string): Promise<PoolPage> {
  try {
    const body = await cached(`gt:${chain}:${path}`, () => getJson(`${GECKO}/${NET[chain]}/${path}${path.includes('?') ? '&' : '?'}include=base_token,quote_token,dex`), TTL)
    const tokens = new Map<string, Json>((body?.included ?? []).map((t: Json) => [t.id, t]))
    return { pools: body?.data ?? [], tokens }
  } catch (e) {
    return { pools: [], tokens: new Map(), error: (e as Error).message }
  }
}

/** 一页读不到就少一部分，全都读不到时报错，不把「没读到」显示成「没有新币」 */
function allFailed(pages: PoolPage[]): void {
  if (pages.every((p) => p.error)) throw new Error(`新币数据暂时读不到（${pages[0].error}），稍后会自动重试`)
}

function toToken(chain: LaunchChain, pool: Json, tokens: Map<string, Json>, progress?: { pct: number; complete: boolean }): Token | null {
  const pair = geckoPair(chain, pool, tokens)
  if (!pair) return null
  const t = tokenView(chain, [pair], null)
  if (!t) return null
  chainOfAddress.set(t.address.toLowerCase(), chain)
  const onCurve = BONDING_DEXES.has(pair.dexId)
  return {
    ...t,
    graduated: progress?.complete ? true : !onCurve,
    bondingPct: progress ? progress.pct : onCurve ? null : 100,
    launchpad: LAUNCHPAD[pair.dexId] ?? t.launchpad,
    dataSource: progress ? 'GeckoTerminal；内盘进度读自 Solana 链上发射曲线账户' : 'GeckoTerminal（浏览器直接读取）',
  }
}

const dedupe = (ts: Token[]) => { const seen = new Set<string>(); return ts.filter((t) => !seen.has(t.address) && !!seen.add(t.address)) }

/** 是否像某个发射平台毕业后迁移出来的池子 */
export function looksGraduated(chain: LaunchChain, dex: string, mint: string): boolean {
  if (chain === 'solana') return (dex === 'pumpswap' && mint.endsWith('pump')) || (dex === 'raydium' && mint.endsWith('bonk'))
  return dex.startsWith('pancakeswap') && /(4444|ffff)$/i.test(mint)
}

export async function launches(chain: LaunchChain): Promise<Launches> {
  const now = Date.now()
  if (chain === 'solana') {
    const [n1, n2, curve, swap] = await Promise.all([
      pools(chain, 'new_pools?page=1'), pools(chain, 'new_pools?page=2'),
      pools(chain, 'dexes/pump-fun/pools?sort=h24_tx_count_desc'), pools(chain, 'dexes/pumpswap/pools?sort=h24_tx_count_desc'),
    ])
    allFailed([n1, n2, curve, swap])
    const tokens = new Map([...n1.tokens, ...n2.tokens, ...curve.tokens, ...swap.tokens])
    const fresh = [...n1.pools, ...n2.pools]
    const pumpAddrs = [...fresh, ...curve.pools].filter((p) => p.relationships?.dex?.data?.id === 'pump-fun').map((p) => p.attributes.address)
    const progress = await readPumpCurves([...new Set(pumpAddrs)]).catch(() => new Map())
    const make = (p: Json) => toToken(chain, p, tokens, progress.get(p.attributes?.address))
    const freshTokens = fresh.filter((p) => BONDING_DEXES.has(p.relationships?.dex?.data?.id)).map(make).filter((t): t is Token => !!t && !t.graduated)
    const soon = curve.pools.map(make).filter((t): t is Token => !!t && !t.graduated && t.bondingPct != null)
      .sort((a, b) => (b.bondingPct ?? 0) - (a.bondingPct ?? 0))
    const graduated = [...fresh, ...swap.pools]
      .filter((p) => looksGraduated(chain, p.relationships?.dex?.data?.id ?? '', String(p.relationships?.base_token?.data?.id ?? '').replace('solana_', '')))
      .filter((p) => now - Date.parse(p.attributes?.pool_created_at ?? '') < DAY_MS)
      .map((p) => toToken(chain, p, tokens)).filter((t): t is Token => !!t)
      .sort((a, b) => a.ageMinutes - b.ageMinutes)
    return { fresh: dedupe(freshTokens), soon: dedupe(soon), graduated: dedupe(graduated), hasProgress: true, updatedAt: new Date().toISOString(), partial: [n1, n2, curve, swap].some((p) => p.error) }
  }
  const [n1, n2, curve] = await Promise.all([pools(chain, 'new_pools?page=1'), pools(chain, 'new_pools?page=2'), pools(chain, 'dexes/four-meme/pools?sort=h24_tx_count_desc')])
  allFailed([n1, n2, curve])
  const tokens = new Map([...n1.tokens, ...n2.tokens, ...curve.tokens])
  const fresh = [...n1.pools, ...n2.pools]
  const make = (p: Json) => toToken(chain, p, tokens)
  return {
    fresh: dedupe(fresh.filter((p) => BONDING_DEXES.has(p.relationships?.dex?.data?.id)).map(make).filter((t): t is Token => !!t)),
    soon: dedupe(curve.pools.map(make).filter((t): t is Token => !!t)),
    graduated: dedupe(fresh.filter((p) => looksGraduated(chain, p.relationships?.dex?.data?.id ?? '', String(p.relationships?.base_token?.data?.id ?? '').replace('bsc_', '')))
      .map(make).filter((t): t is Token => !!t).sort((a, b) => a.ageMinutes - b.ageMinutes)),
    hasProgress: false, updatedAt: new Date().toISOString(), partial: [n1, n2, curve].some((p) => p.error),
  }
}
