import type { Chain } from '@/domain/types'

/** 每条链的钱包、RPC 和浏览器信息。RPC 用 publicnode 的公共节点，都允许网页直接调用。 */
export const NET: Record<Chain, { family: 'solana' | 'evm'; native: string; decimals: number; rpc: string; explorer: string; chainId?: number }> = {
  solana: { family: 'solana', native: 'SOL', decimals: 9, rpc: 'https://solana-rpc.publicnode.com', explorer: 'https://solscan.io' },
  bsc: { family: 'evm', native: 'BNB', decimals: 18, rpc: 'https://bsc-rpc.publicnode.com', explorer: 'https://bscscan.com', chainId: 56 },
  ethereum: { family: 'evm', native: 'ETH', decimals: 18, rpc: 'https://ethereum-rpc.publicnode.com', explorer: 'https://etherscan.io', chainId: 1 },
  robinhood: { family: 'evm', native: 'ETH', decimals: 18, rpc: 'https://robinhood-rpc.publicnode.com', explorer: 'https://robinhoodchain.blockscout.com', chainId: 4663 },
}

export const txUrl = (chain: Chain, sig: string) => `${NET[chain].explorer}/tx/${sig}`

/** 金额字符串和最小单位互转，全程用 BigInt，不走浮点。 */
export function toUnits(amount: string, decimals: number): bigint {
  const [whole, frac = ''] = amount.trim().split('.')
  if (!/^\d*$/.test(whole) || !/^\d*$/.test(frac)) throw new Error('金额格式不对')
  return BigInt((whole || '0') + frac.slice(0, decimals).padEnd(decimals, '0'))
}

export function fromUnits(units: bigint, decimals: number, digits = 6): string {
  const s = units.toString().padStart(decimals + 1, '0')
  const whole = s.slice(0, s.length - decimals)
  const frac = s.slice(s.length - decimals).slice(0, digits).replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : whole
}

export async function rpc<T>(chain: Chain, method: string, params: unknown[]): Promise<T> {
  const r = await fetch(NET[chain].rpc, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) })
  const body = await r.json()
  if (body.error) throw new Error(body.error.message ?? `${method} 失败`)
  return body.result as T
}
