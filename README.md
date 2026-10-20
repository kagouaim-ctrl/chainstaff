# 链工 ChainStaff

带限额钱包的 AI 员工。首发岗位：AI 财务运营员（BNB Chain）。

团队在 Telegram 群里发「付 Alice 300 USDT 设计费」，员工按老板定的规则核对：白名单内的小额自动付（绿），超限额生成 Safe 多签提案等老板签字（黄），陌生地址、风险地址、重复付款直接拦截（红）。每周一自动发周报，国库有异常立刻预警。

## 现在有什么

| 模块 | 说明 |
|---|---|
| `agent/rules/` 规则引擎 | 唯一决定钱动不动的代码。[规格](docs/specs/rules_engine.md) |
| `agent/skills/finance_ops/` | 付款请求解析、员工服务、周报、国库预警。[规格](docs/specs/execution.md) |
| `agent/execution/` | ERC-20 / BNB 转账、发送前模拟、Safe 提案和 EIP-712 哈希、只读 RPC |
| `agent/store.py` | SQLite 账本：先记账后付款，重启不丢，同一请求不会付两次 |
| `bot/` | Telegram 机器人：付款请求、`/rules` `/today` `/pending` `/report` `/approve` `/reject` `/pause` `/resume`，定时周报和预警 |
| `backend/` + `web/` | 老板控制台：改规则、看账本、审批、一键暂停、周报 |
| `scripts/register_agent.py` | 用 bnbagent SDK 注册 ERC-8004 链上工号（测试网免 gas） |
| `tests/` | 195 个用例，覆盖率 99.9%；CI 跑测试、pip-audit、Semgrep |

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

上测试网和主网：见 [docs/deploy-bnb.md](docs/deploy-bnb.md)。上线前对照 [安全清单](docs/security-checklist.md)。

## 目录

```
agent/
  rules/               # 规则引擎（放款判断只在这里）
  execution/           # 链上执行：转账、Safe 提案、RPC
  skills/finance_ops/  # 财务运营员：解析、员工服务、周报、预警
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
