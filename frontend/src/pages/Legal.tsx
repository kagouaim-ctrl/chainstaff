import { Link, NavLink, useParams } from 'react-router-dom'
import { cx } from '@/components/ui'

/**
 * 条款、隐私、风险披露、费用和帮助。内容只写产品现在真实的做法：
 * 自托管、平台手续费 0、数据来自哪些公开接口、浏览器本地存了什么。产品行为变了，这里要一起改。
 */
type Doc = { title: string; updated: string; sections: { h: string; p: string[] }[] }

const UPDATED = '2026-11-07'

export const DOCS: Record<string, Doc> = {
  risk: {
    title: '风险披露', updated: UPDATED, sections: [
      { h: 'Meme 币风险极高', p: [
        'Meme 币价格波动极大，可能在几分钟内下跌 90% 以上，也可能归零。你可能损失全部投入。只用你完全亏得起的钱交易。',
        '大量 Meme 币存在貔貅盘（买入后卖不出去）、增发、冻结、开发者或内部人抛售、流动性被撤走（Rug）等风险。',
      ] },
      { h: '安全检查不是担保', p: [
        '页面上的安全检查来自 RugCheck、GoPlus 和 Jupiter 等第三方接口的自动分析。检查通过不代表代币安全，检查未通过也可能是数据缺失。标为「未知」的项目表示我们没有查到，不等于没有风险。',
      ] },
      { h: '数据可能出错', p: [
        '行情数据来自 DexScreener 和 GeckoTerminal 的公开接口，可能延迟、缺失或错误。我们会识别价格冲突的池子并把明显异常的数据标为「数据异常」，异常数据不展示数值、不能交易，但无法保证识别出所有错误。',
        '页面显示的价格是参考价，不是可成交价格。实际成交价以下单前的报价和链上结果为准。',
      ] },
      { h: '交易是真实的', p: [
        '买卖面板标有「真实钱包交易」：确认后会用你钱包里的真实资产在链上成交，链上交易无法撤销。',
        '网络拥堵、滑点、MEV（夹子）和路由失败都可能让成交价格比报价差，或让交易失败并扣除网络费。',
      ] },
      { h: 'AI Agent', p: [
        'AI Agent 自动交易尚未开放。AI 生成的研究内容可能出错，不构成投资建议。',
      ] },
    ],
  },
  terms: {
    title: '服务条款', updated: UPDATED, sections: [
      { h: '服务内容', p: [
        'KAGOUAI 提供多链 Meme 代币行情、安全检查和交易界面。交易通过第三方聚合器（Solana 上是 Jupiter，BSC、ETH、Robinhood Chain 上是 LI.FI）报价并生成交易，由你自己的钱包签名和发送。',
      ] },
      { h: '自托管', p: [
        '我们不保管你的资产，拿不到你的私钥或助记词，也无法替你发起、撤销或修改任何交易。每一笔交易都需要你在自己的钱包里确认。请不要把助记词或私钥告诉任何人，包括自称 KAGOUAI 的人。',
      ] },
      { h: '不构成建议', p: [
        '本网站的任何内容，包括行情、排行、安全检查和 AI 研究，都不构成投资、法律或税务建议。你对自己的交易决定负全部责任。',
      ] },
      { h: '使用限制', p: [
        '你需要自行确认在你所在的国家或地区使用本服务和交易相关代币是合法的。不得利用本服务从事洗钱、操纵市场或其他违法活动。',
      ] },
      { h: '第三方服务', p: [
        '行情、安全检查、报价和区块链节点由第三方提供。它们的可用性和准确性不在我们的控制范围内。',
      ] },
      { h: '变更', p: [
        '服务和本条款可能更新。重大变化会在页面上提示；继续使用即表示接受更新后的条款。',
      ] },
    ],
  },
  privacy: {
    title: '隐私', updated: UPDATED, sections: [
      { h: '我们不收集什么', p: [
        '应用没有账户系统，不设置跟踪 Cookie，不做统计分析，也不把你的钱包地址发送到我们的服务器。',
      ] },
      { h: '浏览器本地存储', p: [
        '自选列表、快速买入金额、你同意条款的记录，以及在这台设备上成交的交易哈希（用于在 K 线上标出你的买卖点），只保存在你浏览器的本地存储里。清除浏览器数据就会删除它们。',
      ] },
      { h: '你的浏览器直接访问的第三方', p: [
        '为了显示行情和完成交易，你的浏览器会直接请求 DexScreener、GeckoTerminal、RugCheck、GoPlus、Jupiter、LI.FI 和 publicnode 的公共节点。查询持仓和报价时，这些服务能看到你的钱包地址和 IP 地址，适用它们自己的隐私政策。',
      ] },
      { h: 'AI 研究', p: [
        '如果你把代币交给 AI 研究，代币地址会发送到 KAGOUAI 的分析服务，不包含你的钱包地址。',
      ] },
    ],
  },
  fees: {
    title: '费用', updated: UPDATED, sections: [
      { h: '平台手续费：0', p: [
        'KAGOUAI 目前不收取任何平台手续费。以后如果收费，会先在这里和交易面板上写明比例，并在报价里单独列出。',
      ] },
      { h: '你仍然要付的费用', p: [
        '网络费（Gas / 优先费）：付给区块链，交易失败也会扣除。',
        'DEX 交易费：由池子收取，已经包含在报价里。',
        '代币自身的买卖税：部分代币合约在买卖时扣税，安全检查里会显示查到的税率。',
        '滑点：实际成交价和报价之间的差，最多不超过你设置的最大滑点。',
      ] },
    ],
  },
  help: {
    title: '帮助', updated: UPDATED, sections: [
      { h: '怎么开始交易', p: [
        '1. 点右上角「连接钱包」。Solana 代币用 Phantom、OKX Wallet 或 Backpack；BSC、ETH、Robinhood Chain 代币用 MetaMask、Rabby、OKX Wallet 等 EVM 钱包。',
        '2. 打开代币页，在右侧买卖面板输入金额，或在列表里点快速买入。',
        '3. 核对报价里的「预计得到」「最少得到」和价格冲击，点买入，再到钱包里确认。',
        '4. 交易发出后面板会显示状态和区块浏览器链接。',
      ] },
      { h: '「数据异常」是什么意思', p: [
        '这个代币的池子数据自相矛盾，例如市值大于完全稀释估值、价格和其他池子相差很多、市值相对流动性大得离谱。这时价格和市值可能是错的，所以不展示，也不能交易。悬停在标记上可以看到具体原因。',
      ] },
      { h: '交易失败了怎么办', p: [
        '先点「查看交易」在区块浏览器里确认状态。失败的交易不会换走你的代币，只扣网络费。常见原因是价格变化超过最大滑点、余额不够付网络费，或代币本身限制卖出。',
      ] },
      { h: '联系我们', p: [
        '官网 kagouai.com 上有 Telegram 和 X 的联系方式。我们不会私信你索要助记词、私钥或让你转账。',
      ] },
    ],
  },
}

export const LEGAL_LINKS: [string, string][] = [['risk', '风险披露'], ['terms', '条款'], ['privacy', '隐私'], ['fees', '费用'], ['help', '帮助']]

export function Legal() {
  const { doc = 'help' } = useParams()
  const d = DOCS[doc]
  if (!d) return <p className="text-sub">没有这个页面。<Link to="/legal/help" className="text-agent">去帮助</Link></p>
  return (
    <div className="mx-auto max-w-[760px]">
      <nav aria-label="说明文档" className="scrollbar-none mb-6 flex gap-2 overflow-x-auto">
        {LEGAL_LINKS.map(([k, l]) => (
          <NavLink key={k} to={`/legal/${k}`} className={({ isActive }) => cx('h-9 shrink-0 rounded-lg border px-3 text-[14px] leading-9', isActive ? 'border-transparent bg-raised text-fg' : 'border-line text-sub hover:text-fg')}>{l}</NavLink>
        ))}
      </nav>
      <h1 className="text-[24px] font-bold">{d.title}</h1>
      <p className="mt-1 text-[12px] text-sub">更新于 {d.updated}</p>
      {d.sections.map((s) => (
        <section key={s.h} className="mt-6">
          <h2 className="text-[16px] font-medium">{s.h}</h2>
          {s.p.map((x) => <p key={x} className="mt-2 text-[14px] font-normal leading-relaxed text-fg/85">{x}</p>)}
        </section>
      ))}
    </div>
  )
}
