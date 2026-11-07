import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { KV, cx } from '@/components/ui'
import { ISliders } from '@/components/icons'
import { usd, price } from '@/lib/format'
import type { Token } from '@/domain/types'
import { CHAIN_LABEL } from '@/api/chain'
import { NET, fromUnits, toUnits, txUrl } from '@/wallet/chains'
import { nativeBalance, tokenBalance } from '@/wallet/actions'
import { execute, getQuote, type Step } from '@/wallet/swap'
import { useWallet } from '@/wallet/store'
import { saveTrade } from '@/wallet/history'
import { SOL_PRIORITY, effectiveSlippageBps, useTradeSettings, type Speed } from '@/wallet/settings'
import { useQuickBuy } from './QuickBuy'
import { dataBad, qualityTitle, securityPass } from './tokenBits'

const BUY_PRESETS: Record<string, string[]> = { SOL: ['0.1', '0.5', '1', '2'], BNB: ['0.05', '0.1', '0.5', '1'], ETH: ['0.01', '0.05', '0.1', '0.5'] }
const SELL_PRESETS = [25, 50, 100]
const SLIPPAGES = [100, 300, 500, 1000, 2000]
const MAX_SLIPPAGES = [500, 1000, 1500, 2000, 3000, 5000]
// Solana 每笔要留一点 SOL 付网络费和开账户，不能全部换掉
const SOL_RESERVE = 10_000_000n

const STAGE: Record<Step['stage'], string> = {
  approve: '请在钱包里授权这次卖出的数量…', sign: '请在钱包里确认交易…', sent: '已发送，等待上链确认…',
  confirmed: '交易已确认', failed: '交易失败，钱没有被换走（只扣了网络费）', pending: '还没确认，可以在浏览器里查看',
}

/** 把钱包、聚合器和节点的报错翻译成用户能看懂、知道下一步怎么做的话。 */
export function explainFailure(msg: string): string {
  if (/reject|denied|cancel|用户拒绝/i.test(msg)) return '你在钱包里取消了这笔交易，没有发生任何扣款。'
  if (/slippage|0x1771|6001|too little received|INSUFFICIENT_OUTPUT/i.test(msg)) return '价格变化超过了最大滑点，交易没有成交。可以稍后重试，或在交易设置里调高滑点。'
  if (/insufficient (funds|lamports|balance)|exceeds balance|0x1\b/i.test(msg)) return '余额不够支付金额和网络费。'
  if (/no route|could not find any route|NO_ROUTES|TOKEN_NOT_TRADABLE/i.test(msg)) return '聚合器找不到可成交的路由：流动性太少，或这个代币限制交易。'
  if (/blockhash|expired|timeout|timed out/i.test(msg)) return '交易过期或网络超时，没有成交。可以重新报价后再试。'
  if (/simulation|模拟失败/i.test(msg)) return `交易在链上模拟时失败，没有发送：${msg}`
  if (/429|rate/i.test(msg)) return '报价服务暂时限流，请过几秒再试。'
  return `没有完成：${msg}`
}

/** 真实交易面板：用户自己的钱包签名，平台不经手资金。 */
export function LiveTradePanel({ token, initialSide = 'BUY', bare, initialAmount = '' }: { token: Token; initialSide?: 'BUY' | 'SELL'; bare?: boolean; initialAmount?: string }) {
  const net = NET[token.chain]
  const wallet = useWallet()
  const owner = net.family === 'solana' ? wallet.solana?.address ?? null : wallet.evm?.address ?? null
  const [side, setSide] = useState<'BUY' | 'SELL'>(initialSide)
  const [amount, setAmount] = useState(initialAmount)
  const settingsAll = useTradeSettings()
  const cfg = settingsAll.byChain[token.chain]
  const slip = effectiveSlippageBps(cfg, token.liquidityUsd)
  const [settings, setSettings] = useState(false)
  const [ack, setAck] = useState(false)
  const [step, setStep] = useState<Step | null>(null)
  const [error, setError] = useState('')
  const qc = useQueryClient()
  const buy = side === 'BUY'

  const nat = useQuery({ queryKey: ['native', token.chain, owner], queryFn: () => nativeBalance(token.chain, owner!), enabled: !!owner, refetchInterval: 30_000 })
  const bal = useQuery({ queryKey: ['tokenBal', token.chain, owner, token.address], queryFn: () => tokenBalance(token.chain, owner!, token.address), enabled: !!owner, refetchInterval: 30_000 })

  // 输入框：买入是原生币数量，卖出是持仓百分比
  const inRaw = useMemo(() => {
    try {
      if (!amount) return 0n
      if (buy) return toUnits(amount, net.decimals)
      const pct = Math.min(100, Number(amount) || 0)
      return bal.data ? (bal.data.raw * BigInt(Math.round(pct * 100))) / 10_000n : 0n
    } catch { return 0n }
  }, [amount, buy, net.decimals, bal.data])

  const [debounced, setDebounced] = useState(0n)
  useEffect(() => { const t = setTimeout(() => setDebounced(inRaw), 400); return () => clearTimeout(t) }, [inRaw])
  const quote = useQuery({
    queryKey: ['quote', token.chain, side, token.address, debounced.toString(), slip, owner],
    queryFn: () => getQuote(token.chain, side, token.address, debounced, slip, owner),
    enabled: debounced > 0n, refetchInterval: 15_000, retry: false,
  })
  useEffect(() => { setStep(null); setError('') }, [amount, side])

  const sec = securityPass(token.security)
  const honeypot = token.security.sellSimulation === 'fail'
  // 只有会直接让钱拿不回来的情况才要勾选确认：卖不出去、还能增发、还能冻结
  const severe = honeypot || token.security.mintAuthority === true || token.security.freezeAuthority === true
  const needsAck = buy && severe
  const tooMuch = buy && nat.data != null && inRaw > (token.chain === 'solana' ? nat.data - SOL_RESERVE : nat.data)
  const running = step != null && ['approve', 'sign', 'sent'].includes(step.stage)
  // 没连钱包时读不到余额里的小数位，用聚合器报价里带的（LI.FI 有，Jupiter 没有）
  const outDecimals = buy ? bal.data?.decimals ?? quote.data?.raw?.action?.toToken?.decimals ?? null : net.decimals
  const fmtOut = (raw: bigint) => outDecimals == null ? '…' : Number(fromUnits(raw, outDecimals, 6)).toLocaleString('en-US', { maximumFractionDigits: 6 })

  // 价格或市值数据异常时不允许下单：参考价可能是错的，用户无法判断自己买到了什么
  const blocked = dataBad(token)
  const submit = async () => {
    setError('')
    if (blocked) return setError(`这个代币的行情数据异常，暂停交易：${qualityTitle(token)}`)
    if (!owner) return wallet.openPicker(net.family)
    if (!inRaw) return setError(buy ? '先输入金额' : '先选择卖出比例')
    if (!buy && !bal.data?.raw) return setError(`你的钱包里没有 ${token.symbol}`)
    if (tooMuch) return setError(`${net.native} 余额不够${token.chain === 'solana' ? '（要留约 0.01 SOL 付网络费）' : ''}`)
    if (needsAck && !ack) return setError('先勾选确认风险')
    // 交易设置里的硬限制：程序直接拦下，不只是提醒
    if (buy && cfg.minLiquidityUsd > 0 && token.liquidityUsd < cfg.minLiquidityUsd) return setError(`流动性 ${usd(token.liquidityUsd, { compact: true })} 低于你设置的最低 ${usd(cfg.minLiquidityUsd, { compact: true })}，已拦下。可以在交易设置里调整。`)
    if (buy && cfg.maxTradeNative > 0 && inRaw > toUnits(String(cfg.maxTradeNative), net.decimals)) return setError(`超过你设置的单笔上限 ${cfg.maxTradeNative} ${net.native}`)
    try {
      // 用钱包地址和当前金额重新报价，免得用了过期的价格
      const fresh = await getQuote(token.chain, side, token.address, inRaw, slip, owner)
      const done = await execute(fresh, owner, setStep, { priority: token.chain === 'solana' ? SOL_PRIORITY[cfg.speed] : undefined })
      if (done.stage === 'confirmed' && done.id) saveTrade({ chain: token.chain, address: token.address, side, time: Date.now(), id: done.id })
      if (done.stage === 'confirmed') { setAmount(''); qc.invalidateQueries({ queryKey: ['native'] }); qc.invalidateQueries({ queryKey: ['tokenBal'] }) }
    } catch (e) {
      setStep(null)
      const msg = e instanceof Error ? e.message : String(e)
      setError(explainFailure(msg))
    }
  }

  const presets = buy ? BUY_PRESETS[net.native] ?? [] : SELL_PRESETS.map(String)
  const holdingUi = bal.data ? fromUnits(bal.data.raw, bal.data.decimals, 4) : null
  const holdingUsd = bal.data ? Number(fromUnits(bal.data.raw, bal.data.decimals, 6)) * token.priceUsd : null

  return (
    <form id={bare ? undefined : 'trade'} aria-label="买卖" className={bare ? '' : 'rounded-xl border border-line p-4'} onSubmit={(e) => { e.preventDefault(); void submit() }}>
      <div className="mb-3 flex items-center justify-between text-[12px]">
        <span className="flex items-center gap-1.5 rounded-md bg-up/10 px-2 py-1 font-medium text-up" title="不是模拟盘：成交会花掉你钱包里的真实资产">
          <span className="h-1.5 w-1.5 rounded-full bg-up" />真实钱包交易
        </span>
        <span className="text-sub">{CHAIN_LABEL[token.chain]} · {net.native}</span>
      </div>
      {blocked && <p role="alert" className="mb-3 rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-[12px] text-warn">行情数据异常，已暂停交易：{qualityTitle(token)}</p>}
      <div className="flex items-center gap-2">
        <div role="tablist" aria-label="买卖方向" className="flex flex-1 gap-1 rounded-lg bg-panel p-1">
          {(['BUY', 'SELL'] as const).map((s) => (
            <button type="button" key={s} role="tab" aria-selected={side === s} onClick={() => { setSide(s); setAmount('') }}
              className={cx('h-9 flex-1 rounded-md text-[14px]', side === s ? (s === 'BUY' ? 'bg-up text-[#060510]' : 'bg-down text-[#060510]') : 'text-sub hover:text-fg')}>
              {s === 'BUY' ? '买入' : '卖出'}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setSettings((v) => !v)} aria-label="交易设置" aria-expanded={settings} className={cx('flex h-11 w-11 items-center justify-center rounded-lg border border-line', settings ? 'text-fg' : 'text-sub hover:text-fg')}>
          <ISliders width={18} height={18} />
        </button>
      </div>

      <label className="mt-4 block rounded-xl bg-panel px-4 py-5 text-center">
        <span className="flex items-center justify-center gap-1">
          <input inputMode="decimal" value={amount} placeholder="0" onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))} aria-label={buy ? `买入金额（${net.native}）` : '卖出比例（%）'}
            style={{ width: `${Math.max(1, amount.length || 1)}ch` }}
            className="num bg-transparent text-[40px] font-medium leading-none tracking-[-2px] outline-none placeholder:text-mute" />
          <span className={cx('num text-[24px] font-medium', amount ? 'text-fg' : 'text-mute')}>{buy ? net.native : '%'}</span>
        </span>
        <span className="num mt-2 block text-[12px] font-normal text-sub">
          {owner
            ? buy ? `可用 ${nat.data != null ? fromUnits(nat.data, net.decimals, 4) : '…'} ${net.native}` : `持有 ${holdingUi ?? '…'} ${token.symbol}${holdingUsd ? ` ≈ ${usd(holdingUsd)}` : ''}`
            : `连接钱包后在 ${CHAIN_LABEL[token.chain]} 上交易`}
        </span>
      </label>

      <div className="mt-3 grid grid-cols-4 gap-2">
        {presets.map((p) => (
          <button type="button" key={p} onClick={() => setAmount(p)} className={cx('num h-9 rounded-lg text-[14px]', amount === p ? 'bg-raised text-fg' : 'bg-panel text-sub hover:text-fg')}>
            {buy ? p : `${p}%`}
          </button>
        ))}
      </div>

      {settings && <SettingsPanel token={token} />}

      {needsAck && (
        <label className="mt-3 flex items-start gap-2 rounded-lg border border-down/40 bg-down/10 p-2.5 text-[12px] text-down">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
          <span>{honeypot ? '安全检查显示这个代币可能卖不出去（貔貅盘）。' : '这个代币的发行方还能增发或冻结代币。'}买入后可能无法卖出或被稀释。我知道风险，仍然买入。</span>
        </label>
      )}
      {buy && !needsAck && sec === false && <p className="mt-3 text-[12px] text-warn">安全检查有未通过的项目，详见右侧「安全检查」。</p>}

      <button type="submit" disabled={running || blocked}
        className={cx('mt-4 h-11 w-full rounded-full text-[14px] disabled:opacity-60', !owner ? 'bg-agent text-white' : inRaw > 0n ? (buy ? 'bg-up text-[#060510]' : 'bg-down text-[#060510]') : 'bg-raised text-sub')}>
        {!owner ? `连接${net.family === 'solana' ? ' Solana ' : ''}钱包` : running ? '处理中…' : `${buy ? '买入' : '卖出'} ${token.symbol}`}
      </button>

      {error && <p role="alert" className="mt-2 text-[12px] text-down">{error}</p>}
      {step && (
        <p role="status" className={cx('mt-2 text-[12px]', step.stage === 'confirmed' ? 'text-up' : step.stage === 'failed' ? 'text-down' : 'text-sub')}>
          {STAGE[step.stage]}{step.id && <> · <a href={txUrl(token.chain, step.id)} target="_blank" rel="noreferrer" className="underline">查看交易</a></>}
        </p>
      )}

      {inRaw > 0n && (
        <div className="mt-3 text-[12px] font-normal">
          {quote.isFetching && !quote.data && <p className="py-1 text-sub">正在报价…</p>}
          {quote.isError && <p className="py-1 text-down">报价失败：{(quote.error as Error).message}</p>}
          {quote.data && <>
            <KV k="预计得到" v={`${fmtOut(quote.data.outRaw)} ${buy ? token.symbol : net.native}`} className="py-1 text-[12px]" />
            <KV k="最少得到（含滑点）" v={`${fmtOut(quote.data.minOutRaw)} ${buy ? token.symbol : net.native}`} className="py-1 text-[12px]" />
            {(quote.data.inUsd ?? quote.data.outUsd) != null && <KV k="金额" v={`≈ ${usd(quote.data.inUsd ?? quote.data.outUsd)}`} className="py-1 text-[12px]" />}
            {quote.data.priceImpactPct != null && <KV k="价格冲击" v={<span className={quote.data.priceImpactPct > 5 ? 'text-down' : ''}>{quote.data.priceImpactPct < 0.01 ? '< 0.01%' : `${quote.data.priceImpactPct.toFixed(2)}%`}</span>} className="py-1 text-[12px]" />}
            <KV k="路由" v={quote.data.route} className="py-1 text-[12px]" />
          </>}
          <KV k="参考价格" v={price(token.priceUsd)} className="py-1 text-[12px]" />
          <KV k="平台手续费" v="0（暂不收取）" className="py-1 text-[12px]" />
        </div>
      )}
      <p className="mt-3 text-center text-[12px] font-normal text-mute">由你的钱包签名，平台不经手资金 · {token.chain === 'solana' ? 'Jupiter' : 'LI.FI'} 报价</p>
    </form>
  )
}

/** 交易设置，按链分开。Solana 有优先费档位；EVM 的 Gas 由钱包估算，在钱包里调整。 */
function SettingsPanel({ token }: { token: Token }) {
  const net = NET[token.chain]
  const { byChain, update, reset } = useTradeSettings()
  const cfg = byChain[token.chain]
  const set = (p: Parameters<typeof update>[1]) => update(token.chain, p)
  const quick = useQuickBuy()
  const row = 'flex items-center justify-between gap-2 py-1.5'
  const input = 'num w-24 rounded-md border border-line bg-panel px-2 py-1 text-right text-fg'
  const sel = 'rounded-md border border-line bg-panel px-2 py-1 text-fg'
  return (
    <div aria-label="交易设置" className="mt-3 rounded-lg border border-line p-3 text-[12px] text-sub">
      <div className="mb-1 flex items-center justify-between text-fg"><span>{CHAIN_LABEL[token.chain]} 交易设置</span><button type="button" onClick={() => reset(token.chain)} className="text-sub hover:text-fg">恢复默认</button></div>
      <label className={row}>滑点
        <select className={sel} value={String(cfg.slippage)} onChange={(e) => set({ slippage: e.target.value === 'auto' ? 'auto' : Number(e.target.value) })}>
          <option value="auto">自动（当前 {effectiveSlippageBps(cfg, token.liquidityUsd) / 100}%）</option>
          {SLIPPAGES.map((v) => <option key={v} value={v}>{v / 100}%</option>)}
        </select>
      </label>
      <label className={row}>最大滑点
        <select className={sel} value={cfg.maxSlippageBps} onChange={(e) => set({ maxSlippageBps: Number(e.target.value) })}>
          {MAX_SLIPPAGES.map((v) => <option key={v} value={v}>{v / 100}%</option>)}
        </select>
      </label>
      {token.chain === 'solana'
        ? <label className={row}>速度 / 优先费
            <select className={sel} value={cfg.speed} onChange={(e) => set({ speed: e.target.value as Speed })}>
              {(Object.keys(SOL_PRIORITY) as Speed[]).map((k) => <option key={k} value={k}>{SOL_PRIORITY[k].label}</option>)}
            </select>
          </label>
        : <div className={row}><span>Gas</span><span className="text-right">由钱包估算，可在钱包确认时调整</span></div>}
      <div className={row}><span>防夹（Anti-MEV）</span><span className="text-right">{token.chain === 'solana' ? 'Jupiter 公共通道不提供，未开启' : 'LI.FI 报价通道不提供，未开启'}</span></div>
      <label className={row}>快速买入金额（{net.native}）
        <input className={input} inputMode="decimal" value={quick.amounts[net.native] ?? ''} onChange={(e) => quick.setAmount(net.native, e.target.value.replace(/[^\d.]/g, ''))} />
      </label>
      <label className={row}>单笔上限（{net.native}，0 不限）
        <input className={input} inputMode="decimal" value={cfg.maxTradeNative} onChange={(e) => set({ maxTradeNative: Number(e.target.value.replace(/[^\d.]/g, '')) || 0 })} />
      </label>
      <label className={row}>最低流动性（USD，0 不限）
        <input className={input} inputMode="numeric" value={cfg.minLiquidityUsd} onChange={(e) => set({ minLiquidityUsd: Number(e.target.value.replace(/[^\d]/g, '')) || 0 })} />
      </label>
      <div className={row}><span>失败重试</span><span className="text-right">不自动重试，每次都要你重新确认</span></div>
    </div>
  )
}
