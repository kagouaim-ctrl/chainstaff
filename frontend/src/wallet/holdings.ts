/**
 * 连接的钱包在链上的真实持仓。只读链上和公开接口，不经过我们的服务器。
 * Solana：Jupiter 的余额接口能列出一个地址的全部代币；EVM 列全部代币需要索引服务，暂时只能查单个代币（代币页）。
 */
import type { Chain } from '@/domain/types'
import { prices, type PriceRow } from '@/api/chain'
import { nativeBalance } from './actions'
import { NET, fromUnits } from './chains'

export interface Holding { chain: Chain; address: string; amount: number; info: PriceRow | null; valueUsd: number | null }

const WSOL = 'So11111111111111111111111111111111111111112'

export async function solanaHoldings(owner: string): Promise<Holding[]> {
  const r = await fetch(`https://lite-api.jup.ag/ultra/v1/balances/${owner}`)
  if (!r.ok) throw new Error(`读取持仓失败（Jupiter 返回 ${r.status}）`)
  const body: Record<string, { uiAmount?: number }> = await r.json()
  const held = Object.entries(body).filter(([k, v]) => k !== 'SOL' && (v.uiAmount ?? 0) > 0)
  const info = await prices('solana', held.map(([k]) => k))
  return held.map(([address, v]) => {
    const i = info.get(address) ?? null
    const amount = v.uiAmount ?? 0
    return { chain: 'solana' as Chain, address, amount, info: i, valueUsd: i?.priceUsd != null ? i.priceUsd * amount : null }
  }).sort((a, b) => (b.valueUsd ?? -1) - (a.valueUsd ?? -1))
}

/** 原生币余额和美元价值（价格来自主交易对，取不到就是 null）。 */
export async function nativeHolding(chain: Chain, owner: string): Promise<{ amount: number; valueUsd: number | null }> {
  const raw = await nativeBalance(chain, owner)
  const amount = Number(fromUnits(raw, NET[chain].decimals, 6))
  // 用 SOL / WBNB / WETH 的主交易对估价；Robinhood Chain 暂时没有可靠的参考池，显示为未知
  const ref: Record<Chain, string> = { solana: WSOL, bsc: '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c', ethereum: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', robinhood: '' }
  if (!ref[chain]) return { amount, valueUsd: null }
  const p = (await prices(chain, [ref[chain]]).catch(() => new Map())).get(ref[chain])
  return { amount, valueUsd: p?.priceUsd != null ? p.priceUsd * amount : null }
}
