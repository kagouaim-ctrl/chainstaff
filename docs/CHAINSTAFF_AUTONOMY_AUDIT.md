# ChainStaff 自主 Agent 平台 · Phase 0 代码审计

分支：`claude/project-thread-zekqaw`，从 PR #16（`claude/project-thread-v5zo4i`，`5554794`）切出，叠在 #16 → #15 → #13 → #12 → #11 → #10 之上。

审计时的基线：`pytest` 496 通过、3 跳过（BSC 测试网，需要 `KAGOU_TESTNET=1`）；`web/terminal` 的 `tsc -b && vite build` 通过。

---

## 1. 现有功能清单

| 层 | 模块 | 现状 | 真实 / 演示 |
|---|---|---|---|
| 持久化 | `agent/store.py` | 一个 SQLite 文件（WAL），一个串行连接 + RLock，所有子系统共用 | 真实 |
| Agent 配置 | `agent/workforce/db.py` `wf_agents` | 名称、角色、描述、System Prompt、模型、技能、权限、预算、并发、状态 active/paused/archived | 真实、持久化 |
| 工作区 | `wf_workspaces` | 名称、描述、成员（由 Squad 合并而来） | 真实，**没有**仓库 / 合约 / 数据源字段 |
| Squad | `wf_squads` + engine | 主管 + 成员，Mission 只能派给 Squad 内的人 | 真实，但是用户必须先建 Squad，和本次目标冲突 |
| 任务 | `wf_tasks` | goal（Mission）+ subtask，8 种状态，依赖、重试、超时、预算、幂等键 | 真实 |
| 消息总线 | `wf_messages` | 定向消息 queued → delivered → processed，去重键，超时重投，重启恢复 | 真实，但只在「主管 → 员工 → 主管」一条线上用 |
| 调度 | `agent/workforce/engine.py`（1881 行） | 主管规划（模型或规则）→ 按依赖派发 → 线程池执行 → 收集 → 审批 | 真实，**一次性**：目标完成后整条线结束 |
| Agent 推理 | `agent/workforce/runtime.py` | 手写的模型 ↔ 工具循环，逐次鉴权，逐次记成本，取消，截止时间 | 真实（需要 key） |
| 模型网关 | `gateway.py`、`openai_compat.py`、`models.py` | Anthropic（默认 Claude Haiku 5.5）、Qwen / Gemini / OpenRouter / 本地；日 / 月预算、熔断、并发槽、`wf_ai_usage` 记账 | 真实 |
| 工具 | `skills.py`、`chaintools.py` | 7 个只读链上技能 + 额外只读链上工具 | 真实（读 BSC） |
| 记忆 | `wf_memory` + FTS5 | agent / workspace / project 范围，过期 | 真实 |
| 定时 | `wf_schedules` + `cron.py` | cron 触发一个新 goal | 真实，但触发的是一次性 goal |
| 审批 | `wf_approvals` | 任务结果、超预算、上链 | 真实 |
| BSC 数据层 | `agent/bsc/` | RPC 池、WebSocket 新区块、补块、重组、确认数、地址 / 合约 / 代币转账监控、事件触发 goal | 真实（CI 在测试网验证过）。重组时**不回调** `on_event` |
| API | `backend/workforce_api.py`、`backend/app.py`、`backend/chain_api.py` | `/api/wf/*`、SSE（Last-Event-ID 续传）、`/api/chain/*` + WS、`/readyz`、`/api/system`、CORS、只读演示令牌 | 真实 |
| 部署 | `Dockerfile`、`railway.toml`、`deploy/` | 单进程 uvicorn：API、引擎、监听、后台巡检都在一个进程里 | 已准备，未部署 |
| 前端 | `web/terminal/`（React + TS + React Query） | Terminal、Agents、Squads、Workspace、Tasks、On-Chain、Skills、More（Activity、Settings） | 全部连真实 API；示例数据只在 `VITE_DEMO=1` 的静态演示构建 |

## 2. 可复用模块

- **存储层**：`Store` 的串行连接和 `WorkforceDB.atomic()`，新表放同一个文件。
- **Agent 配置表** `wf_agents`：加列，不新建第二张 Agent 表，老数据直接可用。
- **模型调用**：`AnthropicProvider`、`OpenAICompatProvider`、`Gateway`（预算 / 熔断 / 并发 / 记账）、`models.cost_of`。新的运行时直接用这些 provider，不再写一套。
- **只读链上工具**：`SKILL_BY_ID` 里的技能作为新运行时的工具（`chain:read`）。
- **记忆**：`wf_memory` 和全文检索，作为 Memory Service 的存储。
- **BSC 数据层**：`ChainService` 的监控、确认、重组；Token Watcher 直接建在它上面，不再写一个链上监听。
- **SSE 续传**：`/api/wf/stream` 的写法，新 Activity 流照抄。
- **审计**：`store.audit`。
- **前端**：设计系统（`styles.css` 的 token）、`DataTable`、`ui.tsx`（Status、Tabs、Dialog、Sheet、Cell…）、`api.ts`（含幂等 POST）、`health.ts`（断线横幅）、`stream.ts`。

## 3. Mock / 演示模块

| 位置 | 说明 | 处理 |
|---|---|---|
| `web/terminal/src/lib/demo.ts` + `public/demo-data.json` | `VITE_DEMO=1` 时的静态快照，给 `www.kagouai.com/demo/` 用 | 保留（不碰 `/demo/`），Live 模式不会走到 |
| `tests/support/scripted_chat_server.py` | 脚本化的 OpenAI 兼容模型，只在测试 / CI | 保留，扩展给端到端测试用 |
| 规则规划（`planner.py` 无模型时） | 只认目标里的 0x 地址 | 保留在旧流程；新运行时**没有**规则替身：没配模型时任务失败并写明原因 |

Live 模式里没有发现随机数、假在线状态或写死的数量。问题不在「假数据」，而在**产品模型**：

- Agent 的「在线」只有 active / paused，`live` 是从子任务推出来的 busy / idle，没有 Watching、Waiting、Offline 的概念；Worker 挂了界面上也不会显示 Offline。
- 没有「长期职责」，Agent 只能被主管拆分任务时用到。
- Agent 之间不能主动交接任务：只有主管 → 员工 → 主管，员工之间的 `handoff` 只是把上游结果递给依赖它的子任务。

## 4. 前端需删除的入口

| 入口 | 原因 | 处理 |
|---|---|---|
| 顶栏 Squads | 不再要求先建 Squad | 从导航删除；路由和 API 保留在后台兼容层，旧链接跳到 Agents |
| 顶栏 Skills | 不占主页面 | 移到 Settings → Tool Library 和 Agent 详情 → Tools |
| 顶栏 Tasks | 并入 Missions | 删除；`#/tasks` 保留为旧流程（主管拆分的 Mission）的查看页，从 Missions 页进入 |
| 顶栏 On-Chain | 不在新导航里 | 删除顶栏入口；链上数据从 Workspace（合约 / 代币）进入，原页面路由保留 |
| More 菜单 | 新导航没有 More | 删除 |
| Terminal 的「By function」「Favorites」等分类、QuickActionBar 的四格指标 | Overview 换成六个后端指标 | 删除 |
| AgentTable 里的 Run / Assign（直接跑技能、交给 Squad） | 和新的「职责 + 事件唤醒」模型重复 | 从 Agents 列表删除；Agent 详情里用 New task（交给这个 Agent 一个结构化任务）代替 |

## 5. 前端需修改的组件

- `TerminalHeader`：导航改为 Overview / Agents / Missions / Workspace / Activity / Settings；删除 More；保留搜索、网络、通知、模型状态。
- `TerminalShell`：手机底部导航改为 Overview / Agents / Missions / Activity / Settings。
- `Terminal.tsx` → `Overview.tsx`：六个指标 + Agent 状态表 + 右侧 Live Activity。
- `Agents.tsx`：新列表（Name、Responsibility、Workspace、Status、Current Work、Last Wake-up、Actions）+ 新建向导 + 详情页六个 Tab。
- `AgentForm.tsx`：换成七步创建向导（名称、职责、Workspace、工具和权限、工作模式、预算和审批、激活）。
- `Workspace.tsx`：从「Squad 下 Mission」改成「项目资源」：仓库、合约、代币、钱包、文档、API、数据源（Watcher）、Agent、Mission。
- `Activity.tsx`：新活动中心，时间 / Agent / Workspace / 严重级别 / 类型过滤，每条可追溯。
- `Settings.tsx`：AI Models、API Keys、Tool Library、Tool Permissions、Notifications、Runtime、Billing、Audit Logs。
- `types.ts`、`queries.ts`：新 API 的类型和查询。

## 6. 后端缺失模块

| 模块 | 现状 | 要做的 |
|---|---|---|
| API Server | 有 | 新增 `/api/at/*`（autonomy） |
| Agent Runtime | 有，但绑在主管任务上 | 新运行时：每次执行是一个独立 Run，带职责、证据、记忆、同事目录、协作工具 |
| Wake-up Engine | 无 | 事件 → 触发规则 → 任务 → 唤醒 Agent |
| Task Queue | 有消息总线，没有租约 | 持久化队列：领取租约、心跳续租、过期回收、重试上限 |
| Mission Orchestrator | 只有一次性 goal | Mission 类型 One-time / Continuous / Event-driven / Scheduled，暂停 / 停止，执行历史 |
| Watcher Manager | 只有 BSC 监控 | 通用 Watcher：BSC、GitHub Webhook、CI 失败、应用错误日志、API 健康检查；心跳、去重、重试 |
| Tool Registry | 只有链上技能 | 协作工具（交接、完成、拒绝、请求审批、记忆）、代码沙箱工具、HTTP 检查，每个工具有权限和风险等级 |
| Model Gateway | 有 | 复用；新增每次调用的明细（延迟、失败、工具调用） |
| Memory Service | 有存储 | 复用，Agent 运行时自动读写 |
| Notification Service | 无 | 站内通知（KAGOU 没有 Telegram / 外发 webhook，这一条不改） |
| Approval Service | 有，只服务 goal | 新的审批：补丁 / PR 审核、高风险工具 |
| Audit Service | 有 `store.audit` | 复用，Activity 写一份可过滤的结构化记录 |
| 云端 Worker | 无，引擎是 API 进程里的线程 | 独立 Worker 进程 + 心跳；进程管理器负责崩溃重启；API 进程不执行 Agent |
| 隔离执行 | 无 | 代码沙箱：每个任务一个工作副本、清空环境变量、资源上限、超时、不经过 shell |

## 7. 数据库需要调整的结构

全部是新增列 / 新增表，启动时自动迁移，旧数据不动。

| 表 | 变更 |
|---|---|
| `wf_agents` | 加 `responsibility`、`work_mode`（continuous / event / on_demand / scheduled）、`schedule_cron`、`workspace_ids`、`approval_rules`、`notify_rules`、`template` |
| `wf_workspaces` | 加 `resources`（仓库、合约、代币、钱包、文档、API、服务） |
| `at_missions` | 新表：类型、目标、Agent、触发器、状态、权限、预算、成本、结果 |
| `at_tasks` | 新表：结构化交接任务（task_id、from_agent_id、to_agent_id、workspace_id、mission_id、objective、priority、evidence、status、created_at、deadline、result，以及 depth、链路、去重键、租约、重试、checkpoint） |
| `at_runs` | 新表：每次执行（Agent、任务、Worker、开始 / 结束、模型调用、工具调用、Token、成本、错误、日志） |
| `at_events` | 新表：event_id、source、workspace_id、timestamp、payload、severity、processing_status、去重键、确认状态 |
| `at_watchers` | 新表：类型、配置、密钥哈希、状态、心跳、游标、连续失败 |
| `at_activity` | 新表：活动中心（谁触发、为什么、交给谁、做了什么、结果、是否要人处理） |
| `at_notifications`、`at_approvals`、`at_workers`、`at_artifacts`（补丁） | 新表 |

## 8. 实施计划（按要求的顺序）

1. **Phase 1 前端精简**：导航、Overview、Agents 列表 / 创建 / 详情。因为 Overview 的数据必须来自后端，这一阶段同时加上 Agent 新字段、Workspace 资源和 `/api/at/overview`，执行引擎留到 Phase 2。
2. **Phase 2 持久化运行时**：`agent/autonomy/` 包（schema、store、queue、runtime、worker、supervisor），独立 Worker 进程、心跳、租约、崩溃恢复、checkpoint。
3. **Phase 3 协作**：交接工具、自动匹配、唤醒引擎、状态机、防循环（深度、重试、去重、超时、预算、环路、并发）。
4. **Phase 4 触发器**：GitHub Webhook、CI 失败、应用错误日志、API 健康检查、BSC Token Watcher（建在 `ChainService` 上，处理确认和重组）。
5. **Phase 5 端到端**：三个 Agent 修 Bug（测试项目 `examples/buggy-service/`）、Token Watcher 长时间运行（CI 里连 BSC 测试网）、Worker 重启恢复、浏览器关闭后继续运行。

不删除的后台模块：主管规划流程、Squad、旧控制台（`/`）的六个岗位、付款规则引擎、上链锚定。它们继续可用，只是不再出现在新导航里。
