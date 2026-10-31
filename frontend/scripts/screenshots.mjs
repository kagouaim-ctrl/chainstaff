// 页面截图自查：每个页面 1440px 与 375px 各一张。先运行 `npm run build && npm run preview`。
import { createRequire } from 'node:module'
// 使用全局或本地安装的 playwright（CJS 解析会遵循 NODE_PATH）
const { chromium } = createRequire(import.meta.url)('playwright')
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE_URL ?? 'http://localhost:4173'
const OUT = process.env.OUT_DIR ?? 'screenshots'
mkdirSync(OUT, { recursive: true })

const ZAPP = process.env.TOKEN ?? ''
const pages = [
  ['discover', '/'], ['overview', '/overview'], ['agents', '/agents'], ['activity', '/activity'],
  ['portfolio', '/portfolio'], ['orders', '/orders'], ['strategy', '/strategy'],
  ['order-drawer', '/orders?order=ord_7f3a'], ['agent-drawer', '/agents?agent=risk'],
]

const browser = await chromium.launch()
const errors = []
for (const [w, h, tag] of [[1440, 900, 'd'], [375, 812, 'm']]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } })
  page.on('pageerror', (e) => errors.push(`${tag} ${page.url()}: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`${tag} ${page.url()}: ${m.text()}`) })
  const list = [...pages]
  if (ZAPP) list.push(['token', `/token/${ZAPP}`])
  for (const [name, path] of list) {
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(700)
    await page.screenshot({ path: `${OUT}/${tag}_${name}.png`, fullPage: true })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (overflow > 1) errors.push(`${tag} ${name}: 页面横向溢出 ${overflow}px`)
  }
  await page.close()
}
await browser.close()
if (errors.length) { console.log('问题：\n' + errors.join('\n')); process.exitCode = 1 } else console.log('全部截图完成，没有报错或横向溢出')
