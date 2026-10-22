# 公开 Telegram 体验群

让任何人进群，直接和 AI 员工对话。只跑测试模式：付款都是模拟，不配钱包，不碰链。

## 准备

1. **新 bot token**：在 @BotFather 发 `/revoke` 作废旧 token，再拿新的。token 只填在部署平台的环境变量里，不要发到聊天、不要提交到仓库。
2. **隐私模式**：在 @BotFather 发 `/setprivacy` 选 Disable，机器人才能看到群里的普通消息。
3. **体验群**：在 Telegram 新建群，把机器人拉进去；想删骗子消息就设为管理员并给「删除消息」权限。群设置里生成邀请链接，放到官网。

## 部署（Railway）

1. 在 railway.com 用 GitHub 登录，New Project → Deploy from GitHub repo → 选 `chainstaff`。仓库根目录的 `Dockerfile` 会被自动识别。
2. 在服务的 Variables 里添加：
   - `TELEGRAM_BOT_TOKEN`：上面拿到的新 token
   - 可选 `CHAINSTAFF_CONSOLE_TOKEN`：至少 16 位随机串，填了就同时开控制台（Settings → Networking → Generate Domain 拿网址）
3. 可选：Settings → Volumes 挂一个卷到 `/app/data`，重新部署后聊天记录和账本不丢。
4. 部署完成后日志里出现 `Start polling`，在群里发「付 Alice 300 USDT 设计费」试一下。

其他平台（Render、自己的服务器）同理：用这个 Dockerfile，设置同样的环境变量。服务器上：

```bash
docker build -t chainstaff-demo .
docker run -d --restart unless-stopped -e TELEGRAM_BOT_TOKEN=... -v chainstaff-data:/app/data chainstaff-demo
```

## 安全

- 启动脚本 `scripts/public_demo.py` 发现 `CHAINSTAFF_LIVE`、`CHAINSTAFF_CHAIN`、钱包或 `TURNKEY_*` 变量时直接拒绝启动。
- 默认规则 `config/policy.demo.json` 里是示例收款人，老板名单为空，所以群里没人能审批、暂停或改规则。
- 体验群和真实团队用不同的机器人和账本，不要共用 token。
- 同一个 token 同时只能有一个程序在取消息；部署上线后，关掉你电脑上的机器人。
