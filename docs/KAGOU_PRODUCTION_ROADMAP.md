# KAGOU 生产路线图

基线：chainstaff 草稿 PR #15（`claude/project-thread-hfxnft`，head `6014113`），在其上继续开发，不重写。2026-10-25 重新核对：PR #15 仍为草稿、head 未变；KAGOUAI `main` 仍为 `1957bb3`（`/demo/` 已上线）。

状态：✅ 完成并验证　🟡 有代码，缺真实环境验证或部分完成　⛔ 需要你授权或提供资源　⬜ 未开始

| 阶段 | 内容 | 状态 | 说明 |
|---|---|---|---|
| 1 | 真实后端部署准备、Demo / Live 双模式 | ✅ | 见 [实施报告](KAGOU_IMPLEMENTATION_REPORT.md) 第 1 阶段 |
| 2 | 真实 LLM Tool Calling Runtime | 🟡 ⛔ | Runtime、Tool Registry、预算和成本记账已在 PR #15；真实模型调用需要 `ANTHROPIC_API_KEY`（付费） |
| 3 | 多 Agent 协作、审计报告的调度问题 | 🟡 | 6 项 P0 问题 PR #15 已修并有测试；本分支会再逐项核对回归测试 |
| 4 | GMGN 式链上检索 | 🟡 | 已有 13 个只读工具；持有人分布、流动性、历史统计需要索引或第三方数据源 |
| 5 | 统一 On-Chain 页面 | 🟡 | 链下完成 / 待审核 / 链上确认已区分；页面分区还要整理 |
| 6 | Workspace 接入真实事件 | 🟡 | SSE 已是真实事件；需要用真实模型跑一遍确认界面 |
| 7 | 清理旧业务演示内容 | ⬜ ⛔ | 示例快照里还有国库巡检等旧场景；重录快照后更新 `/demo/` 属于改动线上站点，要你确认 |
| 8 | 移除 Telegram | 🟡 | PR #15 已删机器人和接口；内部旧名字（`TelegramSettings` 等）还在 |
| 安全 | 认证、限流、预算、审计 | 🟡 | 有：令牌、只读演示令牌、预算、工具超时、审计日志。没有：多用户、角色、API 限流 |

## 需要你决定或提供的

| 事项 | 用在哪 | 现状 |
|---|---|---|
| `ANTHROPIC_API_KEY`（付费） | 第 2、6 阶段的真实模型测试 | 没有。拿到前所有 Agent 测试都是脚本化模型（Mock），报告里单独列 |
| Railway 账号和项目 | 部署 Live 后端 | 配置已准备（`railway.toml`、部署指南）；没有部署 |
| `app.kagouai.com` DNS | 正式域名 | 不动，等你操作 |
| `ETHERSCAN_API_KEY`（免费额度） | 历史交易、合约源码 / ABI | 没有；相关工具会明确报告缺 key |
| 历史索引数据源（如 BscScan Pro、Covalent、Bitquery、自建索引） | 持有人分布、完整历史统计 | 未选。普通 RPC 做不到，不会假装能做到 |
| 更新 `www.kagouai.com/demo/` | 第 7 阶段 | 只会准备 PR，你确认后再合并 |
| PR #10–#15 的合并顺序 | 全部 | 都未合并；本分支叠在 #15 上 |

## 各阶段的验收

1. Live 控制台连真实后端；后端不可用时明确报错；Agent / Mission 读写、SSE、重启持久化在真实进程上通过（`scripts/live_check.py`）。
2. 用真实模型完成一个多轮工具调用的 Mission，Token 和成本来自 API 返回的 usage；模型不可用时状态明确。
3. 审计报告列出的每个调度问题都有对应的回归测试名。
4. 每个链上工具的结果带数据来源、区块高度和时间；测试网先验证，主网只读后做。
5. On-Chain 页面把“链上数据查询”和“Agent 执行验证”分成两块。
6. Workspace 的每个阶段都来自后端事件。
7. 演示 Mission 改为 BSC Wallet Analysis、Contract Analysis、Token Research、On-Chain Monitoring、Multi-Agent Research。
8. 代码里没有 Telegram 依赖或入口。
