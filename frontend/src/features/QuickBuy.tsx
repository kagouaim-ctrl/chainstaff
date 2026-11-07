import { create } from 'zustand'
import { useEffect } from 'react'
import type { Token } from '@/domain/types'
import { NET } from '@/wallet/chains'
import { CHAIN_LABEL } from '@/api/chain'
import { TokenAvatar } from '@/components/ui'
import { LiveTradePanel } from './LiveTradePanel'
import { dataBad } from './tokenBits'

/**
 * 快速买入：列表上的按钮按链显示原生币金额（SOL / BNB / ETH），点了打开一个带报价的确认弹窗，
 * 用户在弹窗里看到预计得到、最少得到、价格冲击后，再到钱包里签名。不会跳过任何一步确认。
 */
const DEFAULT_AMOUNT: Record<string, string> = { SOL: '0.1', BNB: '0.05', ETH: '0.01' }
const KEY = 'kagouai.quickbuy'

function readAmounts(): Record<string, string> {
  try { return { ...DEFAULT_AMOUNT, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') } } catch { return { ...DEFAULT_AMOUNT } }
}

interface QuickBuyState {
  token: Token | null
  amounts: Record<string, string>
  open: (t: Token) => void
  close: () => void
  setAmount: (native: string, v: string) => void
}

export const useQuickBuy = create<QuickBuyState>((set, get) => ({
  token: null,
  amounts: readAmounts(),
  open: (token) => set({ token }),
  close: () => set({ token: null }),
  setAmount: (native, v) => {
    const amounts = { ...get().amounts, [native]: v }
    try { localStorage.setItem(KEY, JSON.stringify(amounts)) } catch { /* 隐私模式不记 */ }
    set({ amounts })
  },
}))

export const quickLabel = (t: Token, amounts: Record<string, string>) => `${amounts[NET[t.chain].native] ?? ''} ${NET[t.chain].native}`

/** 列表里的快速买入按钮。数据异常的代币不能快速买入。 */
export function QuickBuyButton({ t, className }: { t: Token; className: string }) {
  const { amounts, open } = useQuickBuy()
  const bad = dataBad(t)
  return (
    <button onClick={() => open(t)} disabled={bad} title={bad ? '行情数据异常，暂停交易' : `用 ${quickLabel(t, amounts)} 买入 ${t.symbol}，下单前会显示报价并要求钱包确认`}
      aria-label={`快速买入 ${t.symbol}（${quickLabel(t, amounts)}）`} className={`${className} disabled:cursor-not-allowed disabled:opacity-40`}>
      {quickLabel(t, amounts)}
    </button>
  )
}

export function QuickBuyDialog() {
  const { token: t, amounts, close } = useQuickBuy()
  useEffect(() => {
    if (!t) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [t, close])
  if (!t) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div className="absolute inset-0 bg-black/60" onClick={close} />
      <div role="dialog" aria-modal="true" aria-label={`快速买入 ${t.symbol}`} className="anim-enter relative w-full max-w-[400px] rounded-t-2xl border border-line bg-bg p-4 sm:rounded-2xl">
        <div className="mb-3 flex items-center gap-2">
          <TokenAvatar image={t.image} symbol={t.symbol} hue={t.hue} size={28} />
          <span className="text-[15px] font-medium">快速买入 {t.symbol}</span>
          <span className="rounded bg-raised px-1 text-[11px] text-sub">{CHAIN_LABEL[t.chain]}</span>
          <button onClick={close} aria-label="关闭" className="ml-auto h-8 w-8 rounded-md text-sub hover:bg-raised hover:text-fg">✕</button>
        </div>
        <LiveTradePanel key={t.address} token={t} initialSide="BUY" initialAmount={amounts[NET[t.chain].native] ?? ''} bare />
      </div>
    </div>
  )
}
