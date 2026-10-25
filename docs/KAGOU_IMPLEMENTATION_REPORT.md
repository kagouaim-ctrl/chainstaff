# KAGOU 实施报告

分支：`claude/project-thread-v5zo4i`，叠在 PR #15（`claude/project-thread-hfxnft`，`6014113`）之上。每个阶段完成后在这里追加一节。

验证用语：**真实** = 真实进程 / 真实网络 / 真实浏览器；**模拟** = 测试替身（脚本化模型、模拟链节点）。两者分开报告。

---

## 第 1 阶段：真实后端部署准备、Demo / Live 双模式（2026-10-25）

### 开始前核对

| 项目 | 结果 |
|---|---|
| chainstaff PR #15 | 仍为草稿，head `6014113`，base `claude/project-thread-93pscn`（PR #13） |
| KAGOUAI `main` | `1957bb3`（PR #9 的内容，`/demo/` 已上线） |
| 现有测试 | `pytest`：459 通过，3 个测试网测试默认跳过；`tsc` + `vite build` 通过 |
| 已有能力 | Python 后端、Dockerfile、Caddy、`/healthz`、Workforce API、Mission API、SSE、SQLite 均在 PR #15 中，直接复用 |

### 已完成

1. **前端 API 梳理**：控制台共调用 50 个 API 路径，全部有后端路由，数据结构一致。新增自动核对测试，以后前端多调一个后端没有的路径会直接失败。
2. **Demo / Live 双模式**
   - Live 模式下任何请求连不上（网络错误、502/503/504）或 SSE 断开，页面顶部出现红色横幅：原因、数据停在几点、正在重试、手动重试按钮。恢复后自动消失。**没有任何回退到示例数据的路径。**
   - 顶栏在没有模型 key 时显示 `Model off`，点开到 Settings。
   - 新增 `GET /api/system`：模式、版本、数据库是否持久（只给文件名，不暴露路径）、模型 / 链 / 浏览器 API / 锚定钱包是否配置、写权限（付款、Agent 签名）、CORS 名单。Demo 构建自己回答 `mode: "demo"`。
   - Settings → System 新增 Server 一栏显示上述信息。
3. **环境变量**：新增 `KAGOU_CORS_ORIGINS`、`KAGOU_VERSION`；`PORT`（Railway）；`kagou.env.example` 补全。
4. **CORS 和认证**
   - `KAGOU_CORS_ORIGINS` 精确名单，拒绝 `*`、非 https（localhost 除外）、带路径的值；配置错误时服务器拒绝启动。不允许携带凭据。
   - `/api/chain/ws` 检查 `Origin`（CORS 管不到 WebSocket）：名单外的网页直接 4403。
   - 前端支持 `VITE_API_BASE`，可以把控制台放在别的域名。
   - 认证沿用 PR #15 的所有者令牌和只读演示令牌。
5. **部署健壮性**
   - 新增 `/readyz`（数据库可读才 200），Docker `HEALTHCHECK`、Caddy、Railway 都用它。
   - 启动时检查数据目录可写，不可写直接退出并说明原因（防止 Railway 卷权限问题导致数据存不住而不自知）。
   - uvicorn 加 `--timeout-graceful-shutdown 10`。**发现的问题**：原来重启时 uvicorn 会一直等 SSE 连接关闭，浏览器开着就停不下来；现在最多等 10 秒。
   - 新增 `railway.toml`。
6. **真实进程验证脚本** `scripts/live_check.py`：启动真实 uvicorn + 真实 SQLite 文件，或连接已部署的服务器（`--url`），逐项检查并输出 JSON。CI 里两处运行：主 job 用模拟链节点（稳定），`bsc-testnet` job 用真实 BSC 测试网。

### 测试结果

**自动化测试（模拟）**

| 项目 | 结果 |
|---|---|
| `pytest --cov=agent --cov-fail-under=99` | **468 通过**，3 跳过（测试网），覆盖率 ≥ 99% |
| 新增 `tests/test_live_mode.py` | 9 项：CORS 解析、名单内外的跨域头、默认无 CORS、错误配置拒绝启动、WS Origin 检查、`/readyz` 数据库故障返回 503、`/api/system` 有无模型和链、前端路径核对 |
| `npm run build`、`npm run build:demo` | 通过 |
| 已知偶发 | `test_bsc_perf.py::test_block_processing_p95_under_50ms` 在带覆盖率、机器负载高时失败过一次（P95 计时），单独运行 3/3 通过 |

**真实进程（真实 HTTP、SSE、SQLite、重启；链为本地模拟节点，没有模型）** — `python scripts/live_check.py --sim-chain`：**19/19 通过**

| 检查 | 结果 |
|---|---|
| 服务器启动到 `/readyz` 就绪 | 1.4 秒 |
| 无令牌访问被拒 | 401 |
| `/api/system` | `mode=live`，数据库持久，模型未配置（写明原因），付款和 Agent 签名都关闭 |
| SSE 连接 | 通过 |
| Agent 创建、修改后读回 | 通过（角色、Prompt、预算） |
| Mission 预检 | 只有一条提示：没有测试网钱包，审批后只能链下完成 |
| Mission 创建；同一 `Idempotency-Key` 再提交 | 同一个 Mission，没有重复 |
| Mission 执行到待审核 | 1.0 秒（规则规划 + 2 个只读工具 + 报告） |
| 审批前后的证明状态 | `awaiting_review` → `completed / offchain_completed`（没有被标成链上确认） |
| SSE 收到的事件 | `user.instruction, task.created, goal.submitted, agent.started, plan.created, task.assigned, agent.working, tool.start, tool.done, task.completed, task.result, …, goal.review, approval.requested` |
| 重启后（同一数据库文件） | Agent 修改、Mission 状态、幂等键都还在 |

没有链时（`python scripts/live_check.py`）：Mission 在创建时就被拒绝，原因“服务器没有配置链（CHAINSTAFF_CHAIN），链上分析技能都会失败”——不会先创建再失败，也不会用假数据跑完。

**真实浏览器（Chromium，Playwright；真实后端进程）**

| 检查 | 结果 |
|---|---|
| 正常连接 | 无横幅；顶栏显示 `Model off` |
| 停掉服务器 | 桌面（1440×900）和手机（390×844）都出现红色横幅：“实时事件流断开（Failed to fetch）。页面上的数据停在 …，不是实时数据。正在自动重试。” |
| 恢复服务器 | 横幅自动消失 |
| Settings → System | `Live · 真实后端`、`SQLite · k.db`、“关闭：只读链上数据 · Agent 不能签名或转账” |
| 页面 JS 错误 | 0 |

### 真实 AI 调用验证

**没有做。** 没有 `ANTHROPIC_API_KEY`。本阶段所有 Mission 用规则规划和只读工具完成，不涉及模型；控制台和 `/api/system` 都明确显示模型未配置。

### 真实 BSC 调用验证

本沙箱连不上 BSC 节点（DNS 被拦），真实测试网验证在 GitHub Actions 的 `bsc-testnet` job 里进行：除 PR #15 已有的 3 项冒烟测试，新增一次 `live_check.py --chain`（真实服务器进程 + 真实测试网 + 新区块监听，分析 WBNB 测试网合约）。结果见 PR 的 CI 运行记录，会补到这里。

### 性能

只有上面的单机数字（启动 1.4 秒；无模型的 Mission 到待审核 1.0 秒）。没有做负载测试。

### 安全

| 问题 | 处理 |
|---|---|
| 跨站 WebSocket（CORS 不覆盖） | 已加 Origin 检查 |
| 宽松 CORS 配置 | 不允许 `*` 和非 https，配置错直接拒绝启动 |
| `/api/system` 泄露 | 需要令牌；只给数据库文件名，不给路径；不含任何 key |
| 重启卡在 SSE 连接上 | 加优雅停机超时 |
| 仍未解决 | 单一令牌，没有多用户和角色；没有 API 限流（后续安全阶段） |

### 修改文件

`.github/workflows/ci.yml`、`Dockerfile`、`agent/store.py`（`path`、`ping()`）、`backend/app.py`（CORS、`/readyz`、`/api/system`、WS Origin）、`backend/chain_api.py`、`deploy/app.kagouai.com/{Caddyfile,kagou.env.example}`、`web/terminal/src/{components/TerminalHeader.tsx,components/TerminalShell.tsx,lib/api.ts,lib/chainstream.ts,lib/demo.ts,lib/queries.ts,lib/stream.ts,lib/types.ts,pages/Settings.tsx,styles.css}`

### 新增文件

`railway.toml`、`scripts/live_check.py`、`tests/test_live_mode.py`、`web/terminal/src/lib/health.ts`、`docs/KAGOU_{PRODUCTION_ROADMAP,ARCHITECTURE,IMPLEMENTATION_REPORT,DEPLOYMENT_GUIDE}.md`

### 部署步骤

见 [KAGOU_DEPLOYMENT_GUIDE.md](KAGOU_DEPLOYMENT_GUIDE.md)。没有部署任何东西。

### 未完成（本阶段范围内）

- 真实模型、真实域名、Railway 上的持久化：都等你提供 key / 账号后验证。
- SSE 仍是 0.5 秒轮询 SQLite；单实例够用。
