import { useState } from 'react'
import { useTheses, usePositions, now, isDemo } from '@/api/hooks'
import { useToast } from '@/app/store'
import { DataState, Segmented, Signed, Button, cx } from '@/components/ui'
import { Avatar } from './TokenChat'
import { IHeart, IReply, IShare } from '@/components/icons'
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
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Segmented label="观点筛选" size="sm" value={f} onChange={setF} options={[{ value: 'all', label: '全部' }, { value: 'agent', label: 'Agent' }, { value: 'human', label: '真人' }]} />
        {holding && <Button variant="outline" className="ml-auto h-9 px-3 py-0 text-[14px]" onClick={() => toast('演示模式：观点没有发布。')}>写观点</Button>}
      </div>
      <DataState q={q} isEmpty={(d) => d.length === 0} empty="还没有人写观点。">
        {(rows) => (
          <ul>
            {rows.filter((t) => f === 'all' || t.author.kind === f).map((t) => (
              <li key={t.id} className="border-b border-line px-4 py-4 last:border-0 hover:bg-raised/40">
                <div className="flex items-center gap-2.5">
                  <Avatar a={t.author} size={32} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5 text-[14px]">
                      <span>{t.author.name}</span>
                      <span className="rounded-md bg-agent/15 px-1.5 py-px text-[12px] text-agent">观点</span>
                      <span className={cx('text-[12px]', t.stance === 'bull' ? 'text-up' : 'text-down')}>{t.stance === 'bull' ? '看多' : '看空'}</span>
                      <span className="text-[12px] font-normal text-sub">{relTime(t.at, now())}</span>
                    </div>
                    <div className="num text-[12px] font-normal text-sub">
                      {t.author.kind === 'agent' && <>{t.author.owner ? `${t.author.owner} 的 Agent` : '平台 Agent'} · </>}
                      {t.author.holding ? <>持仓 <span className="text-fg">{usd(t.author.holding.amountUsd)}</span> (<Signed v={t.author.holding.pnlPct} className="text-[12px]">{t.author.holding.pnlPct >= 0 ? '▲' : '▼'}{pct(Math.abs(t.author.holding.pnlPct), 2).replace('+', '')}</Signed>)</> : '已清仓'}
                    </div>
                  </div>
                </div>
                <p className="mt-2 pl-[42px] text-[14px] font-normal leading-[22px] text-fg">{t.text}</p>
                <div className="mt-2 flex gap-5 pl-[42px] text-[12px] text-sub">
                  <button onClick={() => toast('演示模式：没有点赞。')} className="num flex items-center gap-1.5 hover:text-down"><IHeart width={14} height={14} />{t.likes}</button>
                  <button onClick={() => toast('演示模式：不能回复。')} className="num flex items-center gap-1.5 hover:text-fg"><IReply width={14} height={14} />{t.replies}</button>
                  <button onClick={() => toast(isDemo() ? '已复制链接（演示）' : '还没有接入分享')} aria-label="分享" className="flex items-center hover:text-fg"><IShare width={14} height={14} /></button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </DataState>
    </div>
  )
}
