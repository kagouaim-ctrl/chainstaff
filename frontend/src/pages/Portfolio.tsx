import { Link } from 'react-router-dom'
import { useClosed, usePortfolio, usePositions, now } from '@/api/hooks'
import { DataState, Panel, Stat, Signed, TokenAvatar, ModeTag, Tag, Pct } from '@/components/ui'
import { EquityChart } from '@/features/charts'
import { usd, price, relTime, clock } from '@/lib/format'

export function Portfolio() {
  const pf = usePortfolio(); const pos = usePositions(); const closed = useClosed()
  return (
    <div className="space-y-4">
      <Panel demo title="持仓" right={<ModeTag mode="paper" />}>
        <DataState q={pf} rows={3}>
          {(p) => (
            <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
              <div className="grid grid-cols-2 gap-5 lg:grid-cols-1">
                <Stat label="总资产（模拟）" value={usd(p.totalUsd)} />
                <Stat label="可用余额" value={usd(p.cashUsd)} />
                <Stat label="已实现盈亏" value={<Signed v={p.realizedUsd}>{usd(p.realizedUsd, { sign: true })}</Signed>} sub="已最终确认、已扣全部费用" />
                <Stat label="未实现盈亏（估算）" value={<Signed v={p.unrealizedUsd}>{usd(p.unrealizedUsd, { sign: true })}</Signed>} sub="未扣除卖出成本" />
              </div>
              <div className="min-w-0">
                <div className="mb-1 flex items-center gap-2 text-xs text-sub">资金曲线 <ModeTag mode="paper" /> <span>近 6 天 · 只含模拟盘，不与实盘、回测合并</span></div>
                <EquityChart data={p.equity} />
              </div>
            </div>
          )}
        </DataState>
      </Panel>

      <Panel demo title="当前持仓" bodyClass="p-0">
        <DataState q={pos} isEmpty={(d) => d.length === 0} empty="没有持仓。只有链上（模拟盘为模拟引擎）确认成交后才会出现在这里。">
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-b border-line">
                  {['代币', '数量', '均价成本', '现价', '市值', '未实现盈亏（估算）', '止盈 / 止损', '对账'].map((h, i) => <th key={h} className={`px-4 py-2.5 font-normal ${i ? 'text-right' : 'text-left'}`}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.token.address} className="border-b border-line last:border-0 hover:bg-raised">
                      <td className="px-4 py-3"><Link to={`/token/${r.token.address}`} className="flex items-center gap-2.5"><TokenAvatar symbol={r.token.symbol} hue={r.token.hue} size={28} /><span className="font-medium">{r.token.symbol}</span><ModeTag mode={r.mode} /></Link></td>
                      <td className="num px-4 text-right">{r.amount.toLocaleString()}</td>
                      <td className="num px-4 text-right">{usd(r.costBasisUsd)}</td>
                      <td className="num px-4 text-right">{price(r.priceUsd)}</td>
                      <td className="num px-4 text-right">{usd(r.valueUsd)}</td>
                      <td className="px-4 text-right"><Signed v={r.unrealizedUsd}>{usd(r.unrealizedUsd, { sign: true })}</Signed> <Pct v={(r.unrealizedUsd / r.costBasisUsd) * 100} className="text-xs" digits={1} /></td>
                      <td className="num px-4 text-right text-xs"><span className="text-up">+{r.takeProfit}%</span> / <span className="text-down">{r.stopLoss}%</span></td>
                      <td className="px-4 text-right">{r.reconciliation === 'ok' ? <Tag tone="up">一致</Tag> : r.reconciliation === 'pending' ? <Tag tone="warn" title="有卖单待确认，阻止重复卖出">待核实</Tag> : <Tag tone="down">不一致，已暂停</Tag>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="px-4 py-2 text-[11px] text-sub">开仓于：{rows.map((r) => `${r.token.symbol} ${relTime(r.openedAt, now())}`).join(' · ')}</p>
            </div>
          )}
        </DataState>
      </Panel>

      <Panel demo title="已平仓" bodyClass="p-0">
        <DataState q={closed} isEmpty={(d) => d.length === 0} empty="还没有已平仓的交易。">
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-[13px]">
                <thead className="text-xs text-sub"><tr className="border-b border-line">
                  {['代币', '已实现盈亏', '费用', '开仓', '平仓', '最终性'].map((h, i) => <th key={h} className={`px-4 py-2.5 font-normal ${i ? 'text-right' : 'text-left'}`}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.token.address + r.closedAt} className="border-b border-line last:border-0">
                      <td className="px-4 py-3"><span className="flex items-center gap-2.5"><TokenAvatar symbol={r.token.symbol} hue={r.token.hue} size={24} />{r.token.symbol}<ModeTag mode={r.mode} /></span></td>
                      <td className="px-4 text-right"><Signed v={r.realizedUsd}>{usd(r.realizedUsd, { sign: true })}</Signed></td>
                      <td className="num px-4 text-right">{usd(r.feesUsd)}</td>
                      <td className="num px-4 text-right text-sub">{clock(r.openedAt)}</td>
                      <td className="num px-4 text-right text-sub">{clock(r.closedAt)}</td>
                      <td className="px-4 text-right">{r.finalized ? <Tag tone="up">已最终确认</Tag> : <Tag tone="warn">待最终确认</Tag>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DataState>
      </Panel>
    </div>
  )
}
