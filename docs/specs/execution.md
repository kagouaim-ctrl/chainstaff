# SPEC · 执行层、审批、周报和预警

## 付款执行（绿）

1. 规则引擎给出 AUTO_PAY 后，先把请求写进账本（状态 `executing`），再调用执行器。
2. 执行器把金额精确换算成最小单位：ERC-20 调用 `transfer(to, amount)`，BNB 直接转账。
3. 发送前用 `eth_estimateGas` 模拟，失败就不签名。
4. 成功记 `paid` 和交易哈希；失败记 `failed`，失败的请求不占每日额度，也不算重复。
5. 没配置钱包时用测试执行器，不上链，回复里明确写「测试模式」。

## Safe 提案（黄）

- 生成老板 Safe 里要执行的 SafeTx（只用 CALL，不用 DELEGATECALL），按 EIP-712 算出 safeTxHash，和 eth_account 的独立实现交叉验证过。
- 目前生成的是草稿：哈希写进账本和回复。老板在 Safe 里签字执行后发 `/approve 编号`，不付就发 `/reject 编号`。
- 自动推送到 Safe Transaction Service 要先把员工地址登记为 Safe delegate，并确认 Safe 在 BSC 的当前支持，放到下一步。

## 状态

| 结果 | 状态流转 |
|---|---|
| 绿 | executing → paid / failed |
| 黄 | pending_approval → approved / rejected |
| 红 | blocked |

每日额度只算 executing 和 paid。重复检测看 executing、paid、pending_approval、approved。

## 周报

每周一 9 点（老板时区）发上周报表：请求数、三种结果、按币种和类别的支出、被拦截的请求、待签字的请求；配置了国库地址时附余额和按本周速度能撑几个月。

## 预警

每 5 分钟拉一次国库地址的转账（Etherscan V2 多链接口），三类预警，每条只发一次：

- 大额转出：单笔达到 `alert_outflow_threshold`。
- 陌生对手方：国库转给白名单和国库以外的地址。
- 地址投毒：陌生地址给国库转账，且它和白名单地址的前 6 位、后 4 位相同。
