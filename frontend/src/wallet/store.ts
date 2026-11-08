import { create } from 'zustand'
import type { EvmWallet, SolanaWallet } from './discover'

/* eslint-disable @typescript-eslint/no-explicit-any -- 钱包账户对象来自钱包本身 */

/** 连上的钱包。平台不保存私钥，也不经手资金：每笔交易都由用户在自己的钱包里确认。 */
interface WalletState {
  solana: { wallet: SolanaWallet; account: any; address: string } | null
  evm: { wallet: EvmWallet; address: string } | null
  pickerOpen: 'solana' | 'evm' | null
  setSolana: (v: WalletState['solana']) => void
  setEvm: (v: WalletState['evm']) => void
  openPicker: (f: 'solana' | 'evm' | null) => void
}

export const useWallet = create<WalletState>((set) => ({
  solana: null,
  evm: null,
  pickerOpen: null,
  setSolana: (solana) => set({ solana }),
  setEvm: (evm) => set({ evm }),
  openPicker: (pickerOpen) => set({ pickerOpen }),
}))
