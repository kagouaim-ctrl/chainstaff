/**
 * 连钱包、读余额、签名发送。全部在用户的浏览器里完成，私钥从不离开钱包。
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 钱包和 RPC 的原始返回 */
import type { Chain } from '@/domain/types'
import { NET, rpc } from './chains'
import type { EvmWallet, SolanaWallet } from './discover'
import { useWallet } from './store'

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
export function base58(bytes: Uint8Array): string {
  let n = 0n
  for (const b of bytes) n = n * 256n + BigInt(b)
  let out = ''
  while (n > 0n) { out = B58[Number(n % 58n)] + out; n /= 58n }
  for (const b of bytes) { if (b !== 0) break; out = '1' + out }
  return out
}
const fromBase64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0))

export async function connectSolana(w: SolanaWallet) {
  const { accounts } = await w.raw.features['standard:connect'].connect()
  const account = accounts.find((a: any) => a.chains?.includes('solana:mainnet')) ?? accounts[0]
  if (!account) throw new Error('钱包没有返回账户')
  useWallet.getState().setSolana({ wallet: w, account, address: account.address })
}

export async function connectEvm(w: EvmWallet) {
  const accounts: string[] = await w.provider.request({ method: 'eth_requestAccounts' })
  if (!accounts?.[0]) throw new Error('钱包没有返回账户')
  useWallet.getState().setEvm({ wallet: w, address: accounts[0] })
}

export function disconnect(family: 'solana' | 'evm') {
  const s = useWallet.getState()
  if (family === 'solana') { s.solana?.wallet.raw.features['standard:disconnect']?.disconnect?.().catch?.(() => {}); s.setSolana(null) }
  else s.setEvm(null)
}

/** 让 EVM 钱包切到这条链；钱包里没有这条链就先添加。 */
export async function ensureEvmChain(chain: Chain) {
  const w = useWallet.getState().evm
  if (!w) throw new Error('先连接钱包')
  const id = NET[chain].chainId!
  const hex = `0x${id.toString(16)}`
  const current = await w.wallet.provider.request({ method: 'eth_chainId' })
  if (Number(current) === id) return
  try {
    await w.wallet.provider.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] })
  } catch (e: any) {
    if (e?.code !== 4902) throw e
    await w.wallet.provider.request({ method: 'wallet_addEthereumChain', params: [{
      chainId: hex, chainName: chain === 'bsc' ? 'BNB Smart Chain' : chain === 'ethereum' ? 'Ethereum' : 'Robinhood Chain',
      nativeCurrency: { name: NET[chain].native, symbol: NET[chain].native, decimals: 18 },
      rpcUrls: [NET[chain].rpc], blockExplorerUrls: [NET[chain].explorer],
    }] })
  }
}

const pad = (hex: string) => hex.replace(/^0x/, '').toLowerCase().padStart(64, '0')
const call = (chain: Chain, to: string, data: string) => rpc<string>(chain, 'eth_call', [{ to, data }, 'latest'])

/** 原生币余额（最小单位）。 */
export async function nativeBalance(chain: Chain, address: string): Promise<bigint> {
  if (chain === 'solana') return BigInt((await rpc<any>(chain, 'getBalance', [address])).value)
  return BigInt(await rpc<string>(chain, 'eth_getBalance', [address, 'latest']))
}

/** 代币余额和小数位。 */
export async function tokenBalance(chain: Chain, owner: string, token: string): Promise<{ raw: bigint; decimals: number }> {
  if (chain === 'solana') {
    // 公共 RPC 不开放按持有人查代币账户，所以余额用 Jupiter 的接口，小数位读 mint 账户
    const [balances, mint] = await Promise.all([
      fetch(`https://lite-api.jup.ag/ultra/v1/balances/${owner}`).then((r) => r.json()),
      rpc<any>(chain, 'getAccountInfo', [token, { encoding: 'jsonParsed' }]),
    ])
    const decimals = mint?.value?.data?.parsed?.info?.decimals
    if (decimals == null) throw new Error('读不到代币的小数位')
    return { raw: BigInt(balances?.[token]?.amount ?? 0), decimals }
  }
  const [bal, dec] = await Promise.all([call(chain, token, `0x70a08231${pad(owner)}`), call(chain, token, '0x313ce567')])
  return { raw: BigInt(bal === '0x' ? 0 : bal), decimals: Number(BigInt(dec === '0x' ? 18 : dec)) }
}

export async function allowance(chain: Chain, token: string, owner: string, spender: string): Promise<bigint> {
  const r = await call(chain, token, `0xdd62ed3e${pad(owner)}${pad(spender)}`)
  return BigInt(r === '0x' ? 0 : r)
}

/** 让用户在钱包里签名并发送一笔 Solana 交易（Jupiter 生成的序列化交易）。返回交易签名。 */
export async function sendSolana(serializedBase64: string): Promise<string> {
  const s = useWallet.getState().solana
  if (!s) throw new Error('先连接 Solana 钱包')
  const feature = s.wallet.raw.features['solana:signAndSendTransaction']
  const [out] = await feature.signAndSendTransaction({ account: s.account, chain: 'solana:mainnet', transaction: fromBase64(serializedBase64) })
  return base58(out.signature)
}

/** 让用户在钱包里确认一笔 EVM 交易。返回交易哈希。 */
export async function sendEvm(chain: Chain, tx: { to: string; data: string; value?: string; gasLimit?: string }): Promise<string> {
  const w = useWallet.getState().evm
  if (!w) throw new Error('先连接钱包')
  await ensureEvmChain(chain)
  const params: any = { from: w.address, to: tx.to, data: tx.data, value: tx.value ?? '0x0' }
  if (tx.gasLimit) params.gas = tx.gasLimit
  return w.wallet.provider.request({ method: 'eth_sendTransaction', params: [params] })
}

/** 等交易上链。超时只说明还没确认，不等于失败。 */
export async function waitConfirmed(chain: Chain, id: string, timeoutMs = 90_000): Promise<'confirmed' | 'failed' | 'pending'> {
  const end = Date.now() + timeoutMs
  while (Date.now() < end) {
    try {
      if (chain === 'solana') {
        const st = (await rpc<any>(chain, 'getSignatureStatuses', [[id], { searchTransactionHistory: true }])).value?.[0]
        if (st?.err) return 'failed'
        if (st?.confirmationStatus === 'confirmed' || st?.confirmationStatus === 'finalized') return 'confirmed'
      } else {
        const r = await rpc<any>(chain, 'eth_getTransactionReceipt', [id])
        if (r) return r.status === '0x1' ? 'confirmed' : 'failed'
      }
    } catch { /* 节点偶尔超时，下一轮再查 */ }
    await new Promise((ok) => setTimeout(ok, 2000))
  }
  return 'pending'
}
