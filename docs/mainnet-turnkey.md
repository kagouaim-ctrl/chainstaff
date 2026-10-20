# 上 BSC 主网（小额试运行）

主网会动真钱。代码会强制检查三件事：老板设置了 `CHAINSTAFF_ALLOW_MAINNET=yes`、用的是 Turnkey 钱包、RPC 返回的链 ID 是 56。

钱的安全靠三层：
1. 规则引擎：白名单、单笔上限、每日上限（`config/policy.json`）。
2. Turnkey 策略：就算机器人被黑，Turnkey 也只会签「转 USDT 给白名单地址」的交易，转 BNB、转给别人、换链都签不出来。
3. 钱包里只放小额预算（比如 30 USDT）。Turnkey 策略管不了金额，所以最坏情况的损失就是钱包里的钱。

## 1. 注册 Turnkey，建钱包

1. 打开 https://app.turnkey.com 注册，会自动建一个组织。在组织设置里复制 **Organization ID**。
2. Wallets → Create wallet，建一个以太坊（Ethereum）账户，复制它的 **0x 地址**。这就是员工的主网钱包。
3. Users → 新建一个用户，名字叫 `chainstaff-bot`。**不要**把它加进 root quorum，root 用户会绕过所有策略。
4. 给这个用户添加 API key，在浏览器里生成，复制 **API public key** 和 **API private key**。私钥只显示一次，存进密码管理器，不要发给任何人，也不要发到聊天里。
5. 复制这个用户的 **User ID**。

## 2. 配置

```bash
git pull
cp config/chain.mainnet.example.json config/chain.json
```

编辑 `config/policy.json`：
- `payees` 改成真实收款人，测试网的假地址删掉。
- 限额按预算调小，例如 `"single_limit": {"USDT": "5"}`、`"daily_limit": {"USDT": "15"}`。

生成 Turnkey 策略，粘贴到 Turnkey → Policies → Create policy：

```bash
python3 scripts/turnkey_policy.py --user-id 你的UserID
```

以后白名单改了，要重新生成并更新这条策略，否则新收款人会被 Turnkey 拒签。

## 3. 充值

从交易所提现到第 1 步的钱包地址，网络选 **BSC (BEP20)**：
- 30 USDT：自动付款的预算。
- 0.01 BNB：手续费，够付几百笔。

## 4. 启动

```bash
export TELEGRAM_BOT_TOKEN=...
export CHAINSTAFF_CHAIN=config/chain.json
export CHAINSTAFF_LIVE=1
export CHAINSTAFF_WALLET=turnkey
export CHAINSTAFF_ALLOW_MAINNET=yes
export TURNKEY_ORG_ID=...
export TURNKEY_SIGN_WITH=0x...              # 第 1 步的钱包地址
export TURNKEY_API_PUBLIC_KEY=...
export TURNKEY_API_PRIVATE_KEY=...          # 只放在环境变量里
python3 -m bot.main
```

日志里出现 `live=True` 后，先付自己 1 USDT 试一笔，在 bscscan.com 上确认到账。

## 5. 试运行两周

- 每天看一次 `/today`，每周一看周报。
- 有任何异常先发 `/pause`，所有付款立刻停。
- Turnkey 每次签名可能计费，价格以 Turnkey 官网为准。
