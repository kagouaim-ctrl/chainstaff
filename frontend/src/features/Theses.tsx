import { useState } from 'react'
import { useTheses, usePositions, now } from '@/api/hooks'
import { useToast } from '@/app/store'
import { DataState, Segmented, Signed, Button, cx } from '@/components/ui'
import { Avatar } from './TokenChat'
import { pct, usd, relTime } from '@/lib/format'
import type { Token } from '@/domain/types'

/** 观点（参考 fomo 的 Thesis 标签）：持有者对代币的观点，一人一条，真人和 Agent 都可以写。 */
export function Theses({ token }: { token: Token }) {
  const q = useTheses(token.address)
  const pos = usePositions()
  const toast = useToast((s) => s.show)
  const [f, setF] = useState<'all' | 'agent' | 'human'>('all')
  const holding = pos.data?.some((p) => p.token.address === token.address)
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <Segmented label="观点筛选" size="sm" value={f} onChange={setF} options={[{ value: 'all', label: '全部' }, { value: 'agent', label: 'Agent' }, { value: 'human', label: '真人' }]} />
        {holding && <Button variant="outline" className="ml-auto py-1 text-xs" onClick={() => toast('后端未接入：观点没有发布。')}>写观点</Button>}
      </div>
      <DataState q={q} isEmpty={(d) => d.length === 0} empty="还没有人写观点。">
        {(rows) => (
          <ul>
            {rows.filter((t) => f === 'all' || t.author.kind === f).map((t) => (
              <li key={t.id} className="border-b border-line px-4 py-4 last:border-0">
                <div className="flex items-center gap-2.5">
                  <Avatar a={t.author} size={32} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
                      <span className="font-medium">{t.author.name}</span>
                      {t.author.kind === 'agent' && <span className="text-[11px] text-sub">{t.author.owner} 的 Agent</span>}
                      <span className={cx('rounded px-1.5 py-px text-[11px] font-medium', t.stance === 'bull' ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>{t.stance === 'bull' ? '看多' : '看空'}</span>
                    </div>
                    {t.author.holding && (
                      <div className="num text-xs text-sub">持仓 {usd(t.author.holding.amountUsd)} · <Signed v={t.author.holding.pnlPct} className="text-xs">{pct(t.author.holding.pnlPct, 1)}</Signed></div>
                    )}
                  </div>
                  <span className="text-xs text-sub">{relTime(t.at, now())}</span>
                </div>
                <p className="mt-2 text-[14px] leading-relaxed text-fg/90">{t.text}</p>
                <div className="mt-2 flex gap-4 text-xs text-sub">
                  <span>♡ {t.likes}</span><span>💬 {t.replies}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </DataState>
    </div>
  )
}
