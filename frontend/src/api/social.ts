/**
 * 代币群聊和观点，存在 KAGOUAI-meme-agent 后端。读不用登录；发言要先用钱包签一段文字登录
 * （只是证明钱包归属的签名，不是交易，不花钱）。会话令牌存在浏览器本地，7 天有效。
 */
import { API_URL } from './live'
import { useWallet } from '@/wallet/store'
import { base58 } from '@/wallet/actions'
import type { Chain } from '@/domain/types'

export const COMMUNITY_ONLINE = !!API_URL

export interface Post { id: number; chain: Chain; token: string; wallet: string; kind: 'chat' | 'thesis'; text: string; stance: 'bull' | 'bear' | null; priceAt: number | null; createdAt: number }
interface Session { wallet: string; token: string; expiresAt: number }

const KEY = 'kagouai.social.v1'
function sessions(): Record<string, Session> { try { return JSON.parse(localStorage.getItem(KEY) ?? '{}') ?? {} } catch { return {} } }
export function sessionFor(wallet: string | undefined): Session | null {
  const s = wallet ? sessions()[wallet] : undefined
  return s && s.expiresAt * 1000 > Date.now() ? s : null
}
function keep(s: Session) { try { localStorage.setItem(KEY, JSON.stringify({ ...sessions(), [s.wallet]: s })) } catch { /* 隐私模式不记 */ } }

async function call<T>(path: string, init: RequestInit = {}, wallet?: string): Promise<T> {
  if (!API_URL) throw new Error('社区功能待后端上线')
  const s = sessionFor(wallet)
  const r = await fetch(`${API_URL}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(s ? { Authorization: `Bearer ${s.token}` } : {}) } })
  if (r.status === 204) return undefined as T
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.detail ?? `后端返回 ${r.status}`)
  return body
}

/** 发言用哪个钱包：Solana 代币用 Solana 钱包，EVM 代币用 EVM 钱包 */
export function speakerFor(chain: Chain): string | undefined {
  const w = useWallet.getState()
  return chain === 'solana' ? w.solana?.address : w.evm?.address
}

async function signText(chain: Chain, text: string): Promise<string> {
  const w = useWallet.getState()
  if (chain === 'solana') {
    if (!w.solana) throw new Error('先连接 Solana 钱包')
    const feature = w.solana.wallet.raw.features['solana:signMessage']
    if (!feature) throw new Error('这个钱包不支持签名消息')
    const [out] = await feature.signMessage({ account: w.solana.account, message: new TextEncoder().encode(text) })
    return base58(out.signature)
  }
  if (!w.evm) throw new Error('先连接 EVM 钱包')
  const hex = '0x' + [...new TextEncoder().encode(text)].map((b) => b.toString(16).padStart(2, '0')).join('')
  return w.evm.wallet.provider.request({ method: 'personal_sign', params: [hex, w.evm.address] })
}

/** 用钱包签名登录社区。返回钱包地址。 */
export async function signIn(chain: Chain): Promise<string> {
  const wallet = speakerFor(chain)
  if (!wallet) throw new Error(chain === 'solana' ? '先连接 Solana 钱包' : '先连接 EVM 钱包')
  const c = await call<{ nonce: string; message: string }>(`/api/social/challenge?wallet=${encodeURIComponent(wallet)}`)
  const signature = await signText(chain, c.message)
  const s = await call<Session>('/api/social/session', { method: 'POST', body: JSON.stringify({ wallet, nonce: c.nonce, signature }) })
  keep(s)
  return wallet
}

const room = (chain: Chain, token: string) => `/api/social/${chain}/${encodeURIComponent(token)}`
export const messages = (chain: Chain, token: string) => call<Post[]>(`${room(chain, token)}/messages`)
export const theses = (chain: Chain, token: string) => call<Post[]>(`${room(chain, token)}/theses`)
export const postMessage = (chain: Chain, token: string, text: string) => call<Post>(`${room(chain, token)}/messages`, { method: 'POST', body: JSON.stringify({ text }) }, speakerFor(chain))
export const putThesis = (chain: Chain, token: string, stance: 'bull' | 'bear', text: string) => call<Post>(`${room(chain, token)}/thesis`, { method: 'PUT', body: JSON.stringify({ stance, text }) }, speakerFor(chain))
export const deletePost = (chain: Chain, id: number) => call<void>(`/api/social/posts/${id}`, { method: 'DELETE' }, speakerFor(chain))
