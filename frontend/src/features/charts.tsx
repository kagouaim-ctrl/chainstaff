import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, CrosshairMode, type IChartApi, type UTCTimestamp, type SeriesMarker, type Time } from 'lightweight-charts'
import type { Candle, ChartMarker } from '@/domain/types'

/** 价格保留 4 位有效数字；市值这类大数缩写成 K / M / B。 */
const fp = (p: number) => {
  const a = Math.abs(p)
  if (a >= 1e9) return `${(p / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(p / 1e6).toFixed(2)}M`
  if (a >= 1e4) return `${(p / 1e3).toFixed(1)}K`
  return a >= 1 ? p.toFixed(4) : p.toPrecision(4)
}

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || '#888'

function baseChart(el: HTMLElement, height: number): IChartApi {
  return createChart(el, {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: css('--c-sub'), fontFamily: '"Inter Tight", sans-serif', fontSize: 11 },
    grid: { vertLines: { color: 'rgba(203,208,235,0.04)' }, horzLines: { color: 'rgba(203,208,235,0.04)' } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.14, bottom: 0.22 } },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 7, minBarSpacing: 2 },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: 'rgba(152,153,163,0.5)', width: 1, style: 3, labelBackgroundColor: '#161522' },
      horzLine: { color: 'rgba(152,153,163,0.5)', width: 1, style: 3, labelBackgroundColor: '#161522' },
    },
    handleScale: { axisPressedMouseMove: { time: true, price: true } },
    localization: { priceFormatter: (p: number) => fp(p) },
  })
}

const MARKER_STYLE: Record<ChartMarker['kind'], { color: string; shape: SeriesMarker<Time>['shape']; position: SeriesMarker<Time>['position'] }> = {
  discovered: { color: '#516AF6', shape: 'circle', position: 'belowBar' },
  no_trade: { color: '#9899A3', shape: 'square', position: 'aboveBar' },
  risk_rejected: { color: '#FF622E', shape: 'square', position: 'aboveBar' },
  buy: { color: '#21C95E', shape: 'arrowUp', position: 'belowBar' },
  sell: { color: '#FF622E', shape: 'arrowDown', position: 'aboveBar' },
  dev_buy: { color: '#F5A524', shape: 'circle', position: 'belowBar' },
  dev_sell: { color: '#F5A524', shape: 'circle', position: 'aboveBar' },
}

/** K 线 + Agent 决策点（发现、NO_TRADE、风控否决、买卖）。 */

const fv = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(0))

/** 左上角的开高低收图例：鼠标在哪根 K 线就显示哪根，移出图表回到最新一根（和 TradingView 一样）。 */
function Legend({ c, prev, label }: { c?: Candle; prev?: Candle; label?: string }) {
  if (!c) return null
  const chg = prev ? ((c.close - prev.close) / prev.close) * 100 : 0
  const tone = c.close >= c.open ? 'text-up' : 'text-down'
  return (
    <div className="pointer-events-none absolute left-3 top-2 z-10 flex flex-wrap items-center gap-x-2.5 text-[12px] font-normal">
      {label && <span className="text-sub">{label}</span>}
      {([['开', c.open], ['高', c.high], ['低', c.low], ['收', c.close]] as const).map(([k, v]) => (
        <span key={k} className="num"><span className="text-sub">{k}</span> <span className={tone}>{fp(v)}</span></span>
      ))}
      <span className={`num ${chg >= 0 ? 'text-up' : 'text-down'}`}>{chg >= 0 ? '+' : ''}{chg.toFixed(2)}%</span>
      <span className="num"><span className="text-sub">量</span> {fv(c.volume)}</span>
    </div>
  )
}

/** 简单移动平均和指数移动平均，周期不够的地方不画。 */
export function sma(xs: number[], n: number): (number | null)[] {
  let sum = 0
  return xs.map((x, i) => { sum += x; if (i >= n) sum -= xs[i - n]; return i >= n - 1 ? sum / n : null })
}
export function ema(xs: number[], n: number): (number | null)[] {
  const k = 2 / (n + 1)
  let prev: number | null = null
  return xs.map((x, i) => {
    if (i < n - 1) return null
    prev = prev == null ? xs.slice(0, n).reduce((a, b) => a + b, 0) / n : x * k + prev * (1 - k)
    return prev
  })
}

export interface Indicators { ma20: boolean; ema50: boolean }

/**
 * 数据刷新时图表会重建；viewKey 不变（同一代币、同一周期）时保留用户拖动和缩放的位置，
 * 秒级 K 线每 3 秒刷新也不会跳回去。
 */
export function CandleChart({ candles, markers, height = 360, label, indicators = { ma20: false, ema50: false }, viewKey }: { candles: Candle[]; markers: ChartMarker[]; height?: number; label?: string; indicators?: Indicators; viewKey?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const view = useRef<{ key?: string; range: { from: number; to: number } | null; atEnd: boolean }>({ range: null, atEnd: true })
  const [hover, setHover] = useState<number | null>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = baseChart(ref.current, height)
    const s = chart.addCandlestickSeries({
      upColor: css('--c-up'), downColor: css('--c-down'), borderVisible: false, priceLineStyle: 2, wickUpColor: css('--c-up'), wickDownColor: css('--c-down'),
      priceFormat: { type: 'price', precision: 10, minMove: 1e-10 },
    })
    s.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))
    const v = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' } })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    v.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, value: c.volume, color: c.close >= c.open ? 'rgba(33,201,94,0.25)' : 'rgba(255,98,46,0.25)' })))
    const closes = candles.map((c) => c.close)
    const line = (vals: (number | null)[], color: string) => {
      const l = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      l.setData(candles.flatMap((c, i) => vals[i] == null ? [] : [{ time: c.time as UTCTimestamp, value: vals[i]! }]))
    }
    if (indicators.ma20) line(sma(closes, 20), '#F5B544')
    if (indicators.ema50) line(ema(closes, 50), '#516AF6')
    s.setMarkers([...markers].sort((a, b) => a.time - b.time).map((m) => ({ time: m.time as UTCTimestamp, text: m.label, ...MARKER_STYLE[m.kind] })))
    const saved = view.current
    if (viewKey && saved.key === viewKey && saved.range && !saved.atEnd) chart.timeScale().setVisibleLogicalRange(saved.range)
    else if (viewKey && saved.key === viewKey && saved.range) {
      // 停在最新一根时，跟着新 K 线往右走，缩放比例不变
      const width = saved.range.to - saved.range.from
      chart.timeScale().setVisibleLogicalRange({ from: candles.length - 1 - width + 8, to: candles.length - 1 + 8 })
    } else chart.timeScale().fitContent()
    view.current = { key: viewKey, range: chart.timeScale().getVisibleLogicalRange(), atEnd: true }
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r) view.current = { key: viewKey, range: { from: r.from, to: r.to }, atEnd: r.to >= candles.length - 1 }
    })
    const index = new Map(candles.map((c, i) => [c.time, i]))
    chart.subscribeCrosshairMove((p) => setHover(p.time != null ? index.get(p.time as number) ?? null : null))
    return () => chart.remove()
  }, [candles, markers, height, indicators.ma20, indicators.ema50, viewKey])
  const i = hover ?? candles.length - 1
  return (
    <div className="relative">
      <Legend c={candles[i]} prev={candles[i - 1]} label={label} />
      <div ref={ref} className="w-full" style={{ height }} role="img" aria-label="K 线图" />
    </div>
  )
}

export function EquityChart({ data, height = 220 }: { data: { time: number; value: number }[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = baseChart(ref.current, height)
    chart.applyOptions({ localization: { priceFormatter: (p: number) => `$${p.toFixed(0)}` } })
    const s = chart.addAreaSeries({ lineColor: css('--c-paper'), topColor: 'rgba(245,181,68,0.18)', bottomColor: 'rgba(245,181,68,0)', lineWidth: 2 })
    s.setData(data.map((d) => ({ time: d.time as UTCTimestamp, value: d.value })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data, height])
  return <div ref={ref} className="w-full" style={{ height }} role="img" aria-label="资金曲线（模拟盘）" />
}
