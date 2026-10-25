# KAGOU 部署指南

本文件是部署的总入口。**到目前为止没有部署任何东西**：没有改 DNS，没有动 kagouai.com、落地页或 `/demo/`。所有步骤都要你确认后执行。

## 两种运行方式

| | Demo | Live |
|---|---|---|
| 地址 | `www.kagouai.com/demo/`（KAGOUAI 仓库，Vercel 静态托管） | 建议 `app.kagouai.com`（独立后端） |
| 构建 | `npm run build:demo`（`VITE_DEMO=1`） | `npm run build`，由后端在 `/terminal/` 提供 |
| 数据 | `demo-data.json` 示例快照（脚本化模型、模拟链） | 真实 SQLite、真实 BSC RPC、真实模型（有 key 时） |
| 写操作 | 全部拒绝 | 需要控制台令牌 |
| 后端故障 | 不适用 | 页面顶部红色横幅说明连接失败和数据停在什么时间；**不会回退到示例数据** |
| 标识 | 顶栏 `演示 · 示例数据`，实时指示显示 `Snapshot` | 顶栏 `Live`；没有模型 key 时显示 `Model off`；Settings → System 显示模式、数据库、链、写权限 |

两个构建来自同一份代码，演示模式的判断在编译时决定（`src/lib/demo.ts`），Live 构建里不包含任何读取示例快照的路径。

## 方案 A：Railway（你倾向的方案）

仓库根目录的 `railway.toml` 已写好：用同一个 `Dockerfile` 构建，健康检查 `/readyz`，单副本。

你需要在 Railway 上做（需要你的账号，开发不会代你操作）：

1. 新建项目 → 从 GitHub 仓库 `kagouaim-ctrl/chainstaff` 部署，分支选要上线的分支。
2. 给服务加一个 **Volume**，挂载路径 `/data`（SQLite 账本和 `policy.json` 在这里；不挂卷重启就丢数据）。
   - Railway 的卷默认属于 root，而镜像以 uid 10001 运行。如果启动日志出现 `KAGOU: /data is not writable by uid 10001`，在变量里加 `RAILWAY_RUN_UID=0`（以 root 运行，安全性略降）。容器会在不可写时直接退出，而不是带着一个存不住数据的库运行。
3. 设置变量（Variables）：

| 变量 | 必填 | 值 |
|---|---|---|
| `CHAINSTAFF_CONSOLE_TOKEN` | 是 | `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `CHAINSTAFF_CHAIN` | 是 | `/app/config/chain.example.json`（BSC 测试网，镜像里自带） |
| `KAGOU_CHAIN_LISTENER` | 否 | `1`（默认）开新区块监听 |
| `KAGOU_BACKGROUND` | 否 | `1`（默认） |
| `AI_PROVIDER` | 否 | 共享模型服务：`qwen`（首选）、`gemini`、`groq`、`openrouter`、`openai_compatible`、`local`（Ollama / vLLM），或 `anthropic` |
| `AI_BASE_URL` | 用 Qwen 等时必填 | 服务的 OpenAI 兼容地址，从服务控制台复制。Qwen 新加坡：`https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`（旧地址 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` 也能用）。必须 https（本机模型除外） |
| `AI_API_KEY` | 用 Qwen 等时必填 | 只放在这里，浏览器拿不到，也不会出现在 `/api/system` |
| `AI_MODEL` | 用 Qwen 等时必填 | 必须支持 tool calling。Qwen 测试建议 `qwen-plus` |
| `AI_PRICE` | 否 | `输入,输出`（美元 / 百万 token）。Qwen 新加坡地址不填时用内置的官方标价；北京区按人民币计价，要自己填换算后的美元价；其他服务不填时调用记为“无价格”，预算对它们不完整 |
| `AI_ENABLE_THINKING` | 否 | 仅 Qwen：`1` 开思考模式（更贵）。默认关 |
| `AI_DAILY_BUDGET_USD` / `AI_MONTHLY_BUDGET_USD` | 建议 | 每天（UTC）/ 每月的模型花费上限，到了就拒绝新的模型调用并给出明确错误。测试建议 `1` / `10` |
| `AI_MAX_AGENT_STEPS` | 否 | 每次 Agent 运行最多几次模型调用（默认 8，也是上限） |
| `AI_MAX_CONCURRENT_TASKS` | 否 | 同时进行的模型调用数（默认 5），其余排队 |
| `AI_MAX_OUTPUT_TOKENS` / `AI_REQUEST_TIMEOUT` | 否 | 单次调用的输出上限和超时（秒），Agent 自己的设置只能更低 |
| `AI_BREAKER_FAILURES` / `AI_BREAKER_COOLDOWN` | 否 | 连续失败几次后熔断（默认 5），熔断多少秒（默认 60） |
| `ANTHROPIC_API_KEY` | 否 | 第二个可选模型服务（Claude，默认最便宜的 Haiku 5.5） |
| `CHAINSTAFF_MODEL_PRICE` | 否 | Claude 的 `输入,输出` 价格覆盖；不填用内置价格表 |
| `ETHERSCAN_API_KEY` | 否 | 交易历史、源码验证 |
| `KAGOU_VERSION` | 否 | 部署的提交号，显示在 Settings |
| `KAGOU_CORS_ORIGINS` | 否 | 只有前端放在别的域名时才填，见下文 |

   不要设置：`CHAINSTAFF_LIVE`、`CHAINSTAFF_WALLET*`、`CHAINSTAFF_ANCHOR_WALLET`、`TURNKEY_*`。本阶段只读。
4. `PORT` 由 Railway 注入，镜像会用它。
5. 绑定域名：Railway 给的 `*.up.railway.app` 先用来验证；确认后再在域名服务商把 `app.kagouai.com` CNAME 到 Railway（**DNS 由你操作**）。

部署后验证（在任何能联网的机器上）：

```bash
python scripts/live_check.py --url https://<railway 域名> --token <控制台令牌>
```

它会建 2 个员工、1 个工作区、1 个 Mission（名字都以 `live-check` 开头），检查 SSE、幂等提交、审批后的链下完成状态，并输出每一项通过与否。对远程服务器不做重启检查；重启持久化可以在 Railway 上手动 Restart 后再跑一次，看前一次的 Mission 还在。

配置了模型后，再跑真实 AI 检查（会花一点模型费用，受上面的预算限制）：

```bash
python scripts/ai_check.py --url https://<railway 域名> --token <控制台令牌>
```

它建 4 个 Agent（Mission Coordinator、BSC Analyst、On-chain Monitor、Research Agent），让 BSC Analyst 用工具查一个真实地址的余额，再让团队完成一个多 Agent Mission，输出每一步的模型调用、Token、成本和工具调用。也可以在 GitHub Actions 里手动运行 `ai-check` 工作流（需要先在仓库 Settings → Secrets and variables → Actions 加 `AI_API_KEY`，以及 `AI_PROVIDER`、`AI_BASE_URL`、`AI_MODEL` 变量）。

## 方案 B：自己的服务器（Docker Compose + Caddy）

见 [deploy-kagou.md](deploy-kagou.md)。新增的环境变量（`KAGOU_DEMO_TOKEN`、`KAGOU_CORS_ORIGINS`、`KAGOU_VERSION`）已写进 `deploy/app.kagouai.com/kagou.env.example`。镜像的 `HEALTHCHECK` 和 Caddy 的 `health_uri` 已改为 `/readyz`（检查数据库可读）。

## 前端放在别处（可选）

默认后端自己在 `/terminal/` 提供控制台，同源，不需要 CORS。只有在前端单独托管（例如放到 Vercel）时：

```bash
cd web/terminal && VITE_API_BASE=https://app.kagouai.com npm run build
```

并在后端设置 `KAGOU_CORS_ORIGINS=https://<前端域名>`。规则：

- 只接受精确的 origin（`https://host[:port]`），不接受 `*`，非 localhost 必须 https；配置错误时服务器拒绝启动。
- 不允许携带 cookie（令牌只在 `Authorization` 头里）。
- `/api/chain/ws` 不受 CORS 约束，所以服务器自己检查 `Origin`：同域、名单内或非浏览器客户端才允许，其他直接以 4403 关闭。

## 健康检查

| 路径 | 鉴权 | 含义 |
|---|---|---|
| `/healthz` | 无 | 进程活着 |
| `/readyz` | 无 | 数据库能读；失败返回 503。Railway 和 Docker 的健康检查用它 |
| `/api/system` | 令牌 | 模式、版本、数据库是否持久、模型/链/浏览器 API 是否配置、写权限、CORS 名单、今日 / 本月 AI 花费和预算 |
| `/api/wf/ai-usage` | 令牌 | 按天、服务、模型的调用次数、失败数、Token、成本，以及熔断状态 |

## 停机和重启

uvicorn 以 `--timeout-graceful-shutdown 10` 运行：重启时最多等 10 秒让 SSE 连接结束，然后强制关闭。浏览器会自动重连并从上次的事件序号续传；断开期间页面顶部显示连接失败横幅。

## 上线前检查（Live）

- [ ] 卷挂在 `/data`，`/api/system` 里 `database.persistent` 为 `true`
- [ ] 令牌 ≥ 32 字符，只在平台变量里
- [ ] `CHAINSTAFF_CHAIN` 是测试网（chain id 97），除非你明确批准主网只读
- [ ] 没有任何钱包、签名或实盘付款变量
- [ ] `scripts/live_check.py --url …` 全部通过
- [ ] 设了 `AI_DAILY_BUDGET_USD` 和 `AI_MONTHLY_BUDGET_USD`；`scripts/ai_check.py --url …` 全部通过
- [ ] 控制台登录后 Settings → System 显示 `Live · 真实后端`
