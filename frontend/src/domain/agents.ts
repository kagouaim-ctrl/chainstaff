import type { AgentRole, Impl } from './types'

/** 角色定义（来自计划第 3 节，是产品定义，不是演示数据）。 */
export interface RoleDef {
  role: AgentRole
  name: string
  short: string
  duty: string
  impl: Impl
  noModel?: boolean
  runMode: string
  upstream: AgentRole[]
  downstream: AgentRole[]
}

export const ROLES: RoleDef[] = [
  { role: 'scout', name: 'Scout 侦察', short: '侦察', duty: '监听新池子、价格、成交量、流动性异动；去重、过滤；产出候选代币', impl: 'code', runMode: '事件驱动 worker', upstream: [], downstream: ['onchain', 'security', 'sentiment', 'quant'] },
  { role: 'onchain', name: 'On-chain 链上分析', short: '链上', duty: '持仓集中度、前十持有占比、开发者钱包、资金流、捆绑买入与刷量识别', impl: 'code+model', runMode: '按候选触发', upstream: ['scout'], downstream: ['strategy'] },
  { role: 'security', name: 'Security 安全', short: '安全', duty: 'Mint / Freeze 权限、LP 锁定或销毁、转账税、卖出可行性、外部安全 API', impl: 'code+model', runMode: '按候选触发', upstream: ['scout'], downstream: ['strategy', 'risk'] },
  { role: 'sentiment', name: 'Sentiment 情绪', short: '情绪', duty: '社交热度、叙事分类、机器人与喊单识别（非关键数据，缺失时不阻断交易）', impl: 'model+code', runMode: '按候选触发，带缓存', upstream: ['scout'], downstream: ['strategy'] },
  { role: 'quant', name: 'Quant 量化', short: '量化', duty: '波动率、量价、成本与滑点估计、回测与复盘（只读已核实数据）', impl: 'code', runMode: '按候选触发 + 定时', upstream: ['scout'], downstream: ['strategy'] },
  { role: 'strategy', name: 'Strategy 策略 · 主 Agent', short: '策略', duty: '主 Agent 担任总策划：汇总研究、检查冲突，产出结构化交易方案或 NO_TRADE', impl: 'model+code', runMode: '按候选触发', upstream: ['onchain', 'security', 'sentiment', 'quant', 'position'], downstream: ['risk'] },
  { role: 'risk', name: 'Risk 风控', short: '风控', duty: '余额、敞口、单笔上限、日亏损、滑点、频率、重复交易、安全状态 → 批准 / 拒绝 / 暂停', impl: 'code', noModel: true, runMode: '同步服务', upstream: ['strategy', 'position'], downstream: ['execution'] },
  { role: 'execution', name: 'Execution 执行', short: '执行', duty: '校验审批 → 报价 → 签名 → 提交 → 追踪确认 → 解析实际成交（唯一执行入口）', impl: 'code', noModel: true, runMode: '独立服务 + 持久化状态机', upstream: ['risk'], downstream: ['position'] },
  { role: 'position', name: 'Position 持仓', short: '持仓', duty: '持仓、均价、盈亏、链上余额对账、止盈止损与退出评估', impl: 'code', noModel: true, runMode: '定时 + 价格事件', upstream: ['execution'], downstream: ['strategy', 'risk'] },
  { role: 'supervisor', name: 'Supervisor 总控', short: '总控', duty: '生命周期、心跳、超时、去重、防循环、决策链记录、故障暂停与恢复', impl: 'code', noModel: true, runMode: '常驻', upstream: [], downstream: [] },
]

export const ROLE_MAP: Record<AgentRole, RoleDef> = Object.fromEntries(ROLES.map((r) => [r.role, r])) as Record<AgentRole, RoleDef>

export const IMPL_LABEL: Record<Impl, string> = { code: '纯代码', 'code+model': '代码为主，模型写摘要', 'model+code': '模型 + 规则' }
