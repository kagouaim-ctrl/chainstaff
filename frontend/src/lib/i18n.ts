import { create } from 'zustand'
import { EN, EN_PATTERNS } from './en'

/**
 * 界面语言。中文是原文；选英文时，页面上的文字按 en.ts 的词表替换，词表里没有的保持中文。
 * 做法是在渲染后替换文字节点和 placeholder / aria-label / title，不改各个组件；
 * 只改节点的文字内容、不增删节点，React 下次更新同一段文字时会再替换一次。
 * 切回中文时刷新页面。
 */
export type Lang = 'zh' | 'en'
const KEY = 'kagouai.lang'

function initial(): Lang {
  try {
    const q = new URLSearchParams(location.search).get('lang') ?? new URLSearchParams(location.hash.split('?')[1] ?? '').get('lang')
    if (q === 'en' || q === 'zh') return q
    const saved = localStorage.getItem(KEY)
    if (saved === 'en' || saved === 'zh') return saved
  } catch { /* 读不到就用中文 */ }
  return 'zh'
}

export const useLang = create<{ lang: Lang; set: (l: Lang) => void }>((set) => ({
  lang: initial(),
  set: (lang) => {
    try { localStorage.setItem(KEY, lang) } catch { /* 隐私模式不记 */ }
    set({ lang })
    if (lang === 'zh') location.reload()
    else startTranslating()
  },
}))

const UNITS: Record<string, number> = { 万: 1e4, 亿: 1e8 }
const compact = (n: number) => n >= 1e9 ? `${+(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${+(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(2)}K` : `${+n.toFixed(2)}`

/** 一段中文文字的英文；没有对应时原样返回 */
export function toEn(text: string): string {
  const core = text.trim()
  if (!core || !/[一-鿿]/.test(core)) return text
  let out = EN[core]
  if (out == null) {
    for (const [re, to] of EN_PATTERNS) if (re.test(core)) { out = core.replace(re, to); break }
  }
  if (out == null) out = core
  // 数字里的「万」「亿」换成 K / M / B
  out = out.replace(/(\d+(?:\.\d+)?)(万|亿)/g, (_, n: string, u: string) => compact(Number(n) * UNITS[u]))
  return out === core ? text : text.replace(core, out)
}

/** 给 document.title 等非 DOM 文本用 */
export const tr = (s: string) => (useLang.getState().lang === 'en' ? toEn(s) : s)

const ATTRS = ['placeholder', 'aria-label', 'title']
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'CODE'])

function translateTree(root: Node) {
  if (root.nodeType === Node.TEXT_NODE) {
    const v = root.nodeValue ?? ''
    const t = toEn(v)
    if (t !== v) root.nodeValue = t
    return
  }
  if (!(root instanceof Element) || SKIP.has(root.tagName) || root.closest('[data-no-i18n]')) return
  for (const a of ATTRS) {
    const v = root.getAttribute(a)
    if (v) { const t = toEn(v); if (t !== v) root.setAttribute(a, t) }
  }
  for (const c of Array.from(root.childNodes)) translateTree(c)
}

let observer: MutationObserver | null = null
export function startTranslating() {
  if (observer || typeof MutationObserver === 'undefined') return
  document.documentElement.lang = 'en'
  translateTree(document.body)
  if (document.title) document.title = toEn(document.title)
  observer = new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === 'characterData') translateTree(m.target)
      else if (m.type === 'attributes') translateTree(m.target)
      else m.addedNodes.forEach(translateTree)
    }
  })
  observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
}

if (typeof window !== 'undefined' && useLang.getState().lang === 'en') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startTranslating)
  else startTranslating()
}
