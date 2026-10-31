import type { OrderStatus } from './types'

/** 订单状态机（计划第 4 节）。前端用它渲染时间线，并在测试中保证展示的流转都合法。 */
export const TRANSITIONS: Record<OrderStatus, OrderStatus[]> = {
  CREATED: ['VALIDATING'],
  VALIDATING: ['REJECTED', 'READY'],
  READY: ['SUBMITTED', 'EXPIRED'],
  SUBMITTED: ['PENDING', 'EXPIRED', 'UNKNOWN'],
  PENDING: ['CONFIRMED', 'FAILED', 'UNKNOWN'],
  UNKNOWN: ['CONFIRMED', 'FAILED'],
  REJECTED: [],
  CONFIRMED: [],
  FAILED: [],
  EXPIRED: [],
}

export const TERMINAL: OrderStatus[] = ['REJECTED', 'CONFIRMED', 'FAILED', 'EXPIRED']

export function canTransition(from: OrderStatus, to: OrderStatus): boolean {
  return TRANSITIONS[from].includes(to)
}

export function isValidPath(path: OrderStatus[]): boolean {
  if (path[0] !== 'CREATED') return false
  for (let i = 1; i < path.length; i++) if (!canTransition(path[i - 1], path[i])) return false
  return true
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  CREATED: '已创建', VALIDATING: '校验中', REJECTED: '已拒绝', READY: '待提交', SUBMITTED: '已提交',
  PENDING: '待确认', CONFIRMED: '已确认', FAILED: '失败', EXPIRED: '已过期', UNKNOWN: '状态未知',
}

/** 提交 ≠ 成交：只有 CONFIRMED 才算成交。 */
export function isFilled(s: OrderStatus): boolean {
  return s === 'CONFIRMED'
}

export type Tone = 'up' | 'down' | 'warn' | 'sub' | 'agent'
export const STATUS_TONE: Record<OrderStatus, Tone> = {
  CREATED: 'sub', VALIDATING: 'agent', READY: 'agent', SUBMITTED: 'agent', PENDING: 'warn',
  CONFIRMED: 'up', REJECTED: 'down', FAILED: 'down', EXPIRED: 'sub', UNKNOWN: 'warn',
}
