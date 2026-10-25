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

本沙箱连不上 BSC 节点（DNS 被拦），真实测试网验证在 GitHub Actions 的 `bsc-testnet` job 里进行（[运行记录](https://github.com/kagouaim-ctrl/chainstaff/actions/runs/40169456021/job/121097160117)，2026-10-25 06:33 UTC，chain id 97）：

| 检查 | 结果 |
|---|---|
| 3 个公共 RPC 节点 | 全部返回 chain id 97；延迟 353 / 215 / 310 ms；区块 138798223–138798224 |
| WebSocket 新区块监听 | `wss://bsc-testnet-rpc.publicnode.com`，6.7 秒跟到 13 个区块，补块 1 次，0 次重连，0 次重组；单区块处理 P50 170 ms，P95 190 ms |
| 合约读取 | WBNB `symbol()=WBNB`、`decimals()=18`；近 200 个区块 43 条 Transfer |
| `live_check.py --chain`（真实服务器进程 + 真实测试网 + 新区块监听开着） | **19/19 通过**：分析 WBNB 测试网合约的 Mission 2.0 秒到待审核，审批后 `offchain_completed`，重启后 Agent、Mission、幂等键都在 |

这是规则规划 + 只读工具，没有模型参与。

### 性能

只有单机数字：启动 0.9–1.4 秒；无模型的 Mission 到待审核 1.0 秒（模拟链）/ 2.0 秒（真实测试网）。没有做负载测试。

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

---

## AI Runtime 接入：Qwen、AI Model Gateway、成本控制（2026-10-25）

对应需求“KAGOUAI ChainStaff — AI Runtime Integration”，并入第 2 阶段，在同一分支上继续，没有重写。

### 1. 先检查现有项目（结论）

| 问题 | 结论（检查依据） |
|---|---|
| Agent 是否已有真实推理 | **有**。`agent/workforce/runtime.py` 的 `AgentRuntime` 是真实的模型 ↔ 工具循环（PR #15 起），以前只接了 Anthropic，并且需要 key |
| Mission 能否实际执行 | **能**。`engine.py` 调度、`planner.py` 规划；无模型时用规则规划（只认 0x 地址）；`live_check.py` 在真实进程上跑通（见第 1 阶段） |
| BSC 模块是否连真实 RPC | **是**。`agent/bsc/rpc.py` 的 `RpcPool`（多节点、重试、限流）；CI 在 BSC 测试网上验证（chain id 97） |
| Mock / Fake / Hardcoded | Live 模式没有。示例数据只在 `/demo/` 静态构建（`VITE_DEMO=1`）里；测试里的脚本化模型和模拟链只在 `tests/` |
| 发现的问题 | ① 只有一个模型服务；② 没有全站的每日 / 每月预算、并发上限、熔断；③ 主管规划在没设 `CHAINSTAFF_MODEL_PRICE` 时成本记成 0（已修）；④ 没有“任意代币余额”工具；⑤ Agent 的结论没有要求区分链上事实和推断 |

### 2. 实际架构（新增部分）

```
Agent Runtime / Mission Planner
        │  每次模型调用
        ▼
AI Model Gateway (agent/workforce/gateway.py)
  ├ 预算：今日 / 本月花费 ≥ 上限 → 拒绝，明确报错（不静默继续）
  ├ 熔断：同一服务连续失败 N 次 → 暂停 M 秒，期间直接拒绝
  ├ 并发：AI_MAX_CONCURRENT_TASKS 个调用槽，其余排队（取消的任务不再等）
  ├ 上限：AI_MAX_AGENT_STEPS / AI_MAX_OUTPUT_TOKENS / AI_REQUEST_TIMEOUT（Agent 自己只能设得更低）
  └ 记账：wf_ai_usage 表，按 UTC 日、服务、模型累计调用、失败、Token、成本（重启不丢）
        │
        ├─ OpenAICompatProvider（openai_compat.py）：Qwen（首期）/ Gemini / Groq / OpenRouter / 本地 Ollama、vLLM
        └─ AnthropicProvider（runtime.py）：Claude，默认最便宜的 Haiku 5.5
        │
        ▼
Tool Registry（只读 BSC 工具，后端逐次鉴权）→ RpcPool → BSC
```

链上监听（`agent/bsc/listener.py`）不经过模型：新区块、事件过滤、重组、补块、断线重连都是后端规则；只有 Agent 任务才调用模型。

### 3. AI 接入

| 项目 | 内容 |
|---|---|
| 首期模型 | Qwen，阿里云 Model Studio（百炼）OpenAI 兼容接口 `POST {AI_BASE_URL}/chat/completions` |
| 地址（按官方文档核实） | 新加坡：`https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`；旧域名 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` 仍可用。北京、美国、东京各有自己的地址，key 按地域绑定，跨地域用会 401 |
| 模型 | 建议 `qwen-plus`：文档写明支持 function calling，且在新用户免费额度的举例里。更便宜的 `qwen3.7-flash`（≤32K 输入 $0.03 / $0.13 每百万 Token）也在 function calling 支持列表里，但它的模型页对新加坡区写着不支持 function calling，**两份文档矛盾，需要用真实 key 验证后再推荐** |
| 免费额度 | 只在新加坡区（International）：每个模型通常 100 万 Token，90 天，开通 Model Studio 后自动发放 |
| 日本东京 | 可以用：OpenAI 兼容地址 `https://{WorkspaceId}.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1`，要先在东京建工作空间（部署范围选 Global 或 Japan）。官方价格页在东京只列出 Flash 系列（Global 范围），例如 qwen3.7-flash ≤32K 输入 $0.028 / $0.110，比新加坡略低；没有列出 qwen-plus；免费额度文档只写了新加坡。东京标价已内置 |
| 价格 | 新加坡区内置官方美元标价（分档，按单次请求输入量）：qwen-plus $0.4 / $1.2（≤256K，思考模式输出 $4）、qwen3.7-plus $0.4 / $1.6、qwen-flash $0.05 / $0.4、qwen3.7-flash $0.03 / $0.13（≤32K）等。用标价计成本，所以免费额度期间记录的成本会高于实际账单，不会更低。北京区按人民币另有价格，需要 `AI_PRICE` |
| 思考模式 | 默认关（`enable_thinking: false`，与官方 function calling 示例一致，也更便宜）；`AI_ENABLE_THINKING=1` 打开 |
| 统一接口 | `ModelProvider.complete(model, system, messages, tools, limits, on_text, should_stop, timeout_s) -> ModelTurn`（同步，跑在 Agent 线程池里，和现有引擎一致，没有改成 async）。多轮、System Prompt、function calling、超时、重试（429 / 5xx，指数退避）、Token usage、调用日志（时间线 `model.start` / `model.done`）、成本 |
| 工具调用格式 | 不假设一致：Claude 格式的对话在出入口转换成 chat completions（`tool_calls` / `role: tool`）；参数不是合法 JSON 时作为错误结果交还模型，不猜 |
| 流式 | Claude 是流式；OpenAI 兼容接口目前**不流式**（阿里云 OpenAI 兼容文档写明 tools 不能和 `stream=True` 一起用），回答在调用结束时整段出现在时间线 |
| 本地模型 | `AI_PROVIDER=local` + `AI_BASE_URL=http://localhost:11434/v1`（Ollama）或 vLLM 的地址，无需 key，成本记 0。没有部署本地模型 |
| 密钥 | 只在服务器环境变量；不进浏览器、不进 `/api/system`、不进模型请求体；测试断言了这三点 |

配置示例：

```env
AI_PROVIDER=qwen
AI_BASE_URL=https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
AI_API_KEY=<阿里云 Model Studio 的 API Key>
AI_MODEL=qwen-plus
AI_MAX_OUTPUT_TOKENS=2048
AI_REQUEST_TIMEOUT=60
AI_DAILY_BUDGET_USD=1
AI_MONTHLY_BUDGET_USD=10
AI_MAX_AGENT_STEPS=8
AI_MAX_CONCURRENT_TASKS=5
```

配置不完整（例如只填了 `AI_PROVIDER`）或地址不是 https 时，服务器拒绝启动并说明缺什么，而不是带着“好像有模型”的状态运行。旧变量 `KAGOU_OPENAI_COMPAT_*` 仍然有效。

### 4. Agent Runtime

在 PR #15 的循环上没有改流程，只加了网关和输出要求：

接收任务 → 系统提示（角色 + 运行规则）→ 网关检查（预算、熔断）→ 等调用槽 → 模型 → 若要调用工具：后端逐次检查（是否提供给这个 Agent、权限是否还在、技能是否启用、参数 schema）→ 执行只读工具 → 结果作为**数据**交回模型（运行规则写明工具结果里的指令一律忽略）→ 继续推理 → 结论。

- 最大步数（模型调用次数）、工具调用次数、Token、单次超时、任务截止时间、取消、Agent 预算、Mission 预算：都有，到了就停并写明原因。
- 结论要求分三部分：链上事实（带工具、数值、区块）、统计（怎么算的）、推断（明确标注）。
- 每次调用记录：模型、Token、成本、是否有价格、用了哪个服务、耗时、调用了哪些工具。

### 5. BSC 工具

| 需求里的名字 | 实际工具 | 数据来源 |
|---|---|---|
| get_wallet_balance | `wallet_analysis` | `eth_getBalance` 等，带区块高度 |
| get_token_balance | **`token_balance`（本次新增）** | 任意 BEP-20 的 `balanceOf`，带符号、精度、区块 |
| get_transaction | `tx_lookup` | `eth_getTransactionByHash` + 回执 |
| get_token_metadata | `token_info` | `name` / `symbol` / `decimals` / `totalSupply` |
| get_wallet_transactions | `wallet_activity`（链上日志，最近 5000 个区块内的代币转账，不需要 key）；`tx_history`（完整交易历史，**需要 `ETHERSCAN_API_KEY`**） | 普通 RPC 拿不到一个地址的完整交易历史，也拿不到原生 BNB 转账列表；没有 key 时工具直接说明，不编数据 |
| monitor_wallet | 链上监控（`/api/chain/monitors`，后端 `ChainListener`）+ `chain_events` 工具 | 监听不调用模型；Agent 只在需要解释时查询记录下来的事件 |
| analyze_wallet | `agent_task`（Agent 用以上工具推理） | 结论按事实 / 统计 / 推断分开 |

工具都在注册表里标为只读，只有 `chain:read` 权限；没有任何签名、转账、授权、兑换工具。

### 6. Mission 状态

沿用现有状态（改名会破坏现有界面和数据）：

| 需求 | 现有 |
|---|---|
| PENDING / QUEUED | `pending`（已创建）、`assigned` / `waiting`（排队、等依赖） |
| RUNNING | `running` |
| COMPLETED | `review`（等人审核）→ `completed` |
| FAILED / CANCELLED | `failed` / `cancelled` |
| TIMED_OUT | `failed`，原因写“到了截止时间”或“工具超时”（没有单独的状态值） |

重试只重跑失败的只读子任务；链上写操作（锚定）不会被自动重试。监控在重启后从数据库恢复并补块（第 1 阶段已在测试网验证）。

### 7. 多 Agent 协作

不需要新代码：主管（Mission Coordinator）用模型把目标拆成带依赖的步骤，独立步骤并行，下游 Agent 只看到依赖步骤交来的结果（作为数据）。所有 Agent 共用服务器上的同一个模型 key。`scripts/ai_check.py` 会建这 4 个 Agent：Mission Coordinator、BSC Analyst、On-chain Monitor、Research Agent。防止无限互相调用：Agent 不能自己派任务给别的 Agent，只有主管规划，步骤数上限 12。

### 8. 测试结果

**Mock 测试（脚本化模型 / 模拟链节点，不是真实 API）**

| 项目 | 结果 |
|---|---|
| `pytest --cov=agent --cov-fail-under=99` | **496 通过**，3 跳过（测试网），覆盖率 99.08%。其中一次运行里 `test_bsc_perf` 的 P95 计时在高负载下失败 1 次，单独运行 3/3 通过，再跑全量全部通过（已知偶发，见第 1 阶段） |
| 新增 `tests/test_openai_compat.py`（12 项） | 多轮工具调用、工具结果回传、未授权工具被拒、错误参数、截断、重试 429 / 5xx / 网络错误、坏 key 不重试、取消、免费 / 付费 / 无价格的计价、主管规划、预检 |
| 新增 `tests/test_ai_gateway.py`（14 项） | 每日预算耗尽后拒绝新调用（API 收不到请求）、月预算跨重启累计、预算耗尽时规划退回规则规划、熔断打开和恢复、规划失败计入熔断、并发上限（3 个任务同时只有 1 个调用）、排队时取消、服务器上限压过 Agent 设置、环境变量校验、Qwen 配置和分档价格、端到端 Qwen 成本、`build_workforce` 读取 `AI_*` |
| `tests/test_live_mode.py` 新增 1 项 | `/api/system` 报告 Qwen 服务、模型、地址和花费，且不含 key |
| `tests/test_bsc_edges.py` 新增 1 项 | `token_balance` |
| `npm run build`、`npm run build:demo` | 通过 |

**脚本检查（真实服务器进程 + 真实 HTTP，模型是脚本化接口，链是模拟节点）** — `scripts/ai_check.py --sim-chain` 对 `tests/support/scripted_chat_server.py`：**8/8 通过**。这只证明检查脚本本身和服务器端流程能跑通，**不是**真实模型测试。CI 每次都会跑这一步。

**真实模型测试：未执行。** 没有 Qwen 或 Anthropic 的 key。需求里的测试一（AI 连接）、测试二（Agent 执行）、测试四（模型选择并调用工具）、测试六（多 Agent）的真实版本都等 key 到位后用 `scripts/ai_check.py --chain` 完成；测试三（真实地址余额）已在第 1 阶段的 BSC 测试网 CI 中通过（不经模型）；测试五（Mission 状态流转）、测试七（超时 / RPC 失败 / 取消 / 预算耗尽）、测试八（未授权工具被拒）有 Mock 测试和真实进程检查。

### 9. 修改文件

新增：`agent/workforce/gateway.py`、`agent/workforce/models.py`、`agent/workforce/openai_compat.py`、`scripts/ai_check.py`、`tests/support/scripted_chat_server.py`、`tests/test_ai_gateway.py`、`tests/test_openai_compat.py`、`.github/workflows/ai-check.yml`

修改：`agent/workforce/{__init__,chaintools,engine,planner,runtime}.py`、`agent/skills/translator/translator.py`、`backend/{app,workforce_api}.py`、`deploy/app.kagouai.com/kagou.env.example`、`.github/workflows/ci.yml`、`tests/{test_agent_runtime,test_bsc_edges,test_live_mode,test_team}.py`、`web/terminal/src/{components/AgentForm.tsx,components/SquadForm.tsx,components/TerminalHeader.tsx,lib/types.ts,lib/util.ts,pages/Settings.tsx}`、`docs/*`

前端只改了已有页面里的文字和状态：创建 Agent 时的模型选项显示实际服务（如“共享模型 · Qwen · qwen-plus”），Settings 显示共享模型、今日 / 本月 AI 花费和预算、并发和熔断状态。没有新页面，没有改 Landing Page，没有 Telegram。

### 10. 部署

见 [部署指南](KAGOU_DEPLOYMENT_GUIDE.md) 的变量表。没有部署任何东西。

### 11. 尚未完成

- 真实 Qwen / Claude 调用：等 key。
- `qwen3.7-flash` 在新加坡区是否支持 tool calling：文档矛盾，要实测。
- OpenAI 兼容接口不流式。
- 每日 / 每月预算对“无价格”的调用不完整（控制台会提示有多少次）。
- 单进程内的并发和熔断；多实例部署前要换成共享存储。
- 多用户、角色权限、API 限流仍没有（安全阶段）。

### 12. 下一步建议

1. 拿到 Qwen key 后跑 `scripts/ai_check.py --chain`，把真实结果写回本报告，再决定默认用 `qwen-plus` 还是更便宜的 flash 模型。
2. 用真实结果调整 Agent 的系统提示和工具描述。
3. 第 4 阶段的链上检索（持有人分布、流动性）需要先选数据源。
