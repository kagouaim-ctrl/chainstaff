# 链工 ChainStaff

带限额钱包的 AI 员工团队（BNB Chain）。一次部署、一个 Telegram 机器人，里面可以雇佣多个 AI 员工：财务运营员、社区客服、链上监控员、公告翻译员、营销结算员、空投反女巫，另有 4 个岗位即将上线。

团队在 Telegram 群里发「付 Alice 300 USDT 设计费」，员工按老板定的规则核对：白名单内的小额自动付（绿），超限额生成 Safe 多签提案等老板签字（黄），陌生地址、风险地址、重复付款直接拦截（红）。每周一自动发周报，国库有异常立刻预警。

只有财务运营员会动钱。社区客服只按项目写的 FAQ 原文回答、识别骗子、查交易、转人工；链上监控员只读链上数据报警；公告翻译员出草稿，老板确认后才发。营销结算员只出 Safe 批量提案，钱要多签人在 Safe 里签了才动；空投反女巫只读链上数据。

## 现在有什么

| 模块 | 说明 |
|---|---|
| `agent/rules/` 规则引擎 | 唯一决定钱动不动的代码。[规格](docs/specs/rules_engine.md) |
| `agent/team.py` | 岗位库（6 个可雇佣、4 个即将上线），雇佣、停用、解雇，每个员工独立的日志 |
| `agent/front_desk.py` | 一个机器人托管全部员工：每条群消息先查骗局，再分给财务或客服 |
| `agent/skills/finance_ops/` | 付款请求解析、员工服务、周报、国库预警。[规格](docs/specs/execution.md) |
| `agent/skills/community_support/` | 社区客服：FAQ 原文回答，规则识别骗局（索要助记词、冒充管理员含形近字、仿冒和 punycode 域名、私聊诱导、转币翻倍），查交易，转人工工单 |
| `agent/skills/chain_monitor/` | 链上监控员：大额转出、陌生对手方、地址投毒、无限授权和 NFT 全部授权、余额过低，每条只报一次，每日简报 |
| `agent/rules/batch.py` + `agent/skills/campaign_payouts/` | 营销结算员：名单核对（格式、重复、风险名单、同一活动重复领取、反女巫剔除），按固定金额或积分分奖池，单人上限、最低额、预算；老板确认后导出 Safe Transaction Builder JSON 和 CSV Airdrop 文件 |
| `agent/skills/airdrop_sybil/` | 空投反女巫：逐个地址读首笔资金来源和前 100 笔交易，规则找出同一资金来源、名单内互相打钱、批量创建、操作完全相同的团伙，以及几乎没活动和快照前新建的钱包；每条剔除都有理由，干净名单直接交给营销结算员 |
| `agent/skills/translator/` | 公告翻译员：调用 Claude（`claude-opus-5-5`）一次出多语言草稿，规则核对数字、地址、链接、@账号和术语，老板确认后才发 |
| `agent/execution/` | ERC-20 / BNB 转账、发送前模拟、Safe 提案和 EIP-712 哈希、只读 RPC |
| `agent/store.py` | SQLite 账本：先记账后付款，重启不丢，同一请求不会付两次 |
| `bot/` | Telegram 机器人：付款请求和财务命令，`/faq` `/tx` `/human` `/tickets`，`/watch` `/alerts` `/brief`，`/translate` `/post` `/discard`，`/team` `/help`；定时周报、简报和预警 |
| `backend/` + `web/` | 老板控制台：团队总览（雇佣、停用、改称呼、岗位库）、社区客服（设置、FAQ、骗局试算、工单）、链上监控（盯盘地址、预警线、预警、简报）、公告翻译（写公告、草稿核对、术语表、分语言发群）、营销结算（名单核对预览、批次、导出 Safe 文件、标记执行）、空投反女巫（检查进度、团伙、逐个地址理由、导出、判定规则、导入链上资料）；财务的总览、审批、账本筛选和 CSV、收款人、规则和试算、国库预警、员工档案、Telegram 管理（通知群、自动通知、发消息、群聊模拟器、消息记录）、聊天页登录老板自己的 Telegram 号（真实群聊私聊收发、建群拉人、邀请链接，登录只存在浏览器里）、报告、审计日志、一键暂停 |
| `scripts/register_agent.py` | 用 bnbagent SDK 注册 ERC-8004 链上工号（测试网免 gas） |
| `scripts/stage0_gate.py` | 读阶段 0 访谈记录表，算漏斗、中位数和关卡 A 是否通过 |
| `tests/` | 310 个用例，覆盖率 99.9%；CI 跑测试、pip-audit、Semgrep |

没配置钱包时一切都在测试模式，不碰链。

## 快速开始

```bash
pip install -e ".[dev]"
pytest

cp config/policy.example.json config/policy.json   # 白名单、限额、老板用户名
export TELEGRAM_BOT_TOKEN=...                       # 不要写进代码或提交
python3 -m bot.main
```

群里使用前，在 BotFather 发 `/setprivacy` 选 Disable，机器人才能看到普通消息。

控制台：

```bash
export CHAINSTAFF_CONSOLE_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
uvicorn backend.app:app --port 8000      # 打开 http://localhost:8000，粘贴令牌
```

控制台和机器人共用同一个账本文件（`CHAINSTAFF_DB`），所以在控制台改的通知群、自动通知、要发到群里的消息，机器人几秒内就会执行。员工档案页另外读 `CHAINSTAFF_AGENT_ID`（ERC-8004 工号）和 `CHAINSTAFF_WALLET_ADDRESS`（测试模式下显示的钱包地址）。

其他员工需要的环境变量（都是可选的，没设置时控制台会提示）：链上监控员读转账和授权、空投反女巫查地址资料要 `ETHERSCAN_API_KEY`；营销结算员导出 Safe 文件要在链配置里填 `safe_address` 和代币合约地址；公告翻译员要 `ANTHROPIC_API_KEY`。社区客服要删骗子消息时，把机器人设为群管理员并给「删除消息」权限。

上测试网和主网：见 [docs/deploy-bnb.md](docs/deploy-bnb.md)。上线前对照 [安全清单](docs/security-checklist.md)。

公开 Telegram 体验群（任何人可进群试用，只跑测试模式）：见 [docs/telegram-demo.md](docs/telegram-demo.md)。

在控制台里用自己的 Telegram 号：打开聊天页，点「连接我的 Telegram」。第一次要填 [my.telegram.org](https://my.telegram.org/apps) 申请的 api_id 和 api_hash（只存在这个浏览器里），然后扫码或用手机号登录。浏览器直接连 Telegram 服务器，登录信息不经过 KAGOU 后端；在中国大陆要先开代理。客户端库在 `web/vendor/gramjs.min.js`，重新打包用 `scripts/build_gramjs.sh`。

## 目录

```
agent/
  rules/               # 规则引擎（放款判断只在这里）
  execution/           # 链上执行：转账、Safe 提案、RPC
  skills/finance_ops/  # 财务运营员：解析、员工服务、周报、预警
  skills/community_support/ chain_monitor/ translator/  # 其他岗位
  team.py front_desk.py   # 团队名册和消息分派
  store.py             # SQLite 账本
  config.py runtime.py # 配置加载和组装
bot/                   # Telegram 机器人
backend/ web/          # 控制台
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
