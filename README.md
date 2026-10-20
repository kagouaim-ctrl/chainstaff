# 链工 ChainStaff

带限额钱包的 AI 员工。首发岗位：AI 财务运营员（BNB Chain）。

## 现在有什么

| 模块 | 状态 | 说明 |
|---|---|---|
| `agent/rules/` 规则引擎 | ✅ 可用 | 决定每笔付款是绿（自动付）、黄（Safe 提案等签字）还是红（拦截）。规格见 [docs/specs/rules_engine.md](docs/specs/rules_engine.md) |
| `agent/skills/finance_ops/parser.py` 付款请求解析 | ✅ 可用 | 把「付 Alice 300 USDT 设计费」变成结构化请求；看不懂或有歧义就追问，不猜 |
| `agent/skills/finance_ops/employee.py` 财务员工 | ✅ 可用 | 收消息 → 解析 → 规则 → 执行 → 记账 → 回复；老板指令：暂停、恢复、查看规则、今日账本 |
| `bot/` Telegram 机器人 | ✅ 代码完成，等 token 上线 | 目前是测试模式：绿灯只模拟付款，不上链 |
| `tests/` | ✅ 129 个用例全部通过；规则引擎、解析、员工 100% 覆盖 | |
| `backend/` FastAPI | 待开发 | |
| `web/` 控制台 | 待开发 | |
| 钱包与链上执行 | 待开发 | 需要测试网钱包和签名服务商账号 |

## 运行测试

```bash
python3 -m pip install -e ".[dev]"   # 或者只装 pytest
python3 -m pytest
```

## 启动 Telegram 机器人

```bash
cp config/policy.example.json config/policy.json   # 改成你的白名单、限额、老板用户名
export TELEGRAM_BOT_TOKEN=从 BotFather 拿到的 token     # 不要写进代码或提交
export CHAINSTAFF_POLICY=config/policy.json
python3 -m bot.main
```

然后把机器人拉进群。在群里使用前，要在 BotFather 发 `/setprivacy` 并选 Disable，机器人才能看到普通消息（否则只能看到 / 指令）。

## 目录

```
agent/
  rules/               # 规则引擎：白名单、限额、重复、风险地址（放款判断只在这里）
  skills/finance_ops/  # 财务运营员：付款请求解析、员工服务
bot/                   # Telegram 机器人
config/                # 规则配置（policy.json 不提交）
backend/  web/         # 待开发
docs/specs/            # 每个功能一份规格
tests/
```

## 硬规定（来自策划书）

1. 放款判断只写在 `agent/rules/`，模型输出只是建议。
2. 私钥只在钱包服务商的签名层，代码、日志、数据库里都不出现。
3. 先测试网后主网。
4. 阶段 4 之前不写自己的智能合约。
