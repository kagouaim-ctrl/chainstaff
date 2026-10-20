# 部署到 BNB Chain

顺序：本地测试模式 → BSC 测试网（真实链、假钱）→ 主网小额试运行 → 主网。每一步都要老板确认后再进入下一步。

## 0. 测试模式（现在就能跑，不碰链）

```bash
pip install -e .
cp config/policy.example.json config/policy.json      # 改白名单、限额、老板用户名
export TELEGRAM_BOT_TOKEN=...                           # BotFather 给的 token
python3 -m bot.main
```

绿灯付款只记账，回复里会写「测试模式：没有真的上链」。

## 1. BSC 测试网（chain id 97）

需要老板准备：

1. 一个测试网钱包（Trust Wallet 或 MetaMask），把**地址**发给开发者，助记词谁都不给。
2. 一点 tBNB 作手续费：官方水龙头 https://www.bnbchain.org/en/testnet-faucet ，或 Telegram 找 @bnbchain_official_bot。
3. 测试用的稳定币：bnbagent SDK 文档列出了 U Faucet（https://united-coin-u.github.io/u-faucet/ ），领到后把代币合约地址填进 `config/chain.json` 的 `tokens`。

然后：

```bash
pip install -e .                                         # 已包含 bnbagent SDK
cp config/chain.example.json config/chain.json          # 填代币地址、Safe 地址、国库地址

# 1) 注册链上工号（ERC-8004，测试网免 gas）
export WALLET_PASSWORD=...                               # 只用来加密本地 keystore
python3 scripts/register_agent.py --url https://你的落地页

# 2) 实盘付款（测试网）
export CHAINSTAFF_CHAIN=config/chain.json
export CHAINSTAFF_LIVE=1
export CHAINSTAFF_WALLET=evm                             # 测试网用本地加密钱包
export CHAINSTAFF_WALLET_PASSWORD=$WALLET_PASSWORD
python3 -m bot.main
```

启动时会核对 RPC 返回的链 ID 和配置是否一致，不一致就拒绝启动。

可选：
- `CHAINSTAFF_REPORT_CHAT=<群 ID>`：每周一 9 点自动发周报。
- 再加 `ETHERSCAN_API_KEY`（免费申请）和 `treasury_addresses`：每 5 分钟检查国库，发大额转出、陌生地址、地址投毒预警。
- 控制台：`CHAINSTAFF_CONSOLE_TOKEN=<随机长串> uvicorn backend.app:app`，浏览器打开就能改规则、看账本、一键暂停。

测试网验收（策划书阶段 1 第 7 周）：绿、黄、红三种结果各跑通 10 次，截图存档。

## 2. 主网前必须完成（代码会强制检查前两条）

| # | 事项 | 谁来做 |
|---|---|---|
| 1 | 老板书面同意上主网，部署时设置 `CHAINSTAFF_ALLOW_MAINNET=yes` | 老板 |
| 2 | 换成 Turnkey 钱包（`CHAINSTAFF_WALLET=turnkey`），主网禁止本地私钥 | 老板注册 Turnkey，开发者接入 |
| 3 | 在 Turnkey 策略里配置收款白名单、单笔和每日上限，用非 root API 用户（root 会绕过所有策略） | 老板 + 开发者 |
| 4 | 员工钱包只充小额预算（策划书：先 50–100 USDT 跑 2 周） | 老板 |
| 5 | 国库留在老板自己的 Safe，员工地址不是 Safe 的签名人 | 老板 |
| 6 | 核实 Safe 在 BSC 主网的当前支持和 Transaction Service 地址 | 开发者 |
| 7 | 对照 [安全清单](security-checklist.md) 逐条打勾 | 开发者 |
| 8 | 有预算时做一次外部渗透测试 | 老板决定 |
| 9 | 新客户前 2 周开 `read_only`，只出报表和预警 | 默认配置 |

主网 ERC-8004 注册：`python3 scripts/register_agent.py --network bsc-mainnet --i-understand-mainnet --url ...`（需要少量 BNB 作 gas）。

## 3. 长期在线

机器人和控制台需要一台一直开着的机器。策划书推荐 Agent Studio 支持的 NodeOps 或 AWS AgentCore；更简单的过渡做法是一台小云服务器（每月几十元）跑两个进程，SQLite 文件放在 `data/` 并定期备份。
