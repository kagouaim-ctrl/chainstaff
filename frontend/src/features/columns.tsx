import { create } from 'zustand'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cx } from '@/components/ui'
import type { Timeframe, Token } from '@/domain/types'
import { usd, price, compactCN, age } from '@/lib/format'
import { ChangeCell, Masked } from './tokenBits'

/**
 * 发现页表格的列。用户可以显示、隐藏和调整顺序，设置存在浏览器本地。
 * 某一列在当前所有行里都没有数据时自动隐藏（不显示一整列「--」）。
 */
export type ColKey = 'price' | 'mcap' | 'change' | 'volume' | 'txns' | 'liquidity' | 'holders' | 'top10' | 'dev' | 'age'

export interface Col {
  key: ColKey
  label: (tf: Timeframe) => string
  /** 排序用的值；null 的排到最后 */
  value: (t: Token, tf: Timeframe) => number | null
  cell: (t: Token, tf: Timeframe) => ReactNode
  /** 这一列是否可能整列没有数据（没有就自动隐藏） */
  optional?: boolean
}

const pctCell = (v: number | null, warnAt: number) => v == null ? <span className="text-mute">--</span> : <span className={v > warnAt ? 'text-warn' : ''}>{v.toFixed(1)}%</span>

export const COLS: Record<ColKey, Col> = {
  price: { key: 'price', label: () => '价格', value: (t) => t.priceUsd, cell: (t) => <Masked t={t}>{price(t.priceUsd)}</Masked> },
  mcap: { key: 'mcap', label: () => '市值', value: (t) => t.mcapUsd, cell: (t) => <Masked t={t}>{usd(t.mcapUsd, { compact: true })}</Masked> },
  change: { key: 'change', label: (tf) => `${tf} 涨跌`, value: (t, tf) => t.stats[tf].change, cell: (t, tf) => <ChangeCell t={t} v={t.stats[tf].change} /> },
  volume: { key: 'volume', label: (tf) => `${tf} 成交额`, value: (t, tf) => t.stats[tf].volume, cell: (t, tf) => usd(t.stats[tf].volume, { compact: true }) },
  txns: {
    key: 'txns', label: (tf) => `${tf} 笔数`, value: (t, tf) => t.stats[tf].txns,
    cell: (t, tf) => <span title={`买 ${t.stats[tf].buyers} 笔 / 卖 ${t.stats[tf].sellers} 笔`}>{compactCN(t.stats[tf].txns)}<span className="ml-1 text-[11px] font-normal"><span className="text-up">{compactCN(t.stats[tf].buyers)}</span>/<span className="text-down">{compactCN(t.stats[tf].sellers)}</span></span></span>,
  },
  liquidity: { key: 'liquidity', label: () => '流动性', value: (t) => t.liquidityUsd, cell: (t) => usd(t.liquidityUsd, { compact: true }) },
  holders: { key: 'holders', label: () => '持有者', value: (t) => t.holders, cell: (t) => t.holders == null ? <span className="text-mute">--</span> : compactCN(t.holders), optional: true },
  top10: { key: 'top10', label: () => '前十持有', value: (t) => t.security.top10Pct, cell: (t) => pctCell(t.security.top10Pct, 30), optional: true },
  dev: { key: 'dev', label: () => '开发者持有', value: (t) => t.security.devHoldPct, cell: (t) => pctCell(t.security.devHoldPct, 5), optional: true },
  age: { key: 'age', label: () => '币龄', value: (t) => -t.ageMinutes, cell: (t) => <span className="font-normal text-sub">{age(t.ageMinutes)}</span> },
}

export const ALL_COLS: ColKey[] = ['price', 'mcap', 'change', 'volume', 'txns', 'liquidity', 'holders', 'top10', 'dev', 'age']
const DEFAULT: ColKey[] = ['price', 'mcap', 'change', 'volume', 'txns', 'liquidity', 'holders', 'top10']
const KEY = 'kagouai.columns.v1'

function read(): { cols: ColKey[]; dense: boolean } {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    if (v && Array.isArray(v.cols)) return { cols: v.cols.filter((c: string) => c in COLS), dense: !!v.dense }
  } catch { /* 读不到就用默认 */ }
  return { cols: DEFAULT, dense: false }
}

interface ColState {
  cols: ColKey[]
  dense: boolean
  toggle: (k: ColKey) => void
  move: (k: ColKey, d: -1 | 1) => void
  setDense: (v: boolean) => void
  reset: () => void
}

const save = (s: { cols: ColKey[]; dense: boolean }) => { try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* 隐私模式不记 */ } }

export const useColumns = create<ColState>((set, get) => ({
  ...read(),
  toggle: (k) => {
    const cols = get().cols.includes(k) ? get().cols.filter((c) => c !== k) : ALL_COLS.filter((c) => c === k || get().cols.includes(c))
    save({ cols, dense: get().dense }); set({ cols })
  },
  move: (k, d) => {
    const cols = [...get().cols]
    const i = cols.indexOf(k), j = i + d
    if (i < 0 || j < 0 || j >= cols.length) return
    ;[cols[i], cols[j]] = [cols[j], cols[i]]
    save({ cols, dense: get().dense }); set({ cols })
  },
  setDense: (dense) => { save({ cols: get().cols, dense }); set({ dense }) },
  reset: () => { save({ cols: DEFAULT, dense: false }); set({ cols: DEFAULT, dense: false }) },
}))

/** 当前要显示的列：用户选中的，去掉在这些行里整列没有数据的。 */
export function visibleCols(cols: ColKey[], rows: Token[], tf: Timeframe): Col[] {
  return cols.map((k) => COLS[k]).filter((c) => !c.optional || rows.some((t) => c.value(t, tf) != null))
}

/** 表格单元格：值变了就闪一下（涨绿跌红），第一次渲染不闪。 */
export function FlashTd({ v, className, children }: { v: number | null; className?: string; children: ReactNode }) {
  const prev = useRef(v)
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    const p = prev.current
    prev.current = v
    if (p == null || v == null || p === v) return
    setDir(v > p ? 'up' : 'down')
    setN((x) => x + 1)
  }, [v])
  return <td key={n} className={cx(className, dir && `flash-${dir}`)}>{children}</td>
}
