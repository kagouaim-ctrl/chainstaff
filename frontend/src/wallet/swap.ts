/**
 * 报价和下单。Solana 走 Jupiter，BSC 和 Robinhood Chain 走 LI.FI。两者都不需要 key。
 * 平台手续费默认 0；定好比例和收款地址后在这里打开。
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 聚合器的原始返回 */
import type { Chain } from '@/domain/types'
import { NET } from './chains'
import { allowance, sendEvm, sendSolana, waitConfirmed } from './actions'

const JUP = 'https://lite-api.jup.ag/swap/v1'
const LIFI = 'https://li.quest/v1'
const WSOL = 'So11111111111111111111111111111111111111112'
const EVM_NATIVE = '0x0000000000000000000000000000000000000000'

export interface Quote {
  chain: Chain
  side: 'BUY' | 'SELL'
  inRaw: bigint
  outRaw: bigint
  minOutRaw: bigint
  priceImpactPct: number | null
  inUsd: number | null
  outUsd: number | null
  route: string
  raw: any
}

async function json(r: Response) {
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.message ?? body?.error ?? `报价失败（${r.status}）`)
  return body
}

/** side=BUY：用原生币买 token；SELL：把 token 卖成原生币。amount 是最小单位。 */
export async function getQuote(chain: Chain, side: 'BUY' | 'SELL', token: string, amount: bigint, slippageBps: number, from: string | null): Promise<Quote> {
  if (chain === 'solana') {
    const [inMint, outMint] = side === 'BUY' ? [WSOL, token] : [token, WSOL]
    const q = await json(await fetch(`${JUP}/quote?inputMint=${inMint}&outputMint=${outMint}&amount=${amount}&slippageBps=${slippageBps}`))
    return {
      chain, side, inRaw: BigInt(q.inAmount), outRaw: BigInt(q.outAmount), minOutRaw: BigInt(q.otherAmountThreshold),
      priceImpactPct: q.priceImpactPct != null ? Number(q.priceImpactPct) * 100 : null,
      inUsd: q.swapUsdValue != null && side === 'BUY' ? Number(q.swapUsdValue) : null, outUsd: q.swapUsdValue != null && side === 'SELL' ? Number(q.swapUsdValue) : null,
      route: (q.routePlan ?? []).map((s: any) => s.swapInfo?.label).filter(Boolean).join(' → ') || 'Jupiter', raw: q,
    }
  }
  const id = NET[chain].chainId
  const [fromToken, toToken] = side === 'BUY' ? [EVM_NATIVE, token] : [token, EVM_NATIVE]
  // 没连钱包时用一个占位地址只看报价，真正下单前会用钱包地址重新报价
  const addr = from ?? '0x1111111111111111111111111111111111111111'
  const q = await json(await fetch(`${LIFI}/quote?fromChain=${id}&toChain=${id}&fromToken=${fromToken}&toToken=${toToken}&fromAmount=${amount}&fromAddress=${addr}&slippage=${slippageBps / 10_000}`))
  const est = q.estimate ?? {}
  return {
    chain, side, inRaw: BigInt(est.fromAmount ?? amount), outRaw: BigInt(est.toAmount ?? 0), minOutRaw: BigInt(est.toAmountMin ?? 0),
    priceImpactPct: null, inUsd: est.fromAmountUSD != null ? Number(est.fromAmountUSD) : null, outUsd: est.toAmountUSD != null ? Number(est.toAmountUSD) : null,
    route: q.toolDetails?.name ?? q.tool ?? 'LI.FI', raw: q,
  }
}

export type Step = { stage: 'approve' | 'sign' | 'sent' | 'confirmed' | 'failed' | 'pending'; id?: string }

/** 执行一笔报价。每一步都要用户在钱包里确认；onStep 报告进度。 */
export async function execute(q: Quote, owner: string, onStep: (s: Step) => void, opts: { priority?: { level: string; maxLamports: number } } = {}): Promise<Step> {
  if (q.chain === 'solana') {
    const body = await json(await fetch(`${JUP}/swap`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        quoteResponse: q.raw, userPublicKey: owner, wrapAndUnwrapSol: true, dynamicComputeUnitLimit: true,
        prioritizationFeeLamports: { priorityLevelWithMaxLamports: { maxLamports: opts.priority?.maxLamports ?? 1_000_000, priorityLevel: opts.priority?.level ?? 'high' } },
      }),
    }))
    if (body.simulationError) throw new Error(`交易模拟失败：${body.simulationError.error ?? '未知原因'}`)
    onStep({ stage: 'sign' })
    const sig = await sendSolana(body.swapTransaction)
    onStep({ stage: 'sent', id: sig })
    const end = await waitConfirmed('solana', sig)
    const step: Step = { stage: end, id: sig }
    onStep(step)
    return step
  }
  // EVM：卖出前先确认授权额度够不够，不够就先让用户授权这一笔的数量
  const tx = q.raw.transactionRequest
  const spender = q.raw.estimate?.approvalAddress
  const fromToken = q.raw.action?.fromToken?.address
  if (q.side === 'SELL' && spender && fromToken && fromToken !== EVM_NATIVE) {
    const have = await allowance(q.chain, fromToken, owner, spender)
    if (have < q.inRaw) {
      onStep({ stage: 'approve' })
      const data = `0x095ea7b3${spender.replace(/^0x/, '').toLowerCase().padStart(64, '0')}${q.inRaw.toString(16).padStart(64, '0')}`
      const approveId = await sendEvm(q.chain, { to: fromToken, data })
      const ok = await waitConfirmed(q.chain, approveId)
      if (ok !== 'confirmed') { const s: Step = { stage: ok === 'failed' ? 'failed' : 'pending', id: approveId }; onStep(s); return s }
    }
  }
  onStep({ stage: 'sign' })
  const id = await sendEvm(q.chain, { to: tx.to, data: tx.data, value: tx.value, gasLimit: tx.gasLimit })
  onStep({ stage: 'sent', id })
  const end = await waitConfirmed(q.chain, id)
  const step: Step = { stage: end, id }
  onStep(step)
  return step
}
