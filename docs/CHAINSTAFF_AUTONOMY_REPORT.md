# ChainStaff 自主 Agent 平台：实施报告

分支 `claude/project-thread-zekqaw`，叠在 Draft PR #16（`claude/project-thread-v5zo4i`）之上。没有部署任何环境，没有做任何链上写操作。

结论先说：

- 不需要模型密钥的部分已经在本机真实运行过：任务队列、唤醒、委派、隔离环境测试、审批、Worker 崩溃恢复、Activity 追踪。验证方式是 API 进程、独立 Worker 进程和一个本地模型服务三个进程一起跑。
- 这些运行里的"模型"是一个按固定规则回答的脚本服务（`tests/support/autonomy_brain.py`），属于 **mock**。它证明的是运行时本身，不能证明模型的分析能力。
- 用真实模型跑这条流程还没做过，因为这个环境没有 API Key。
- BSC 测试网的 Token Watcher 检查已经写好并加进 CI。沙箱连不上 BSC 节点，所以要等 PR 的 CI 跑完才有结果。

## 1. 修改了哪些文件

和 PR #16 相比，共 70 个文件，新增约 11,200 行，删除约 275 行。

后端（新增 `agent/autonomy/` 包）

| 文件 | 内容 |
|---|---|
| `db.py` | 新表：at_missions / at_tasks / at_runs / at_steps / at_events / at_watchers / at_activity / at_notifications / at_approvals / at_artifacts / at_workers / at_token_holders / at_flows。和原账本共用同一个 SQLite 文件（WAL），给 wf_agents 加了列，不改原有列 |
| `constants.py` | Agent 状态、任务状态、循环保护的上限（都能用环境变量调） |
| `agents.py` | Agent 模板（Bug Hunter、Investigator、Fix Agent、Token Watcher、Contract Monitor 等），Agent 状态从数据库推导，接收方自动匹配并给出每个候选的理由 |
| `wakeup.py` | 结构化任务：创建、委派、指派、拒绝后重新分配、取消、重试、截止时间，以及全部循环保护 |
| `runtime.py` | 一个任务的一次有上限的模型运行：工具调用每次都在服务端检查权限，预算、调用次数上限，检查点续跑 |
| `tools.py` | Agent 能用的工具：完成、拒绝、委派、通知、记忆、读事件、隔离副本里的读写和搜索、跑测试、提交补丁、API 检查、链上流向和持有人 |
| `sandbox.py` | 每个任务一份 git 副本；测试在清空的环境变量里跑，能用 `unshare` 时断网，并有资源限制；补丁审批通过后应用到本地仓库，或者推分支开 Draft PR |
| `events.py` / `watchers.py` | 事件入口、去重、按指纹归并、路由（多个 Mission 匹配时只唤醒最合适的一个），以及 Watcher：应用日志、CI、GitHub Webhook（HMAC 校验）、API 健康、BSC 事件（确认数、重组、持有人余额） |
| `missions.py` | Mission 的四种类型（一次性、持续、事件驱动、定时），触发规则，Agent 的长期职责 |
| `worker.py` / `supervise.py` | 云端 Worker（持久队列、租约、心跳、崩溃恢复、并发），以及同时管 API 和 Worker 两个进程、退出就重启的 supervisor |
| `service.py` / `activity.py` / `github.py` / `workspaces.py` | 对外服务层、活动日志和通知、GitHub Draft PR、Workspace 资源校验 |

- `backend/autonomy_api.py`：新增的 `/api/at/*` 接口，加上 `/api/ingest/{logs,ci,github}/{watcher}` 三个回调入口（带鉴权和限流）。
- `backend/app.py`：挂载上面的接口。
- 小改动：`agent/bsc/listener.py` 和 `agent/bsc/service.py` 把链上事件转交给 autonomy 的 Watcher；`agent/workforce/engine.py` 改了 5 行。

前端（`web/terminal/`）

- 新增页面：`Overview`、`AgentWizard`（7 步）、`Missions`、`WorkspaceAt`、`ActivityAt`、`SettingsAt`，以及组件 `components/AtBits.tsx` 和 `lib/at.ts`。
- 修改：`Agents.tsx`（列表和 6 个详情标签页）、导航（`TerminalHeader`、`TerminalShell`）、`main.tsx`、`styles.css`。
- 改名：旧的 Activity、Settings、Workspace 页面改名为 `Log`、`SettingsLegacy`、`Board`，从 Settings → Legacy 进入。

测试、脚本和部署

- 新增 10 个测试文件，共 84 个测试：`tests/test_autonomy_*.py` 和 `tests/autonomy_kit.py`。
- 新增 `tests/support/autonomy_brain.py`（mock 模型）和 `tests/support/scripted_autonomy_server.py`。
- 新增 `scripts/autonomy_check.py`（端到端检查）和 `examples/buggy-service/`（一个带除零 bug 的小服务，用来演示）。
- 部署相关：`Dockerfile` 的启动命令改为 supervisor，并安装 git；`railway.toml` 只改了注释；`.github/workflows/ci.yml` 加了两个步骤；`docs/KAGOU_DEPLOYMENT_GUIDE.md` 写了环境变量；`docs/CHAINSTAFF_AUTONOMY_AUDIT.md` 是阶段 0 的审计。

## 2. 删除了哪些功能入口

- 主导航从原来的 7 项（Terminal / Agents / Squads / Workspace / Tasks / On-Chain / Skills）改成 6 项：**Overview / Agents / Missions / Workspace / Activity / Settings**。
- 从导航里拿掉的有 Squads、Skills、Terminal、Tasks、On-Chain。
  - 这些页面和它们的后端都还在，从 Settings → Legacy 进入，没有删代码。
  - Squads 的数据仍然保留在后端。
- Skills 现在出现在两个位置：Agent 详情 → Tools，以及 Settings → Tool Library。
- 不需要 AI Manager：任务的接收方由服务端按权限、工作区和职责自动匹配。

## 3. 保留了哪些原有功能

- 原有的财务员工、审批规则引擎和账本（`agent/`）没有改动，旧接口 `/api/wf/*` 也全部保留。
- BSC 监听保留了 WebSocket、重连、补块和多节点切换；模型网关保留了多家模型、预算和熔断。
- KAGOU 落地页、`/demo/`、品牌、深色背景加黄色强调的风格都没动。
- 原有的测试全部通过。

## 4. 新增了哪些后端能力

- **结构化委派**：任务字段包括 task_id、from、to、workspace、mission、objective、priority、evidence、status、created_at、deadline、result。
  - 接收方自动匹配，每个候选都写明为什么选或为什么不选。
  - 没有合适的 Agent 时，任务标为 unassigned 并通知用户，用户可以手动指派。
- **Agent 状态**：Watching / Working / Investigating / Waiting / Waiting Approval / Paused / Offline / Error。
  - 状态都从数据库推导，不单独存一份。
  - 没有在线 Worker 时显示 Offline，不显示假在线。
- **循环保护**：
  - 委派深度
  - 循环委派（链上出现过的 Agent 不能再接）
  - 重试次数
  - 按指纹去重
  - 超时和截止时间
  - 任务、Agent、Mission 和全局四级预算
  - 每小时的委派次数和事件唤醒次数
  - 同一问题最多修复几次
  - Agent 和 Worker 的并发上限
  - 一次运行内的模型调用和工具调用次数上限
- **Worker**：
  - 持久队列，用租约和比较后设置（compare-and-set）认领任务，两个 Worker 不会跑同一个任务。
  - 心跳；租约过期后由别的 Worker 接手。
  - 任务被中断后从检查点续跑，中断两次判为失败并通知用户。
  - supervisor 负责重启退出的进程。
- **Watcher**：
  - 应用日志、CI 结果和 GitHub Webhook，都带签名或令牌校验。
  - API 健康检查：掉线时唤醒 Agent，恢复时唤醒 Agent 去验证。
  - BSC 事件：处理确认数和重组，记录持有人余额和资金流向。
  - Token Watcher 的提示词和工具都要求只报告事实，"一笔转账本身不能说明任何不法行为"。
- **Bug 流程**：
  1. Monitor Agent 被事件唤醒。
  2. 它把任务委派给 Investigator，Investigator 在隔离副本里定位原因。
  3. Investigator 委派给 Fix Agent。Fix Agent 改代码、跑测试、提交补丁，在 GitHub 仓库上开 Draft PR。
  4. 人工审批。
  5. 审批通过后自动建一个验证任务。

  每一步都有真实的任务 ID、运行记录、步骤日志和 Activity 记录。
- **安全**：
  - Agent 创建后默认只读，高权限要显式给。
  - 补丁、PR 合并、部署、合约操作和资金操作都不能由 Agent 自己完成。补丁一定要人工批准；Agent 没有合并、部署和签名的工具。
  - 私钥和令牌不会进入模型上下文：测试环境清空了环境变量，GitHub 令牌只出现在 git 子进程里。
  - 跨 Agent 的任务都要检查接收方的权限。
- **模型网关**：沿用现有的多厂商网关，记录 token、调用次数、延迟、成本、失败和工具调用，Settings → Billing 和 Agent 详情里都能看到。

## 5. 哪些功能已经真实运行

下表里"本机进程"指真实的 uvicorn API、独立 Worker 进程和真实的 SQLite 文件。模型都是 mock。

| 功能 | 怎么验证的 |
|---|---|
| 通过 API 和界面创建 Agent、Mission、Workspace、Watcher，重启后数据还在 | 本机进程，界面截图 |
| 日志 → Agent 1 → Agent 2 → Agent 3 → 隔离测试 → 待审核补丁 → 界面上点批准 → 补丁应用 → 验证任务通过 | `scripts/autonomy_check.py`，以及界面上的手动操作 |
| 重复的错误日志被去重，只产生一次调查 | 同上 |
| 任务运行中 SIGKILL 杀掉 Worker，新 Worker 在租约过期后接手并完成，留下 interrupted 运行记录 | 同上 |
| Overview 的 KPI、Agent 状态、Activity 和后端数据库一致；SSE 实时推送 | 本机进程和截图；契约测试检查前端用到的每个接口都存在 |
| GitHub 仓库补丁：推分支、开 Draft PR | 推到本地的 bare 仓库，PR 请求被替换成假的，没有调用真实的 GitHub |
| BSC 确认数、重组、持有人 | 用模拟链测试，没有真实链 |

`autonomy_check.py` 最后一次本机运行结果是 21/21 通过（mock 模型）。

## 6. 哪些仍然需要 API Key 或云端配置

- **模型**：设置 `AI_PROVIDER`、`AI_API_KEY`、`AI_MODEL`（或 `ANTHROPIC_API_KEY`）。没有配置时，Agent 被唤醒后任务会失败并写明原因，不会假装完成。
- **云端常驻**：要有人在 Railway 或 Docker 上部署（我没有部署）。需要一个挂在 `/data` 的卷，启动命令已经是 supervisor。
- **GitHub Draft PR**：设置 `KAGOU_GITHUB_TOKEN`，需要目标仓库 Contents 和 Pull requests 的读写权限，并在仓库里配置 Webhook 指向 `/api/ingest/github/<watcher>`。
- **BSC**：设置 `CHAINSTAFF_CHAIN`，节点要能联网。
- **本地仓库**：设置 `KAGOU_LOCAL_REPO_ROOTS`。
- **断网隔离**：容器平台不允许非特权用户命名空间时，测试照样能跑，但不断网，Settings → Runtime 会如实显示。
- 环境变量的完整说明见 `docs/KAGOU_DEPLOYMENT_GUIDE.md` 的"自主 Agent"一节。

## 7. 测试结果

- **通过**：
  - `pytest` 584 通过、3 跳过。跳过的 3 个是需要测试网的测试。
  - `--cov=agent` 覆盖率 99.01%，CI 门槛是 99%。
  - 前端 `tsc -b && vite build` 通过。
  - `scripts/autonomy_check.py`（mock 模型）21/21 通过。
  - `pip-audit` 没有发现漏洞。
- **没有在本机跑**：
  - semgrep：沙箱拿不到规则，CI 会跑。
  - Docker 镜像构建：沙箱没有 Docker daemon，镜像只改了两处（加装 git、换启动命令），在 CI 和 Railway 构建时会验证。
  - 测试网 Token Watcher：沙箱连不上 BSC，由 CI 的 `bsc-testnet` 任务跑。
- **没有跑过**：任何真实模型的测试。

## 8. 12 项验收测试

| # | 验收项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 自由创建 Agent，赋予任意职责 | 通过（本机） | 7 步向导，或直接调 API；职责是自由文本 |
| 2 | Agent 有真实的持久化配置 | 通过（本机） | 存在 SQLite 里，重启后还在，有测试覆盖 |
| 3 | 关闭浏览器后云端任务仍然运行 | 部分 | Worker 是独立进程，和浏览器无关，端到端检查全程没开浏览器。但没有部署到云上，"云端"这一点没验证 |
| 4 | Agent 1 发现 Bug 后自动给 Agent 2 提交任务 | 通过（mock 模型） | 委派由真实运行时完成，"决定委派"这一步来自 mock |
| 5 | Agent 2 真正调用 AI 和工具分析 | **未通过：没跑** | 工具调用是真的（搜索、读真实代码），模型是 mock。需要 API Key 才能验证 |
| 6 | Agent 2 把任务交给 Agent 3 | 通过（mock 模型） | 同第 4 项 |
| 7 | Agent 3 在隔离环境生成修复并跑测试 | 通过（mock 模型） | 隔离副本和测试是真实的，修改内容由 mock 给出；原仓库在审批前保持不变 |
| 8 | 所有交接都能在 Activity 追踪 | 通过（本机） | 每条记录都写明谁触发、为什么、交给谁、结果，以及是否需要人处理 |
| 9 | Worker 重启后未完成任务能恢复 | 通过（本机） | 用 SIGKILL 杀进程后新 Worker 接手 |
| 10 | 前端状态和后端一致 | 通过（本机） | 状态从数据库推导；契约测试；截图核对 |
| 11 | Token Watcher 持续监听 BSC 测试网 | **待 CI** | 检查已写好；只证明几分钟内的监听，不是长时间稳定性测试 |
| 12 | 第二天登录能看到前一天的进度和记录 | 部分 | 数据持久化，Activity 能按时间筛选，重启后都在。没有做跨天的真实运行 |

## 9. 已知限制

- 单副本：两个进程共用一个 SQLite 文件。要扩展成多台机器，需要换成 Postgres。
- "Project Optimizer"有模板，但它会不会产生无意义的代码改动，取决于真实模型，没有验证过。目前它只有读代码和跑测试的权限，没有提交补丁的权限。
