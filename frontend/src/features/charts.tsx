import { useEffect, useRef } from 'react'
import { createChart, ColorType, CrosshairMode, type IChartApi, type UTCTimestamp, type SeriesMarker, type Time } from 'lightweight-charts'
import type { Candle, ChartMarker } from '@/domain/types'

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || '#888'

function baseChart(el: HTMLElement, height: number): IChartApi {
  return createChart(el, {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: css('--c-sub'), fontFamily: '"Inter Tight", sans-serif', fontSize: 11 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
    rightPriceScale: { borderColor: css('--c-line') },
    timeScale: { borderColor: css('--c-line'), timeVisible: true, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
    localization: { priceFormatter: (p: number) => (p >= 1 ? p.toFixed(4) : p.toPrecision(4)) },
  })
}

const MARKER_STYLE: Record<ChartMarker['kind'], { color: string; shape: SeriesMarker<Time>['shape']; position: SeriesMarker<Time>['position'] }> = {
  discovered: { color: '#516AF6', shape: 'circle', position: 'belowBar' },
  no_trade: { color: '#9899A3', shape: 'square', position: 'aboveBar' },
  risk_rejected: { color: '#FF622E', shape: 'square', position: 'aboveBar' },
  buy: { color: '#21C95E', shape: 'arrowUp', position: 'belowBar' },
  sell: { color: '#FF622E', shape: 'arrowDown', position: 'aboveBar' },
}

/** K 线 + Agent 决策点（发现、NO_TRADE、风控否决、买卖）。 */
export function CandleChart({ candles, markers, height = 360 }: { candles: Candle[]; markers: ChartMarker[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = baseChart(ref.current, height)
    const s = chart.addCandlestickSeries({
      upColor: css('--c-up'), downColor: css('--c-down'), borderVisible: false, wickUpColor: css('--c-up'), wickDownColor: css('--c-down'),
      priceFormat: { type: 'price', precision: 10, minMove: 1e-10 },
    })
    s.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))
    const v = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' } })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    v.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, value: c.volume, color: c.close >= c.open ? 'rgba(33,201,94,0.25)' : 'rgba(255,98,46,0.25)' })))
    s.setMarkers(markers.map((m) => ({ time: m.time as UTCTimestamp, text: m.label, ...MARKER_STYLE[m.kind] })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [candles, markers, height])
  return <div ref={ref} className="w-full" style={{ height }} role="img" aria-label="K 线图，标注了 Agent 的发现、决策和交易点" />
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
