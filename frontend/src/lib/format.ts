/** 数字格式化：等宽数字由 .num 类负责，这里只管文本。 */

const SUBSCRIPT = '₀₁₂₃₄₅₆₇₈₉'

/** 中文大数缩写：万 / 亿。小于 1 万原样显示。 */
export function compactCN(n: number): string {
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  // 保留约 4 位有效数字：1.23万、12.3万、123.4万、1234万
  const fix = (v: number) => trim(v.toFixed(v >= 1000 ? 0 : v >= 100 ? 1 : 2))
  if (abs >= 1e8) return `${sign}${fix(abs / 1e8)}亿`
  if (abs >= 1e4) return `${sign}${fix(abs / 1e4)}万`
  return `${sign}${trim(abs.toFixed(abs >= 100 ? 0 : 2))}`
}

function trim(s: string): string {
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

/** 美元金额，>= 1 万用万/亿缩写。 */
export function usd(n: number | null | undefined, opts: { compact?: boolean; sign?: boolean } = {}): string {
  if (n == null || Number.isNaN(n)) return '--'
  const { compact = true, sign = false } = opts
  const prefix = sign && n > 0 ? '+' : n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (compact && abs >= 1e4) return `${prefix}$${compactCN(abs)}`
  if (abs > 0 && abs < 0.01) return `${prefix}$${Number(abs.toPrecision(2))}`
  return `${prefix}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * 代币价格。极小价格用下标零表示：0.00001234 → $0.0₄1234
 */
export function price(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '--'
  if (n === 0) return '$0'
  if (n >= 1) return `$${n.toLocaleString('en-US', { maximumFractionDigits: 4 })}`
  const s = n.toFixed(20)
  const m = /^0\.(0*)(\d{1,4})/.exec(s)
  if (!m) return `$${n}`
  const zeros = m[1].length
  if (zeros < 4) return `$${n.toPrecision(4).replace(/0+$/, '')}`
  const sub = String(zeros).split('').map((d) => SUBSCRIPT[Number(d)]).join('')
  return `$0.0${sub}${m[2].replace(/0+$/, '')}`
}

export function pct(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '--'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(digits)}%`
}

export function bps(n: number): string {
  return `${n} bps（${(n / 100).toFixed(2)}%）`
}

/** 代币年龄，单位分钟。 */
export function age(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} 分钟`
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} 小时`
  return `${Math.floor(minutes / 1440)} 天`
}

export function relTime(iso: string, now: Date): string {
  const diff = (now.getTime() - new Date(iso).getTime()) / 1000
  if (diff < 0) return '刚刚'
  if (diff < 60) return `${Math.floor(diff)} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

export function clock(iso: string): string {
  const d = new Date(iso)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function shortAddr(a: string, head = 4, tail = 4): string {
  return a.length <= head + tail + 1 ? a : `${a.slice(0, head)}…${a.slice(-tail)}`
}

export function duration(ms: number): string {
  const m = Math.floor(ms / 60000)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} 小时 ${m % 60} 分`
  return `${Math.floor(h / 24)} 天 ${h % 24} 小时`
}

/** 把浏览器的英文网络错误换成用户看得懂的话；其他错误原样返回。 */
export function errText(e: unknown, fallback = '未知错误'): string {
  const m = e instanceof Error ? e.message : typeof e === 'string' ? e : ''
  if (!m) return fallback
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(m)) return '网络连不上服务器，稍后会自动重试'
  return m
}
