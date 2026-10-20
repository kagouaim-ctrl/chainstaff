# 链工 ChainStaff

带限额钱包的 AI 员工。首发岗位：AI 财务运营员（BNB Chain）。

## 现在有什么

| 模块 | 状态 | 说明 |
|---|---|---|
| `agent/rules/` 规则引擎 | ✅ 可用 | 决定每笔付款是绿（自动付）、黄（Safe 提案等签字）还是红（拦截）。规格见 [docs/specs/rules_engine.md](docs/specs/rules_engine.md) |
| `agent/skills/finance_ops/parser.py` 付款请求解析 | ✅ 可用 | 把「付 Alice 300 USDT 设计费」变成结构化请求；看不懂或有歧义就追问，不猜 |
| `tests/` | ✅ 114 个用例全部通过，覆盖率 100% | |
| `bot/` Telegram 机器人 | 待开发 | 需要你的 BotFather token |
| `backend/` FastAPI | 待开发 | |
| `web/` 控制台 | 待开发 | |
| 钱包与链上执行 | 待开发 | 需要测试网钱包和签名服务商账号 |

## 运行测试

```bash
python3 -m pip install -e ".[dev]"   # 或者只装 pytest
python3 -m pytest
```

## 目录

```
agent/
  rules/               # 规则引擎：白名单、限额、重复、风险地址（放款判断只在这里）
  skills/finance_ops/  # 财务运营员技能：付款请求解析
backend/  bot/  web/   # 待开发
docs/specs/            # 每个功能一份规格
tests/
```

## 硬规定（来自策划书）

1. 放款判断只写在 `agent/rules/`，模型输出只是建议。
2. 私钥只在钱包服务商的签名层，代码、日志、数据库里都不出现。
3. 先测试网后主网。
4. 阶段 4 之前不写自己的智能合约。
