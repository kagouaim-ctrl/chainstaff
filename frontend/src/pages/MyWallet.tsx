import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, TokenAvatar, cx } from '@/components/ui'
import { usd, price, shortAddr } from '@/lib/format'
import { CHAIN_LABEL } from '@/api/chain'
import { useWallet } from '@/wallet/store'
import { NET, txUrl } from '@/wallet/chains'
import { readTrades } from '@/wallet/history'
import { nativeHolding, solanaHoldings } from '@/wallet/holdings'
import type { Chain } from '@/domain/types'

/**
 * 实时模式的「持仓」：连接钱包在链上的真实资产，不是模拟盘。
 * 没连钱包时不显示任何金额（不显示 $0.00）；读不到的价格显示「未知」，不算进总额。
 */
export function MyWallet() {
  const { solana, evm, openPicker } = useWallet()
  if (!solana && !evm) {
    return (
      <div className="mx-auto max-w-[520px] py-16 text-center">
        <h1 className="text-[20px] font-bold">持仓</h1>
        <p className="mt-2 text-[14px] font-normal text-sub">连接钱包后，这里显示你钱包在链上的真实持仓。资金一直在你自己的钱包里。</p>
        <div className="mt-6 flex justify-center gap-2">
          <Button variant="primary" className="h-11 px-5" onClick={() => openPicker('solana')}>连接 Solana 钱包</Button>
          <Button variant="outline" className="h-11 px-5" onClick={() => openPicker('evm')}>连接 EVM 钱包</Button>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-6">
      <h1 className="text-[20px] font-bold">持仓 <span className="ml-2 align-middle rounded-md bg-up/10 px-2 py-1 text-[12px] font-medium text-up">真实钱包</span></h1>
      {solana && <SolanaSection owner={solana.address} />}
      {evm && <EvmSection owner={evm.address} />}
      <MyTrades />
    </div>
  )
}

function Native({ chain, owner }: { chain: Chain; owner: string }) {
  const q = useQuery({ queryKey: ['nativeHolding', chain, owner], queryFn: () => nativeHolding(chain, owner), refetchInterval: 30_000 })
  return (
    <div className="rounded-lg border border-line px-3 py-2">
      <div className="text-[12px] font-normal text-sub">{CHAIN_LABEL[chain]} · {NET[chain].native}</div>
      <div className="num text-[14px]">{q.isError ? <span className="text-down">读取失败</span> : q.data ? <>{q.data.amount.toLocaleString('en-US', { maximumFractionDigits: 4 })} {NET[chain].native}<span className="ml-2 text-sub">{q.data.valueUsd != null ? `≈ ${usd(q.data.valueUsd)}` : '价格未知'}</span></> : '…'}</div>
    </div>
  )
}

function SolanaSection({ owner }: { owner: string }) {
  const q = useQuery({ queryKey: ['holdings', 'solana', owner], queryFn: () => solanaHoldings(owner), refetchInterval: 60_000 })
  const priced = (q.data ?? []).filter((h) => h.valueUsd != null)
  const total = priced.reduce((s, h) => s + h.valueUsd!, 0)
  const unknown = (q.data?.length ?? 0) - priced.length
  return (
    <section aria-label="Solana 持仓">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[16px] font-medium">Solana</h2>
        <span className="num text-[12px] text-sub">{shortAddr(owner, 4, 4)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Native chain="solana" owner={owner} />
        {q.data && <div className="rounded-lg border border-line px-3 py-2"><div className="text-[12px] font-normal text-sub">代币持仓估值</div><div className="num text-[14px]">{usd(total)}{unknown > 0 && <span className="ml-2 text-[12px] text-sub">另有 {unknown} 个价格未知，未计入</span>}</div></div>}
      </div>
      {q.isLoading && <p className="mt-3 text-[13px] text-sub">正在读取链上持仓…</p>}
      {q.isError && <p role="alert" className="mt-3 text-[13px] text-down">{(q.error as Error).message}</p>}
      {q.data && q.data.length === 0 && <p className="mt-3 text-[13px] text-sub">这个钱包没有持有 SPL 代币。</p>}
      {q.data && q.data.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-[13px]">
            <thead className="text-[12px] text-sub"><tr className="border-b border-line">
              {['代币', '数量', '参考价', '估值'].map((h, i) => <th key={h} className={cx('px-3 py-2 font-normal', i ? 'text-right' : 'text-left')}>{h}</th>)}
            </tr></thead>
            <tbody>
              {q.data.map((h) => (
                <tr key={h.address} className="h-11 border-b border-line last:border-0 hover:bg-raised">
                  <td className="px-3">
                    <Link to={`/token/${h.address}`} className="flex items-center gap-2">
                      <TokenAvatar image={h.info?.image} symbol={h.info?.symbol ?? '?'} hue={200} size={24} />
                      <span>{h.info?.symbol ?? shortAddr(h.address, 4, 4)}</span>
                      {h.info?.bad && <span className="rounded bg-warn/15 px-1 text-[11px] text-warn">数据异常</span>}
                    </Link>
                  </td>
                  <td className="num px-3 text-right">{h.amount.toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
                  <td className="num px-3 text-right">{h.info?.priceUsd != null ? price(h.info.priceUsd) : <span className="text-sub">未知</span>}</td>
                  <td className="num px-3 text-right">{h.valueUsd != null ? usd(h.valueUsd) : <span className="text-sub">未知</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-[11px] text-sub">持仓来自 Jupiter 的链上余额接口，价格来自 DexScreener 的主交易对。成本和盈亏需要完整的链上交易历史，下一阶段接入。</p>
    </section>
  )
}

function EvmSection({ owner }: { owner: string }) {
  return (
    <section aria-label="EVM 持仓">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[16px] font-medium">BSC / ETH / Robinhood Chain</h2>
        <span className="num text-[12px] text-sub">{shortAddr(owner, 4, 4)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {(['bsc', 'ethereum', 'robinhood'] as Chain[]).map((c) => <Native key={c} chain={c} owner={owner} />)}
      </div>
      <p className="mt-2 text-[11px] text-sub">列出 EVM 钱包的全部代币需要链上索引服务，正在接入。现在可以在代币页的买卖面板里看到你持有的这个代币数量。</p>
    </section>
  )
}

/** 在这台设备上通过 KAGOUAI 成交的交易。只存在浏览器里，以区块浏览器为准。 */
function MyTrades() {
  const rows = readTrades()
  return (
    <section aria-label="我的交易">
      <h2 className="text-[16px] font-medium">在本设备成交的交易</h2>
      {rows.length === 0
        ? <p className="mt-2 text-[13px] text-sub">还没有。通过买卖面板确认上链的交易会记在这里。</p>
        : (
          <ul className="mt-2 divide-y divide-line">
            {rows.slice(0, 50).map((t) => (
              <li key={t.id} className="flex h-11 items-center gap-3 text-[13px]">
                <span className={t.side === 'BUY' ? 'text-up' : 'text-down'}>{t.side === 'BUY' ? '买入' : '卖出'}</span>
                <Link to={`/token/${t.address}`} className="num hover:underline">{shortAddr(t.address, 4, 4)}</Link>
                <span className="text-sub">{CHAIN_LABEL[t.chain]}</span>
                <span className="num text-sub">{new Date(t.time).toLocaleString('zh-CN', { hour12: false })}</span>
                <a href={txUrl(t.chain, t.id)} target="_blank" rel="noreferrer" className="ml-auto text-agent hover:underline">区块浏览器</a>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}
