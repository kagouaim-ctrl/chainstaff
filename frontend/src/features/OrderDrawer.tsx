import { Link } from 'react-router-dom'
import { useOrder } from '@/api/hooks'
import { Drawer, DataState, ModeTag, Tag, KV, TokenAvatar, cx } from '@/components/ui'
import { DecisionChain } from './DecisionChain'
import { STATUS_LABEL, STATUS_TONE, isFilled } from '@/domain/orderMachine'
import { clock, price, usd } from '@/lib/format'
import { ICheck, IX } from '@/components/icons'

export function OrderDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useOrder(id)
  return (
    <Drawer open={!!id} onClose={onClose} wide title={<span className="flex items-center gap-2">交易详情 <span className="font-mono text-sm text-sub">{id}</span></span>}>
      <DataState q={q} empty={`没有找到订单 ${id}`}>
        {(o) => o && (
          <div className="space-y-6 p-5">
            <div className="flex flex-wrap items-center gap-3">
              <TokenAvatar symbol={o.token.symbol} hue={o.token.hue} size={40} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-lg font-medium">
                  <span className={o.side === 'BUY' ? 'text-up' : 'text-down'}>{o.side === 'BUY' ? '买入' : '卖出'}</span>
                  <Link to={`/token/${o.token.address}`} className="hover:underline" onClick={onClose}>{o.token.symbol}</Link>
                  <ModeTag mode={o.mode} />
                  <Tag tone={STATUS_TONE[o.status]}>{STATUS_LABEL[o.status]}</Tag>
                  <Tag tone="sub" className="border border-dashed border-sub/50">演示</Tag>
                </div>
                <div className="text-xs text-sub">Solana · 创建于 {clock(o.createdAt)}</div>
              </div>
            </div>

            {o.failureReason && <p className="rounded-lg border border-down/30 bg-down/10 p-3 text-[13px] text-down">失败原因：{o.failureReason}</p>}
            {o.status === 'UNKNOWN' && <p className="rounded-lg border border-warn/30 bg-warn/10 p-3 text-[13px] text-warn">状态未知：对账任务正在查询签名状态；期间同币同方向的新指令会被风控拒绝，不会重发。</p>}
            {!isFilled(o.status) && ['SUBMITTED', 'PENDING'].includes(o.status) && <p className="rounded-lg border border-line p-3 text-[13px] text-sub">已提交但尚未确认：提交不等于成交，确认前不更新持仓。</p>}

            <section className="grid gap-x-8 sm:grid-cols-2">
              <KV k="指令 ID" v={<span className="font-mono text-xs">{o.id}</span>} />
              <KV k="幂等键" v={<span className="font-mono text-xs">{o.idempotencyKey}</span>} />
              <KV k="请求金额" v={usd(o.requestedUsd)} />
              <KV k="请求数量" v={o.requestedAmount?.toLocaleString() ?? '--'} />
              <KV k="实际成交数量" v={o.filledAmount?.toLocaleString() ?? '未成交'} />
              <KV k="参考价格" v={price(o.referencePrice)} />
              <KV k="实际成交价格" v={o.fillPrice ? price(o.fillPrice) : '--'} />
              <KV k="最终性" v={o.finality === 'finalized' ? '已最终确认（计入已实现盈亏）' : o.finality === 'confirmed' ? '已确认，待最终确认' : '--'} />
              <KV k="交易签名" v={o.txSignature ?? <span className="text-sub">模拟盘无链上签名</span>} />
            </section>

            <section>
              <h3 className="mb-2 text-sm font-medium">费用</h3>
              {o.fees ? (
                <div className="rounded-lg border border-line px-3">
                  <KV k="网络费" v={usd(o.fees.networkUsd)} />
                  <KV k="优先费" v={usd(o.fees.priorityUsd)} />
                  <KV k="滑点成本" v={usd(o.fees.slippageUsd)} />
                  <KV k="平台手续费（含 AI 费用）" v={<span>{usd(o.fees.platformUsd)} <span className="text-xs text-sub">费率待定，占位 0.5%</span></span>} />
                  <KV k="合计" className="border-t border-line font-medium" v={usd(o.fees.networkUsd + o.fees.priorityUsd + o.fees.slippageUsd + o.fees.platformUsd)} />
                </div>
              ) : <p className="text-[13px] text-sub">未成交，没有实际费用。</p>}
            </section>

            <section>
              <h3 className="mb-2 text-sm font-medium">状态时间线</h3>
              <ol className="space-y-2">
                {o.events.map((e, i) => (
                  <li key={i} className="flex items-start gap-3 text-[13px]">
                    <span className="num w-[108px] shrink-0 text-xs text-sub">{clock(e.at)}</span>
                    <Tag tone={STATUS_TONE[e.status]} className="w-16 justify-center">{STATUS_LABEL[e.status]}</Tag>
                    <span className="text-fg/85">{e.reason}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-medium">
                风控审批 <Tag tone={o.risk.result === 'APPROVED' ? 'up' : 'down'}>{o.risk.result}</Tag>
              </h3>
              <p className="mb-2 text-xs text-sub">审批 {o.risk.id} · {clock(o.risk.decidedAt)} · 有效期至 {clock(o.risk.validUntil)} · 参数哈希 <span className="font-mono">{o.risk.paramsHash}</span></p>
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full text-[13px]">
                  <thead className="text-xs text-sub"><tr className="border-b border-line"><th className="px-3 py-2 text-left font-normal">规则</th><th className="px-3 py-2 text-left font-normal">上限</th><th className="px-3 py-2 text-right font-normal">实际</th><th className="px-3 py-2"></th></tr></thead>
                  <tbody>
                    {o.risk.checks.map((c) => (
                      <tr key={c.rule} className="border-b border-line last:border-0">
                        <td className="px-3 py-2">{c.rule}</td><td className="px-3 py-2 text-sub">{c.limit}</td>
                        <td className="num px-3 py-2 text-right">{c.actual}</td>
                        <td className={cx('px-3 py-2', c.pass ? 'text-up' : 'text-down')} aria-label={c.pass ? '通过' : '未通过'}>{c.pass ? <ICheck /> : <IX />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="mb-3 text-sm font-medium">决策链</h3>
              <DecisionChain steps={o.decisionChain} />
            </section>
          </div>
        )}
      </DataState>
    </Drawer>
  )
}
