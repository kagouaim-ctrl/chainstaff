# KAGOU — Real-Time On-Chain Multi-Agent Platform

BNB Chain 上的实时多 Agent 平台，由 ChainStaff（链工）整合升级而来。核心是 AI 员工平台（Agent Runtime、Mission 调度、Tool Registry）和 BSC 实时数据层（多节点 RPC 池、WebSocket 新区块监听、重组处理、链上事件监控），控制台是 `web/terminal/`（KAGOU Console）。整合说明见 [docs/KAGOU_INTEGRATION_REPORT.md](docs/KAGOU_INTEGRATION_REPORT.md)。

默认只读链上数据。Agent 不能自己转移资产、不能自己签交易；私钥和助记词不会交给模型；模型调用只在后端。KAGOU 没有 Telegram：没有机器人、群聊、通知或 webhook，预警和报告只在控制台里。

原 ChainStaff 的岗位（财务运营员、社区客服、链上监控员、公告翻译员、营销结算员、空投反女巫）仍在旧控制台（`/`）里。只有财务运营员会动钱：白名单内的小额自动付（绿），超限额生成 Safe 多签提案等老板签字（黄），陌生地址、风险地址、重复付款直接拦截（红）。社区客服和付款请求原来从 Telegram 群进来，现在没有对外入口，只能在控制台试算。

## 现在有什么

| 模块 | 说明 |
|---|---|
| `agent/rules/` 规则引擎 | 唯一决定钱动不动的代码。[规格](docs/specs/rules_engine.md) |
| `agent/team.py` | 岗位库（6 个可雇佣、4 个即将上线），雇佣、停用、解雇，每个员工独立的日志 |
| `agent/front_desk.py` | 消息分派：先查骗局，再分给财务或客服（现在只供控制台试算使用） |
| `agent/background.py` | 服务器后台任务：链上监控员巡检、国库预警，只写进账本，不发到任何地方 |
| `agent/bsc/` + `backend/chain_api.py` | BSC 实时数据层：多节点 RPC 池（故障切换、限流、重组安全的缓存）、WebSocket 新区块监听（断线重连、补块、HTTP 轮询兜底）、重组回滚、确认数、地址/代币/合约事件监控，事件可触发 Mission；`/api/chain/*` 和 `/api/chain/ws` |
| `agent/skills/finance_ops/` | 付款请求解析、员工服务、周报、国库预警。[规格](docs/specs/execution.md) |
| `agent/skills/community_support/` | 社区客服：FAQ 原文回答，规则识别骗局（索要助记词、冒充管理员含形近字、仿冒和 punycode 域名、私聊诱导、转币翻倍），查交易，转人工工单 |
| `agent/skills/chain_monitor/` | 链上监控员：大额转出、陌生对手方、地址投毒、无限授权和 NFT 全部授权、余额过低，每条只报一次，每日简报 |
| `agent/rules/batch.py` + `agent/skills/campaign_payouts/` | 营销结算员：名单核对（格式、重复、风险名单、同一活动重复领取、反女巫剔除），按固定金额或积分分奖池，单人上限、最低额、预算；老板确认后导出 Safe Transaction Builder JSON 和 CSV Airdrop 文件 |
| `agent/skills/airdrop_sybil/` | 空投反女巫：逐个地址读首笔资金来源和前 100 笔交易，规则找出同一资金来源、名单内互相打钱、批量创建、操作完全相同的团伙，以及几乎没活动和快照前新建的钱包；每条剔除都有理由，干净名单直接交给营销结算员 |
| `agent/skills/translator/` | 公告翻译员：调用 Claude（`claude-opus-5-5`）一次出多语言草稿，规则核对数字、地址、链接、@账号和术语，老板确认后才发 |
| `agent/execution/` | ERC-20 / BNB 转账、发送前模拟、Safe 提案和 EIP-712 哈希、只读 RPC |
| `agent/store.py` | SQLite 账本：先记账后付款，重启不丢，同一请求不会付两次 |
| `backend/` + `web/` | 老板控制台：团队总览（雇佣、停用、改称呼、岗位库）、社区客服（设置、FAQ、骗局试算、工单）、链上监控（盯盘地址、预警线、预警、简报）、公告翻译（写公告、草稿核对、术语表）、营销结算（名单核对预览、批次、导出 Safe 文件、标记执行）、空投反女巫（检查进度、团伙、逐个地址理由、导出、判定规则、导入链上资料）；财务的总览、审批、账本筛选和 CSV、收款人、规则和试算、国库预警、员工档案、报告、审计日志、一键暂停 |
| `agent/workforce/` + `backend/workforce_api.py` | AI 员工平台引擎：自定义员工（角色、模型、技能、权限、预算、链上身份）、工作区、AI 主管拆分任务并按依赖并行分配、员工之间持久化消息（去重、投递状态、超时、重试）、8 种任务状态、人工审批、分范围的长期记忆（全文检索、过期）、cron 定时任务、7 个读真实链上数据的技能、审批后把结果哈希写到 BSC 测试网。[说明](docs/workforce.md) |
| `web/terminal/` | KAGOU Console（`/terminal/`）：Terminal、Agents、Workspace、Tasks、On-Chain（含实时链上页）、Skills，以 Mission 为中心，实时阶段和事件流，手机端独立布局 |
| `scripts/register_agent.py` | 用 bnbagent SDK 注册 ERC-8004 链上工号（测试网免 gas） |
| `scripts/stage0_gate.py` | 读阶段 0 访谈记录表，算漏斗、中位数和关卡 A 是否通过 |
| `tests/` | 455 个用例，覆盖率 99%，另有 3 个只读的 BSC 测试网冒烟测试（`KAGOU_TESTNET=1`，CI 单独一个 job）；CI 跑测试、pip-audit、Semgrep，以及终端的类型检查、构建和 npm audit |

没配置钱包时一切都在测试模式，不碰链。

## 快速开始

```bash
pip install -e ".[dev]"
pytest

cp config/policy.example.json config/policy.json   # 白名单、限额、老板（签字人）
```

控制台：

```bash
export CHAINSTAFF_CONSOLE_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
uvicorn backend.app:app --port 8000      # 打开 http://localhost:8000，粘贴令牌
```

AI 员工终端（React）：

```bash
cd web/terminal && npm ci && npm run build && cd ../..
uvicorn backend.app:app --port 8000      # 打开 http://localhost:8000/terminal/，用同一个令牌登录
```

开发时 `npm run dev` 会把 `/api` 代理到 8000 端口。终端用到的环境变量见 [docs/workforce.md](docs/workforce.md)。

服务器进程同时跑 Agent 引擎、BSC 监听（`KAGOU_CHAIN_LISTENER=0` 关闭）和后台巡检（`KAGOU_BACKGROUND=0` 关闭）。链配置里的 `rpc_urls`、`ws_urls`、`confirmations` 决定连哪些节点。员工档案页另外读 `CHAINSTAFF_AGENT_ID`（ERC-8004 工号）和 `CHAINSTAFF_WALLET_ADDRESS`（测试模式下显示的钱包地址）。

其他员工需要的环境变量（都是可选的，没设置时控制台会提示）：链上监控员读转账和授权、空投反女巫查地址资料要 `ETHERSCAN_API_KEY`；营销结算员导出 Safe 文件要在链配置里填 `safe_address` 和代币合约地址；公告翻译员要 `ANTHROPIC_API_KEY`。
上测试网和主网：见 [docs/deploy-bnb.md](docs/deploy-bnb.md)。上线前对照 [安全清单](docs/security-checklist.md)。

## 目录

```
agent/
  rules/               # 规则引擎（放款判断只在这里）
  execution/           # 链上执行：转账、Safe 提案、RPC
  skills/finance_ops/  # 财务运营员：解析、员工服务、周报、预警
  skills/community_support/ chain_monitor/ translator/  # 其他岗位
  team.py front_desk.py background.py  # 团队名册、消息分派、后台巡检
  bsc/                 # BSC 实时数据层：RPC 池、监听、重组、监控
  store.py             # SQLite 账本
  config.py runtime.py # 配置加载和组装
agent/workforce/       # AI 员工平台：员工、任务图、消息总线、主管、技能、上链
backend/ web/          # 控制台（web/terminal/ 是新的 React 终端）
config/                # 示例配置（真实配置不提交）
scripts/               # ERC-8004 注册
docs/                  # 规格、部署、安全清单
tests/
```

## 硬规定（来自策划书）

1. 放款判断只写在 `agent/rules/`，模型输出只是建议。
2. 私钥只在钱包服务商的签名层，代码、日志、数据库里都不出现。
3. 先测试网后主网；主网前 4 周用小额真实资金试运行。
4. 阶段 4 之前不写自己的智能合约。
