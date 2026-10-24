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
| `agent/workforce/missions.py` | Mission 视图：把一个目标任务和它的子任务、审批、链上记录合成一个 Mission（进度、参与员工、交付物、上链证明状态），以及执行账本。只读，不改表结构 |
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

## Mission

终端里的 Mission 就是目标任务（goal），数据链路是 Mission → Task（子任务）→ Agent → Execution（工具调用）→ Result。后端没有新表，`missions.py` 在读的时候把这些拼起来，旧数据直接可用。

- 阶段（stage）：planning、executing、review、completed、failed、cancelled，由目标任务的状态推出来。
- 进度只用真实状态：已结束子任务数 / 子任务总数，以及完成、失败、运行、排队各多少。引擎没有子任务内部的百分比，终端也不编。
- 上链证明（proof.state）分五种，终端始终分开显示：`in_progress`、`awaiting_review`（等人审批）、`offchain_completed`（审批通过 = 链下完成）、`onchain_pending`（锚定交易已广播，未打包）、`onchain_confirmed`（交易已打包进区块）。另有 `not_completed`（失败或取消）。
- 锚定的是整个 Mission 的结果哈希。账本里子任务一行写的是「汇入 Mission 结果」，不声称每个子任务的哈希都在链上。

直接派活：`POST /api/wf/agents/{id}/run` 让一个员工直接跑一个技能（不经过主管拆分）。它会生成一个只有一个子任务的 Mission（planner 为 `direct`），照样走审批和上链，交付物就是这个技能的输出。数据报告技能不能单独派，因为它要汇总别人的结果。

相关接口：

| 接口 | 作用 |
|---|---|
| `GET /api/wf/missions` | Mission 列表，参数 `workspace`、`status`（逗号分隔）、`agent`、`q`、`limit` |
| `GET /api/wf/missions/{id}` | 单个 Mission；传子任务号返回 404 |
| `GET /api/wf/ledger` | 执行账本：每个结束的 Mission 一行，带结果哈希、上链状态、交易链接，以及其中每次执行 |
| `POST /api/wf/agents/{id}/run` | 直接派活，body 是 `skill`、`input`，可选 `title`、`priority`、`budget_usd`、`timeout_s`、`workspace_id` |
| `GET /api/wf/tasks` | 现在额外返回 `counts`（各状态的数量），给标签页计数用 |

员工的实时状态（`live`）：`busy`（有分派中或运行中的子任务，或在管的 Mission 在执行）、`review`（手上的 Mission 在等人审批）、`idle`、`error`（24 小时内有失败），以及 `paused`、`archived`。

## 终端

`/terminal/` 是一个面向执行的工作台，不是聊天界面。顶栏：Terminal、Agents、Workspace、Tasks、On-Chain、Skills，右侧是全局搜索（`/` 或 ⌘K，可搜员工、Mission、任务号、交易哈希、地址）、网络和实时连接状态、运行中数量、通知（待审批和最近事件）、钱包。Activity 和 Settings 在 More 菜单里。默认首页是 Terminal。

- Terminal：左边按状态和职能分类，中间是员工表（排序、筛选、搜索、自定义列、紧凑模式、收藏、实时刷新），右边是员工 Inspector（概览、当前执行、技能、最近任务、记忆、权限、动态）。在 Inspector 里可以直接派活（Run task）或交给工作区一个 Mission（Assign mission）、暂停、配置。
- Workspace：左边 Agent Squad（每个员工的状态、当前任务、进度、最近活动）和 Mission 列表；中间是 Mission 工作区（Mission 头部、待审批、简报、任务分配、员工进度与协作时间线、结果），底部是下达 Mission 的输入框；右边是 Execution Inspector（Overview、Steps、Logs、Results、On-Chain），完整日志只在 Logs 里。
- Tasks：全宽任务表（All、Running、Pending、Review、Completed、Failed，可切换看子任务或 Mission），点一行在右边打开 Inspector（Overview、Execution、Graph、Messages、Logs、Result、On-Chain），任务图只在 Graph 标签里画。
- On-Chain：顶部是网络、RPC 健康、最新区块、Gas、锚定钱包；主区是执行账本（谁跑了什么、产出的哈希、有没有上链、在哪笔交易），以及交易、员工登记、合约事件、钱包查询。
- 手机：底部导航 Terminal、Agents、Workspace、Tasks、More。Workspace 上面是 Mission 头部，中间是时间线，下面是输入框；Squad 是左侧抽屉，Inspector 是底部弹层，日志默认折叠。

登录用控制台的同一个令牌，令牌只存在当前标签页。列设置、收藏、紧凑模式这类偏好存在浏览器本地。所有数据来自后端，实时更新走 `/api/wf/stream` 的 SSE。

## 还没有做的

- 自己部署的合约（Agent Registry、Task Registry、Reputation）。现在的上链是测试网上的 calldata 记录，任何人可以用 `/api/wf/chain/verify/{tx}` 把链上哈希和本地结果对照。
- ERC-8004 身份的链上核验。员工档案里的编号和地址是手填的。
- 代码执行沙箱、浏览器自动化。员工只能调用内置技能，不能在服务器上执行任意命令。
- 多用户：登录、钱包签名登录、按用户隔离工作区、角色权限。现在一个令牌对应一个所有者。
- 向量记忆。现在是关键词全文检索。
- 第三方技能安装。技能市场里只有内置技能，可以安装、卸载、启用、停用、更新。
- LangGraph。任务图是自建的，节点结构按 LangGraph 的思路设计，之后可以把单个员工的「模型 ↔ 工具」循环换成 LangGraph。
