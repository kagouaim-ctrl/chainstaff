# AI 员工平台（Workforce）

一个工作区里有一位 AI 主管和若干 AI 员工。用户把目标交给主管，主管拆成带依赖的子任务，分给有对应技能的员工并行执行；员工之间通过持久化消息交接结果；最后汇总成报告交给人审批。审批通过可以把报告哈希写到 BSC 测试网。

模型只在链下运行。读链上数据走 RPC，写链上只发生在人工审批之后，并且只允许测试网（chain id 97）。

## 组成

| 文件 | 作用 |
|---|---|
| `agent/workforce/db.py` | 表结构和读写：员工、工作区、任务、消息、审批、记忆（FTS5 全文索引）、定时任务、技能状态、链上记录。状态变化用比较再写入，两个线程不会同时改同一个任务 |
| `agent/workforce/engine.py` | 调度：消息投递循环、主管拆分、按依赖分派、线程池执行、交接、重试、超时、预算、审批、定时、上链轮询、重启恢复 |
| `agent/workforce/planner.py` | 主管的拆分：配了 `ANTHROPIC_API_KEY` 且主管选了 Anthropic 时用模型出计划（结构化输出，逐项校验员工、技能、输入、依赖顺序，地址必须出现在目标里）；否则或模型计划不合格时退回规则拆分 |
| `agent/workforce/skills.py` | 7 个内置技能：钱包分析、代币信息、合约分析、交易历史、合约事件读取、链上监控、数据报告。每个技能有输入输出结构、所需权限、版本 |
| `agent/workforce/chain.py` | 只读 RPC：区块、余额、代码、存储槽、eth_call、日志、回执，字节码里的函数选择器扫描 |
| `agent/workforce/anchor.py` | 结果上链：一笔 0 金额、发给自己的交易，calldata 是 `CSTASK01 ‖ sha256(报告) ‖ 任务号`；发送前先 estimateGas 模拟；只接受 chain id 97 |
| `agent/workforce/cron.py` | 5 段 cron 表达式（UTC） |
| `backend/workforce_api.py` | `/api/wf/*` 接口、SSE 实时流（断线按 Last-Event-ID 续传）、写接口限流、`/terminal/` 静态文件 |
| `web/terminal/` | React + TypeScript 终端 |

## 任务和消息

任务状态：`pending → assigned → running → (waiting) → review → completed`，以及 `failed`、`cancelled`。目标任务（goal）在子任务全部结束后进入 `review`，等人审批。

消息种类：user、agent、system、task、tool、chain。员工之间的消息有投递状态 `queued → delivered → processed`（或 `failed`、`expired`），带去重键，同一件事不会投递两次；超时未处理的消息会被重新投递，进程重启后从数据库恢复。

主管的限制：最多步数、截止时间、预算。超出预算时不会继续花钱，而是生成一条「超预算」审批。

权限：员工只能用自己被授予的技能，技能需要的权限（`chain:read`、`net:etherscan`、`report:write`）也必须授予；员工之间直接发消息需要 `message:peers`，并且只能在同一工作区。

## 环境变量

| 变量 | 作用 | 不设置时 |
|---|---|---|
| `CHAINSTAFF_CHAIN` | 链配置文件（例如 `config/chain.example.json` 的 BSC 测试网） | 链上技能不可用 |
| `ANTHROPIC_API_KEY` | 主管用模型拆分、员工用模型写摘要 | 规则拆分，不调用模型 |
| `CHAINSTAFF_MODEL_PRICE` | 每百万 token 价格 `输入,输出`（美元），用来记成本 | 成本记 0 |
| `ETHERSCAN_API_KEY` | 交易历史技能 | 该技能报错并写明原因 |
| `CHAINSTAFF_ANCHOR_WALLET` | 上链用的托管钱包（`evm`），只能是测试网 | 审批后不能上链 |

## 终端

`/terminal/` 的页面：Overview、Workforce、Workspace、Tasks、On-Chain、Skills、Activity、Settings；手机上底部是 Home、Agents、Workspace、Tasks、More。登录用控制台的同一个令牌，令牌只存在当前标签页。

On-Chain 页把「链下完成」（任务审批通过）和「链上确认」（交易被打包进区块）分开显示。

## 还没有做的

- 自己部署的合约（Agent Registry、Task Registry、Reputation）。现在的上链是测试网上的 calldata 记录，任何人可以用 `/api/wf/chain/verify/{tx}` 把链上哈希和本地结果对照。
- ERC-8004 身份的链上核验。员工档案里的编号和地址是手填的。
- 代码执行沙箱、浏览器自动化。员工只能调用内置技能，不能在服务器上执行任意命令。
- 多用户：登录、钱包签名登录、按用户隔离工作区、角色权限。现在一个令牌对应一个所有者。
- 向量记忆。现在是关键词全文检索。
- 第三方技能安装。技能市场里只有内置技能，可以安装、卸载、启用、停用、更新。
- LangGraph。任务图是自建的，节点结构按 LangGraph 的思路设计，之后可以把单个员工的「模型 ↔ 工具」循环换成 LangGraph。
