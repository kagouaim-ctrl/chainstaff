import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Dialog, cx } from '@/components/ui'
import { useToast } from '@/app/store'
import { shortAddr } from '@/lib/format'
import { evmWallets, onWalletsChanged, solanaWallets, startDiscovery } from './discover'
import { connectEvm, connectSolana, disconnect, nativeBalance } from './actions'
import { fromUnits } from './chains'
import { useWallet } from './store'
import { copyText } from '@/lib/copy'
import { Link } from 'react-router-dom'

const TERMS_KEY = 'kagouai.terms.v1'
const readAccepted = () => { try { return localStorage.getItem(TERMS_KEY) === '1' } catch { return false } }

const INSTALL = {
  solana: [['Phantom', 'https://phantom.com/download'], ['OKX Wallet', 'https://web3.okx.com/download'], ['Backpack', 'https://backpack.app/download']],
  evm: [['MetaMask', 'https://metamask.io/download/'], ['OKX Wallet', 'https://web3.okx.com/download'], ['Rabby', 'https://rabby.io/']],
} as const

function useDetected() {
  useEffect(() => { startDiscovery() }, [])
  const version = useSyncExternalStore(onWalletsChanged, () => solanaWallets().length * 100 + evmWallets().length)
  return { version, solana: solanaWallets(), evm: evmWallets() }
}

/** 选钱包的对话框。只列出浏览器里真的装了的钱包；一个都没有就给安装链接。 */
export function WalletPicker() {
  const { pickerOpen, openPicker } = useWallet()
  const toast = useToast((s) => s.show)
  const found = useDetected()
  const [busy, setBusy] = useState('')
  const [accepted, setAccepted] = useState(readAccepted)
  if (!pickerOpen) return null
  const family = pickerOpen
  const list = family === 'solana' ? found.solana : found.evm
  const pick = async (name: string, connect: () => Promise<void>) => {
    if (!accepted) return toast('请先勾选同意条款和风险披露')
    try { localStorage.setItem(TERMS_KEY, '1') } catch { /* 隐私模式下每次都要勾选 */ }
    setBusy(name)
    try { await connect(); openPicker(null); toast(`已连接 ${name}`) } catch (e) { toast(e instanceof Error ? `没有连上：${e.message}` : '没有连上') } finally { setBusy('') }
  }
  return (
    <Dialog open onClose={() => openPicker(null)} title={family === 'solana' ? '连接 Solana 钱包' : '连接 BSC / ETH / Robinhood 钱包'} footer={<Button onClick={() => openPicker(null)}>取消</Button>}>
      <div role="tablist" aria-label="钱包类型" className="mb-3 flex gap-1 rounded-lg bg-bg p-1">
        {(['solana', 'evm'] as const).map((f) => (
          <button key={f} role="tab" aria-selected={family === f} onClick={() => openPicker(f)}
            className={cx('h-8 flex-1 rounded-md text-[13px]', family === f ? 'bg-raised text-fg' : 'text-sub hover:text-fg')}>
            {f === 'solana' ? 'Solana' : 'BSC / ETH / Robinhood'}
          </button>
        ))}
      </div>
      <p className="text-sub">资金一直在你自己的钱包里。每笔交易都要你在钱包里确认，平台拿不到你的私钥，也动不了你的钱。</p>
      <ul className="mt-3 space-y-2">
        {list.map((w) => (
          <li key={w.name}>
            <button disabled={!!busy || !accepted} onClick={() => pick(w.name, () => family === 'solana' ? connectSolana(w as never) : connectEvm(w as never))}
              className="flex h-12 w-full items-center gap-3 rounded-lg border border-line px-3 text-left hover:bg-raised disabled:opacity-50">
              {w.icon ? <img src={w.icon} alt="" width={24} height={24} className="rounded" /> : <span className="h-6 w-6 rounded bg-raised" />}
              <span className="flex-1 text-[14px]">{w.name}</span>
              {busy === w.name && <span className="text-xs text-sub">请在钱包里确认…</span>}
            </button>
          </li>
        ))}
      </ul>
      <label className="mt-3 flex items-start gap-2 text-[12px] text-sub">
        <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} className="mt-0.5" />
        <span>我已阅读并同意 <Link to="/legal/terms" onClick={() => openPicker(null)} className="text-agent hover:underline">服务条款</Link> 和 <Link to="/legal/risk" onClick={() => openPicker(null)} className="text-agent hover:underline">风险披露</Link>，明白在这里的买卖是真实链上交易。</span>
      </label>
      {!list.length && (
        <div className="mt-3 text-[13px]">
          <p className="text-sub">这个浏览器里没有找到钱包。装一个再刷新页面：</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {INSTALL[family].map(([n, u]) => <a key={n} href={u} target="_blank" rel="noreferrer" className="rounded-lg border border-line px-3 py-1.5 hover:bg-raised">{n}</a>)}
          </div>
        </div>
      )}
    </Dialog>
  )
}

function NativeBalance({ chain, address }: { chain: 'solana' | 'bsc'; address: string }) {
  const q = useQuery({ queryKey: ['native', chain, address], queryFn: () => nativeBalance(chain, address), refetchInterval: 30_000 })
  return <span className="num text-xs text-sub">{q.data != null ? `${fromUnits(q.data, chain === 'solana' ? 9 : 18, 4)} ${chain === 'solana' ? 'SOL' : 'BNB'}` : '…'}</span>
}

/** 顶栏的钱包按钮：没连就是「连接钱包」，连了显示地址，点开可以断开或连另一类钱包。 */
export function WalletButton() {
  const { solana, evm, openPicker } = useWallet()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])
  const any = solana || evm
  if (!any) return <Button variant="primary" className="h-11 px-4" onClick={() => openPicker('solana')}>连接钱包</Button>
  const row = (label: string, addr: string | undefined, family: 'solana' | 'evm') => (
    <li className="flex items-center gap-2 py-2">
      <span className="w-20 text-[12px] text-sub">{label}</span>
      {addr ? <>
        <span className="flex-1"><span className="num block text-[13px]">{shortAddr(addr, 4, 4)}</span>{family === 'solana' ? <NativeBalance chain="solana" address={addr} /> : <NativeBalance chain="bsc" address={addr} />}</span>
        <button onClick={() => void copyText(addr)} className="text-xs text-sub hover:text-fg">复制</button>
        <button onClick={() => disconnect(family)} className="text-xs text-down hover:underline">断开</button>
      </> : <button onClick={() => { setOpen(false); openPicker(family) }} className="flex-1 text-left text-[13px] text-agent hover:underline">连接</button>}
    </li>
  )
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex h-11 items-center gap-2 rounded-lg border border-line px-3 hover:bg-raised">
        <span className="h-2 w-2 rounded-full bg-up" />
        <span className="num text-[14px]">{shortAddr((solana ?? evm)!.address, 4, 4)}</span>
      </button>
      {open && (
        <div role="dialog" aria-label="钱包" className={cx('anim-enter absolute right-0 top-[52px] z-40 w-[320px] rounded-xl border border-line bg-panel p-4')}>
          <ul className="divide-y divide-line">
            {row('Solana', solana?.address, 'solana')}
            {row('BSC / ETH / Robinhood', evm?.address, 'evm')}
          </ul>
          <p className="mt-2 text-[11px] text-sub">这里显示 BSC 余额；同一个 EVM 钱包在 ETH 和 Robinhood Chain 上的余额在交易面板里显示。</p>
        </div>
      )}
    </div>
  )
}
