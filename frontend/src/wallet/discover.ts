/**
 * 发现浏览器里装了哪些钱包，不依赖任何钱包库：
 * Solana 钱包走 Wallet Standard（Phantom、OKX、Backpack、Solflare 都支持），EVM 钱包走 EIP-6963（MetaMask、OKX、Rabby 等）。
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 钱包注入的对象没有统一类型 */

export interface SolanaWallet { name: string; icon: string; raw: any }
export interface EvmWallet { name: string; icon: string; rdns: string; provider: any }

const solana: SolanaWallet[] = []
const evm: EvmWallet[] = []
let started = false
const listeners = new Set<() => void>()
const changed = () => listeners.forEach((f) => f())

function addSolana(w: any) {
  if (!w?.chains?.some((c: string) => c.startsWith('solana:'))) return
  if (!w.features?.['standard:connect'] || !w.features?.['solana:signAndSendTransaction']) return
  if (solana.some((x) => x.name === w.name)) return
  solana.push({ name: w.name, icon: w.icon, raw: w }); changed()
}

export function startDiscovery() {
  if (started || typeof window === 'undefined') return
  started = true
  const api = { register: (...ws: any[]) => { ws.forEach(addSolana); return () => {} } }
  window.addEventListener('wallet-standard:register-wallet', ((e: CustomEvent) => e.detail(api)) as EventListener)
  window.dispatchEvent(new CustomEvent('wallet-standard:app-ready', { detail: api }))
  window.addEventListener('eip6963:announceProvider', ((e: CustomEvent) => {
    const { info, provider } = e.detail ?? {}
    if (!info?.uuid || evm.some((x) => x.rdns === info.rdns)) return
    evm.push({ name: info.name, icon: info.icon, rdns: info.rdns, provider }); changed()
  }) as EventListener)
  window.dispatchEvent(new Event('eip6963:requestProvider'))
  // 老钱包只注入 window.ethereum，不发 EIP-6963 公告
  setTimeout(() => {
    const eth = (window as any).ethereum
    if (eth && !evm.length) { evm.push({ name: eth.isMetaMask ? 'MetaMask' : '浏览器钱包', icon: '', rdns: 'injected', provider: eth }); changed() }
  }, 500)
}

export const solanaWallets = () => solana.slice()
export const evmWallets = () => evm.slice()
export function onWalletsChanged(f: () => void) { listeners.add(f); return () => { listeners.delete(f) } }
