# KAGOU 技术审计：ChainStaff 能否升级为链上版 Grok Bot

审计日期：2026-10-24　｜　性质：只读审计，未修改任何业务代码、数据库结构、前端或 Landing Page
审计对象：`kagouaim-ctrl/chainstaff`，`main` 与未合并的 PR 栈 #10 → #11 → #12 → #13

> 行号说明：除特别标注 `main@0879002` 或 `PR#11@0993f2d` 的位置外，**所有文件路径和行号都指 PR #13 的 head `d961a3d`**（目前最新的代码）。`main` 分支上根本没有 Workforce、Mission、Squad 和 `/terminal/` 终端。

---

## 1. Executive Summary

**结论：B，需要局部重构。** 但必须说清楚"局部"的范围：调度、持久化、审计、实时控制台这一层可以直接保留；**"Agent 用模型推理并调用工具"这一层目前不存在，需要新建**；**实时链上数据层（WebSocket 订阅、事件索引、重组处理）也不存在，需要新建**。

关键事实：

1. **截图里的功能都不在 `main`。** `main@0879002` 只有旧的 Telegram AI 团队和 `web/console.html`。Workforce 引擎、Mission、Squad、React 终端全部在未合并的 PR #10–#13 里（Python 新增 5,807 行，前端约 5,300 行）。
2. **ChainStaff 的 "Agent" 不是 LLM Agent。** 每个子任务执行的是固定的 Python 函数（`agent/workforce/skills.py` 里 7 个只读技能），没有"模型 ↔ 工具"循环，没有 Tool Calling，没有流式输出。模型只在两处被调用：主管拆任务（`planner.py:115-170`）和数据报告的摘要（`engine.py:1162-1167`）。没有 `ANTHROPIC_API_KEY` 时整个系统是纯规则驱动，规则规划器只能处理带 `0x` 地址的目标（`planner.py:88-91`）。
3. **调度层质量不错。** 持久化消息总线 + 任务 DAG + CAS 状态迁移 + 预算/步数/截止时间 + 人工审批 + 结果哈希上链（测试网），370 个测试全部通过，覆盖率 99%。这是 KAGOU 最值得复用的部分。
4. **"工作区里没有在岗的员工"已复现并定位。** 抛出点 `agent/workforce/planner.py:75`；根因在 PR #11 的 `_check_members` 把主管从成员里剔除、`_plan` 只看工作区成员、下达时不做检查。PR #12 已用 Squad + Preflight 修复（本次对照测试确认），**但 PR #12 没有合并，`main` 上这个功能本身都不存在**。
5. **没有实时链上能力。** 全仓库没有 WebSocket RPC、`eth_subscribe`、`newHeads`。RPC 是单个 HTTP URL、20 秒超时、无重试、无故障切换（`agent/execution/rpc.py:17-30`）。链上监控是每 300 秒拉一次 Etherscan（`bot/main.py:183-197`）。
6. **Telegram 没有接入 Workforce。** 机器人（`bot/main.py`）只连旧的 FrontDesk 规则员工，不能下达 Mission、不能收 Mission 结果。

---

## 2. Repository Version

### 2.1 版本状态

| 项目 | 结果 |
|---|---|
| 云端检出分支 | `claude/project-thread-mbhzkl`（与 `main` 相同，`0879002`） |
| `main` 最新提交 | `0879002 Merge pull request #8`（Console：KAGOU 配色、账本页） |
| 云端工作区未提交修改 | 无（`git status` 干净） |
| 用户本机未提交修改 | **未检查**。本机已连接的文件夹只有 `健身记录表` 和 `前端代码`，都未授权，也不确定是否是 chainstaff 仓库，所以没有读取。如需核对本机代码，请授权对应文件夹 |
| 远程分支 | `main`、`claude/project-thread-mbhzkl`，以及 PR 分支（见下） |

### 2.2 未合并的 Pull Request

| PR | 分支 → 基线 | Head | 内容 | 状态 |
|---|---|---|---|---|
| #6 | `claude/turnkey-extra` → main | `b1de911` | Turnkey 依赖、一键主网启动 | open |
| #9 | `claude/project-thread-lbaeas` → main | `c05b7e1` | 控制台聊天重设计、浏览器内 Telegram 登录、公开 Demo | open |
| **#10** | `…-idzqxd` → main | `372c044` | Workforce 引擎（Agent、任务图、消息总线、AI 主管、审批、链上技能）+ React 终端 | open |
| **#11** | `…-pw48i0` → #10 | `0993f2d` | 以 Agent 执行为中心重建终端，新增 Mission 层（`missions.py`） | open |
| **#12** | `…-v6ullf` → #11 | `78a147b` | Squad、Mission Preflight、Mission Control Workspace（**修复"没有在岗员工"**） | open |
| **#13** | `…-93pscn` → #12 | `d961a3d` | 终端统一设计系统（纯前端样式） | open |

PR #1–#5、#7、#8 已合并进 `main`。

### 2.3 截图对应的代码

| 截图中的元素 | 文件 |
|---|---|
| Terminal 页 | `web/terminal/src/pages/Terminal.tsx`，指标条 `components/QuickActionBar.tsx`，员工表 `components/AgentTable.tsx`，右侧 `components/AgentInspector.tsx` |
| Workspace 页 | `web/terminal/src/pages/Workspace.tsx`，`components/Mission.tsx`、`ExecutionFeed.tsx`、`ExecutionInspector.tsx`、`TaskComposer.tsx` |
| 后端接口 | `backend/workforce_api.py`（`/api/wf/*`、SSE `/api/wf/stream`） |
| 状态来源 | `agent/workforce/engine.py:agent_views()`（1556-1600）、`agent/workforce/missions.py` |

**你截图里的 "Agents online 1/1 · Idle 1" 这个标签只存在于 PR #11 的代码里**（`PR#11@0993f2d:web/terminal/src/components/QuickActionBar.tsx:27`）。PR #12 起这个格子改名为 "Active agents"（`QuickActionBar.tsx:47`）。所以出错时浏览器跑的是 PR #11 分支（或基于它的部署），不是 `main`，也不是 PR #12/#13。

另外，项目文件夹里的设计截图（`chainstaff-design-system/before-*.png` 等）显示区块高度 `#1,003`，这是测试桩 `FakeChain`（`tests/test_workforce.py:57`，`head = 1000`，`mine()` 后 +3）生成的数据，**不是真实 BSC 测试网**。

### 2.4 是否包含最新 Mission 系统

`main`：不包含。PR #11 起包含 Mission 视图（`agent/workforce/missions.py`，只读视图，不新增表）；PR #12 起包含 Squad、Preflight、并发上限、改派。

### 2.5 README 与实际代码的差异

| README 说法 | 实际 |
|---|---|
| `main` README：310 个用例 | `main` 实测 310 passed ✔ |
| PR #13 README：355 个用例 | PR #13 实测 **370** passed（README 未随 PR #12 更新） |
| "AI 员工平台引擎：自定义员工（角色、模型、技能……）" | 员工可配 `provider/model/system_prompt`，但**员工执行任务时不调用模型**；`system_prompt` 只在主管规划和报告摘要时进 prompt（`planner.py:122`、`:173`） |
| "员工 budget_usd 预算" | 字段存了（`engine.py:141-142`），**调度时从不检查**；只有 Mission 级预算生效（`engine.py:1025-1027`） |
| "实时事件流" | 是 SSE，但服务端是每 0.5 秒轮询 SQLite 再推送（`workforce_api.py:519-535`），不是事件驱动 |
| "7 个读真实链上数据的技能" | 代码确实发真实 JSON-RPC，但**本次环境无法连接 BSC**，未做真实验证 |
| `docs/workforce.md` "还没有做的"一节 | 与代码一致：无自有合约、无 ERC-8004 链上核验、无沙箱、无多用户、无向量记忆、无第三方技能 |

---

## 3. Current Architecture

仓库里其实有**两套互不相通的 "AI 员工" 系统**：

```
系统一（main 已有）：Telegram AI 团队 —— 规则驱动
  bot/main.py ──aiogram polling──▶ agent/front_desk.py:FrontDesk.on_message()
       ├─ finance_ops（付款：规则引擎 agent/rules/ 决定，executors 执行）
       ├─ community_support（FAQ 原文、骗局正则）
       ├─ chain_monitor（Etherscan 轮询，每 300 秒）
       ├─ translator（唯一调用 Claude 的地方）
       ├─ campaign_payouts / airdrop_sybil（规则）
  backend/app.py + web/console.html（老板控制台）

系统二（仅在 PR #10–#13）：Workforce —— DAG 调度 + 固定技能
  web/terminal (React) ──REST + SSE──▶ backend/workforce_api.py
       └─▶ agent/workforce/engine.py:Workforce（单进程，1 个调度线程 + 4 线程池）
             ├─ planner.py（模型规划 / 规则规划）
             ├─ skills.py（7 个 Python 函数）──▶ chain.py ──▶ agent/execution/rpc.py（HTTP JSON-RPC）
             ├─ db.py（SQLite，与账本同一文件、同一把锁）
             └─ anchor.py（审批后把结果哈希写 BSC 测试网）
```

两套系统共用 `agent/store.py` 的 SQLite 连接（`store.py:196-219`，单连接 + RLock 串行化，WAL）。Telegram 机器人不知道 Workforce 的存在。

---

## 4. Agent Runtime Audit

### 4.1 生命周期

| 能力 | 状态 | 证据 |
|---|---|---|
| 创建 | 已实现 | `engine.py:156 create_agent` → `db.add_agent`，`POST /api/wf/agents`（`workforce_api.py:118`） |
| 初始化 / 启动 | **不适用** | Agent 只是数据库一行配置，没有进程、没有会话、没有"启动"动作。引擎在 `backend/app.py:691-692` 启动一次（`wf.start()`），所有 Agent 共享 |
| 暂停 / 恢复 | 已实现（数据库字段） | `engine.py:179 set_agent_status`，`active/paused/archived`；暂停后 `_deliver` 跳过该 Agent 收件箱（`engine.py:880-881`） |
| 停止 | 部分实现 | 归档 = 停止；**正在线程里跑的技能无法中断**（见 §11 BUG-2） |
| 删除 | 已实现 | `engine.py:190 delete_agent`，有历史任务时拒绝删除，要求归档 |
| 错误恢复 | 部分实现 | 子任务级：瞬时错误重试、改派同 Squad 其他 Agent（`engine.py:1214-1235`）；进程级：`recover()`（`engine.py:835-848`），**但不处理卡在 `running` 的 Mission**（见 BUG-3） |
| 状态持久化 | 已实现 | 所有状态都是 SQLite 行，CAS 更新（`db.py:424-437 transition`） |

**状态从哪来：**

| 截图上的状态 | 计算方式 | 是不是 Runtime 真实状态 |
|---|---|---|
| Online / on duty | `a.status === "active"`（`QuickActionBar.tsx:35`） | 数据库静态字段，**不代表可调度**（不看是否在 Squad、有没有可用技能） |
| Busy / Running | `live === "busy"`：有 `assigned/running` 子任务，或在管的 Mission 处于 running/waiting（`engine.py:1584-1588`） | 来自任务表，真实 |
| Idle | 以上都不满足（`engine.py:1593-1594`） | 推导值 |
| Failed / error | 24 小时内有失败子任务（`engine.py:1591-1592`） | 推导值，不是"Agent 坏了" |
| Offline | **不存在**。没有心跳、没有进程，Agent 不会"掉线" | — |

### 4.2 模型调用

| 检查项 | 结果 | 证据 |
|---|---|---|
| 是否真实接入 LLM | 是，Anthropic Messages API（beta） | `planner.py:179-200 _call`，`self.llm().beta.messages.create(**kwargs)` |
| 提供商 | 只有 `none` 和 `anthropic` | `engine.py:42 PROVIDERS` |
| API Key 管理 | 环境变量 `ANTHROPIC_API_KEY`，由 SDK 读取，不进数据库、不返回前端 | `agent/workforce/__init__.py:50-55` |
| 流式输出 | **否** | 只有 `messages.create`，无 `stream` |
| Tool Calling | **否** | 请求里没有 `tools`；规划用结构化输出（`output_config.format = json_schema`，`planner.py:188`） |
| 超时 | **未显式设置**，用 SDK 默认值 | `planner.py:190` 每次 `self.llm()` 新建客户端，不传 `timeout` |
| 重试 | 只有 SDK 默认重试；业务层无 | 同上 |
| Token 用量 | 已记录到任务和 Agent | `engine.py:1304-1312 _charge`，`db.add_agent_usage` |
| 成本 | 只有配置 `CHAINSTAFF_MODEL_PRICE` 才计算，否则记 0 | `planner.py:197-199` |
| 模型切换 | 每个 Agent 可填 `model`；服务端回退走 `server-side-fallback` beta | `planner.py:182-187` |

**判断：** 现在的 Agent 是**由固定规则驱动的自动化程序**，模型只扮演"主管拆任务"和"写摘要"两个角色。一个 Agent 被派到子任务后，执行的是 `skill.run(ctx, input)`（`engine.py:1161`），不经过模型。KAGOU 需要的"Agent 自己决定查什么、调用哪个工具、看结果再决定下一步"，代码里没有。

另外，模型规划是在调度线程里**同步**调用的：`_deliver` → `_plan`（`engine.py:892`）→ `planner.plan` → `_call`。一次模型调用期间，整个引擎的消息投递、超时检查、重新派发都停住（见 BUG-4）。

### 4.3 工具系统

| 检查项 | 结果 | 证据 |
|---|---|---|
| Tool Registry | 静态元组 `SKILLS`，模块加载时固定 | `skills.py:302-325` |
| Tool Schema | 有输入/输出 JSON Schema；自写校验器只支持 type/required/pattern/enum/min/max/items | `skills.py:76-101` |
| Tool Permissions | 有：技能声明所需权限，Agent 必须全部持有 | `skills.py:26-30`，`engine.py:217-232 can_run` |
| Tool Execution | 线程池执行，执行前再查一次权限 | `engine.py:1135-1189` |
| Tool Results | 存在任务 `result`，计算 SHA-256，交接给依赖任务 | `engine.py:1181-1188` |
| Tool Errors | `SkillError` 视为输入错误不重试不改派；其他异常视为瞬时错误重试 | `engine.py:1168-1177` |
| Tool Timeout | **没有技能级超时**。只有 HTTP 层 20 秒（`rpc.py:22`），技能内部多次调用会累加 | — |
| Tool Logging | `tool.start`/`tool.done`/`tool.error` 事件，带日志、耗时、输出预览 | `engine.py:1155-1156`、`1191-1197` |

能否动态添加新的链上工具？**不能。** 加一个技能要改 `skills.py` 源码并重启；"技能市场"的安装/卸载只是在内置列表上切换开关（`engine.py:1482-1504`），README 也写了"第三方技能安装"未做。

能否让不同 Agent 用不同 Skills？**能。** 每个 Agent 有自己的 `skills` 和 `permissions`。

能否不改核心 Runtime 扩展工具？**不能**，但改动面小：`Skill` 是一个带 `run` 回调的 dataclass（`skills.py:55-73`），把 `SKILLS` 换成可注册的表就能扩展。

### 4.4 多 Agent 协作

| 检查项 | 结果 | 证据 |
|---|---|---|
| 互相传递任务 | 是，经持久化消息总线：`task.assign`、`task.result`、`handoff` | `engine.py:1031-1033`、`1199-1212` |
| 统一任务上下文 | 部分：同一 Mission 的子任务共享 `root_id`，上游结果通过 `handoff` 传给下游（`engine.py:1151-1152`） | — |
| 共享工作区 | 是（Workspace + Squad），另有分范围记忆（FTS5 trigram，`db.py:159`） | — |
| 并行 | 是，`ThreadPoolExecutor(max_workers=4)`（`engine.py:94`），每 Agent `max_concurrency`，每 Squad `max_parallel` | `engine.py:1012-1021` |
| 依赖 | 是，`depends_on` DAG；模型计划只允许依赖排在前面的步骤，天然无环 | `planner.py:161-163`、`engine.py:993-1001` |
| 结果汇总 | 是，`data_report` 技能 + `_maybe_finalize` | `engine.py:1094-1123` |
| 重复执行 | 状态 CAS 防住了重复执行；但成功后 `task.assign` 消息没标 processed，会再投递一次空跑（BUG-5） | 测试 P2 |
| 无限循环 | 风险低：`max_steps`（默认 20）、截止时间、预算三道闸；动态追加只发生在规则规划的 `wallet_analysis` 后（`engine.py:1059-1060`） | — |

**真实调用链（PR #13）：**

```
User（浏览器 Workspace 输入框）
 → POST /api/wf/squads/{sid}/missions        backend/workforce_api.py:263 squad_mission → _submit (205)
 → Workforce.submit_goal                      agent/workforce/engine.py:615
     → preflight                              engine.py:505-613（不通过则 400，不建任务）
     → db.add_task(kind=goal, status=pending) engine.py:638
     → db.post(event=goal.submitted, recipient=主管)  engine.py:647   ← 写入 wf_messages
 ── 调度线程 Workforce.start → tick（每 1 秒） engine.py:815-861
 → _deliver → _plan(manager, msg)             engine.py:877-901 / 946
     → _roster(goal)                          engine.py:477（Squad 成员 + 可执行的主管）
     → Planner.plan → _model_plan | _rule_plan    planner.py:73-113
         → [Model] Anthropic messages.create  planner.py:190（仅主管 provider=anthropic 且有 key）
     → db.add_task(kind=subtask) × N          engine.py:967-972
 → _dispatch                                  engine.py:983-1038 → post(task.assign)
 → _deliver → pool.submit(_execute_safe)      engine.py:889-890
 → _execute                                   engine.py:1135
     → can_run 权限检查                       engine.py:1142
     → skill.run(ctx, input)                  engine.py:1161   ← [Tool]，不经过模型
         → ChainReader → RpcClient.call       chain.py:92 → rpc.py:26（httpx POST）
     → transition(running→completed)          engine.py:1182   ← [Database]
     → _handoff / _report_to_manager          engine.py:1188-1189
 → _collect → _dispatch → _maybe_finalize     engine.py:1049 / 1094（goal → review，生成审批）
 → [Browser] GET /api/wf/stream（SSE）        workforce_api.py:511-537（每 0.5 秒查 wf_messages 推送）
     → web/terminal/src/lib/stream.ts:run → 400ms 防抖后 invalidate React Query → 重新拉 REST
 → 人工审批 POST /api/wf/approvals/{id}      → engine.py:1318 decide → 可选 anchor_task（1355）
```

---

## 5. Mission Scheduler Audit

### 5.1 Mission 生命周期

Mission 就是 `kind='goal'` 的任务行，阶段由状态推出（`missions.py:21-22`）。

| 阶段 | 后端逻辑 | 证据 |
|---|---|---|
| Created | 是，`pending` | `engine.py:638-640` |
| Planning | 是，`pending→running` CAS 后规划 | `engine.py:949` |
| Task Assignment | 是，子任务 `pending→assigned` | `engine.py:1028` |
| Running | 是，goal `waiting`，子任务 `running` | `engine.py:980`、`1148` |
| Review | 是，全部子任务结束 → `review` + 审批 | `engine.py:1113-1123` |
| Completed / Failed | 是；Completed 只在人工审批后 | `engine.py:1338-1346` |

| 能力 | 状态 | 证据 |
|---|---|---|
| 任务拆分 | 模型规划（需 key）/ 规则规划（只认地址） | `planner.py` |
| Agent 选择 | `pick`：能跑该技能者中按（非主管、在途任务少、创建早）排序 | `planner.py:64-69` |
| 失败处理 | 重试 → 改派 → 失败并通知依赖方 | `engine.py:1214-1249` |
| 超时 | Mission 截止时间，超时整体失败 | `engine.py:927-936` |
| 取消 | 是，取消子任务并作废消息；**正在运行的线程不会停** | `engine.py:732-739`、`766-775` |
| 重启恢复 | 部分：消息和运行中的子任务会重排；规划中的 Mission 不会 | `engine.py:835-848`，BUG-3 |

### 5.2 调度器能力

| # | 能力 | 状态 | 说明 |
|---|---|---|---|
| 1 | 任务队列 | 部分 | 队列就是 SQLite 表 + 每秒一次的 tick 扫描，没有独立消息队列 |
| 2 | 并行 | 已实现 | 线程池 4 + Agent/Squad 并发上限 |
| 3 | 依赖 | 已实现 | DAG，`data_report` 容忍上游失败 |
| 4 | 优先级 | 部分 | 同一 Mission 内按 priority 排序（`engine.py:990`）；`open_goals` 按 priority 排序（`db.py:553`），但线程池本身 FIFO |
| 5 | 重试 | 已实现 | `max_attempts=2`，手动重试接口 |
| 6 | 幂等 | 部分 | 消息有 `dedupe_key`，状态 CAS；**Mission 提交接口没有幂等键**（BUG-6） |
| 7 | 超时 | 部分 | Mission 级有；技能级、模型调用级没有 |
| 8 | 取消 | 部分 | 逻辑取消有；不能中断执行中的线程 |
| 9 | Agent 故障转移 | 已实现（PR #12） | `_ensure_available` / `_pick_other` / `_reassign`（`engine.py:1256-1294`） |
| 10 | 重启后恢复 | 部分 | 见 BUG-3 |

### 5.3 "工作区里没有在岗的员工" 排查

**抛出位置：** `agent/workforce/planner.py:74-75`（PR #10 起至今未变）

```python
def plan(self, goal, manager, members, wf):
    if not members:
        raise PlanError("工作区里没有在岗的员工")
```

**PR #11 的完整调用链：**

1. 建工作区：`PR#11@0993f2d:agent/workforce/engine.py:212 create_workspace` → `_check_members`（236-244）最后一行 `return manager_id or None, [m for m in members if m != manager_id]`，**主管被从成员列表剔除**。
2. 建员工：`create_agent`（136）**不会把新员工加进任何工作区**。
3. 下达任务：`submit_goal`（256-283）只检查"有主管、主管 active"，**不检查有没有执行者**，直接建 Mission。
4. 规划：`_plan`（550-562）`members = [a for a in (self.db.agent(x) for x in ws["members"]) if a and a["status"] == ACTIVE]`，**只看工作区成员，主管永远不算执行者** → 空列表 → `planner.py:75` 抛错 → `_end_goal(FAILED)`。

**为什么 Terminal 显示 Online 1/1、Idle 1：** "Online" 只看 `status === "active"`（`PR#11:QuickActionBar.tsx:17`），"Idle" 是"手上没任务"（`engine.py agent_views`）。两者都不检查该 Agent 是否属于这个工作区、能否被调度。前端显示的 "Online" 和后端的"可调度"是两个不同的概念，这是**字段语义不一致**，不是缓存不同步。

**逐项核对你列的检查点：**

| 检查点 | 结论 |
|---|---|
| Agent 是否属于当前 Workspace | **否**（这是根因之一）：唯一的 Agent 是主管，被剔出成员；或员工建在工作区之后，从未加入 |
| 是否属于当前 Squad | PR #11 没有 Squad |
| 是否被正确注册 | 是，`wf_agents` 有这一行 |
| 是否满足 Skill | 与本错误无关：成员列表为空时根本走不到技能匹配 |
| 是否可调度 | PR #11 没有"可调度"概念，只有 `status` |
| Online/Idle 如何计算 | 见上，与调度无关 |
| Planner 如何找可用 Agent | 只读 `ws["members"]`，过滤 `active` |
| 数据库关联是否正确 | 关联本身正确，但"主管不算成员"是设计如此 |
| 状态缓存不同步 | **未发现**。前端每次都从 `/api/wf/agents` 取数据，SSE 触发刷新 |
| 前后端字段不一致 | **是**：前端 "Online" ≠ 后端"能执行任务" |

**最小复现（本次实际运行，代码见附录 A）：**

| 场景 | PR #11 `0993f2d` | PR #13 `d961a3d` |
|---|---|---|
| P1a 只有 1 个 Agent（它是主管，有 `wallet_analysis` 技能），工作区设它为主管 | Mission 创建 → **failed：工作区里没有在岗的员工**；`agents_online=1, total=1, idle=1`（与截图一致） | Mission 正常完成到 `review`（Squad 默认 `manager_executes`，主管自己执行） |
| P1b 只有 1 个主管，无技能 | 创建 → failed，同上 | **拒绝创建**：`Squad Core squad 里除了主管还没有成员，主管自己也没有可用的技能：先把有技能的员工加进来`；`missions_created=0` |
| P1c 先建工作区（只设主管），后建分析员，未加入 | 创建 → failed，同上；`online=2, idle=2` | **拒绝创建**，同上原因 |

**根本原因：** 工作区成员与"可执行者"是两件事，但 PR #11 只用成员列表找执行者，且（a）主管被设计成不在成员里、（b）新员工不会自动加入、（c）下达时不预检，所以错误推迟到规划阶段才暴露。

**受影响文件（PR #11）：** `agent/workforce/engine.py`（`_check_members`、`submit_goal`、`_plan`、`_expand`）、`agent/workforce/planner.py:75`、`web/terminal/src/components/QuickActionBar.tsx`。

**PR #12 的修复（commit `c83484d`，已验证有效）：**
- 引入 Squad，执行者 = 成员 + 主管（若 `manager_executes`）：`engine.py:467-480`
- 下达前预检，不通过就不建 Mission 并给出修复动作：`engine.py:505-613`，`submit_goal` 在 631-634 调用
- 旧数据启动时迁移为 "Core squad"：`engine.py:299-308`
- 回归测试：`tests/test_squads.py:37 test_workspace_made_before_its_agents_gets_a_working_squad`、`tests/test_squads.py:60 test_a_manager_with_skills_can_work_alone`、`tests/test_workforce.py:433`

**推荐修复方式：** 合并 PR #10–#12（或至少 #12 的引擎改动）。修复本身不需要再改代码。

**残留问题（PR #13 仍存在）：**
- 预检与规划之间有时间窗：预检通过后、规划前把执行者暂停，Mission 仍会在规划阶段失败（本次测试：暂停唯一执行者后 Mission `failed`，错误"团队里没有能做链上分析的员工"）。严重程度：低。
- Terminal 的 "on duty" 仍然只看 `status`，不看是否在任何 Squad 里（`QuickActionBar.tsx:35`）。建议把 Squad 的 `ready/executors`（`engine.py:497-498` 已经算好）显示在 Terminal。

**回归测试方案：** 保留上述 3 个测试；补一个"预检后、规划前暂停执行者"的测试，断言 Mission 失败原因可读且 Agent 状态正确；补一个前端单测断言 "on duty" 计数只算至少在一个 active Squad 里的 Agent（如果采纳上面的建议）。

---

## 6. BSC Infrastructure Audit

### 6.1 RPC 能力

| 检查项 | 结果 | 证据 |
|---|---|---|
| 传输 | **只有 HTTP**（`httpx.post`），无 WebSocket | `agent/execution/rpc.py:19-22` |
| Testnet | 支持，示例配置 chain id 97 | `config/chain.example.json` |
| Mainnet | 读：支持（`config/chain.mainnet.example.json`，56）；Workforce 写：拒绝（`anchor.py:40-41` 只允许 97） | — |
| Chain ID 校验 | 只在旧财务员工 `CHAINSTAFF_LIVE=1` 时校验（`runtime.py:59-61`）；**Workforce 启动不校验 RPC 实际链 ID**（`workforce/__init__.py:29-31`） | — |
| 故障切换 | **无**，单个 `rpc_url` | `config.py:95` |
| 请求超时 | 20 秒 | `rpc.py:22` |
| 限流 | **无**（只有控制台写接口限流，`workforce_api.py:37-53`） | — |
| 重试 | **无**；错误直接 `RuntimeError` | `rpc.py:26-30` |
| 缓存 | **无** | — |
| JSON-RPC id | 固定为 1，不支持批量 | `rpc.py:27` |

### 6.2 实时链上监听

| 能力 | 状态 | 实际实现 |
|---|---|---|
| New Block Subscription | **尚未实现** | 只有按需 `eth_blockNumber` / `eth_getBlockByNumber` |
| Contract Event Subscription | **尚未实现** | `contract_events` 技能是一次性 `eth_getLogs`（最近 N 块，`skills.py:216-231`） |
| Address Activity Monitoring | 部分（旧系统） | `ChainMonitor.run_once` 每 300 秒调 Etherscan API（`bot/main.py:183-197`，`monitor.py:205-213`） |
| Transaction Receipt Tracking | 部分 | 只跟踪自己发出的锚定交易，每秒 tick 轮询回执（`engine.py:1377-1389`，`anchor.py:58-75`） |
| Token Transfer Monitoring | 部分（旧系统） | 同上，Etherscan 轮询 |

全仓库搜索 `websocket|wss://|eth_subscribe|newHeads`：**0 处**。当前全部是定时轮询，不是实时订阅。

### 6.3 数据正确性

| 检查项 | 结果 |
|---|---|
| 重复事件去重 | 旧监控：按 alert key 只报一次（`bot/main.py:166-170`）；Workforce 没有事件流，不涉及 |
| 区块重组 | **未处理**。锚定交易拿到第一个回执就记 `confirmed`（`anchor.py:69-74`），Mission 显示 `onchain_confirmed`（`missions.py:35-36`），没有确认数要求 |
| 断线重连 | 不适用（没有长连接） |
| 事件补偿 | 旧监控用"上次检查时间"补拉，最多回看 24 小时（`monitor.py:41`、`212-213`），按时间而不是按区块号 |
| 区块高度同步 | 无本地高度游标 |
| 交易状态更新 | 锚定交易：pending → confirmed/failed，15 分钟查不到判失败（`anchor.py:62-67`） |
| 持久化 | 锚定记录在 `wf_chain_records` |

### 6.4 安全审计

| 检查项 | 结论 | 证据 / 严重程度 |
|---|---|---|
| 私钥暴露给前端 | **否**。锚定钱包用 bnbagent keystore（密码在 `CHAINSTAFF_WALLET_PASSWORD`），接口只返回地址和余额 | `runtime.py:37-41`，`workforce_api.py:456-462` |
| API Key 进日志 | 未发现直接记录。**风险点**：Etherscan key 作为 URL 查询参数（`workforce/__init__.py:39`），若 httpx 异常文本带 URL，会被写进 `tool.error` 时间线并推到浏览器。未复现。中 |
| Agent 权限过大 | **否**。Workforce 技能全部只读；唯一写操作是锚定，且只在审批后、只在 97 | `skills.py:1-5`，`engine.py:1318-1375` |
| 未授权链上写 | 未发现。旧财务付款：规则引擎决定 + `CHAINSTAFF_LIVE=1` + 主网需 `CHAINSTAFF_ALLOW_MAINNET=yes` 且必须 Turnkey | `runtime.py:59-68` |
| 独立权限验证 | **否**。整个控制台一个 Bearer 令牌（`backend/app.py:68-71`），拿到令牌的人既能下任务也能审批、能上链。中（KAGOU 多用户场景下为高） |
| 重复执行 | Mission 可被重复提交（BUG-6）；锚定有防重（`engine.py:1362-1364`） |
| 人工审批 | 有：Mission 结果、超预算都要审批；锚定必须在审批时选择 | `engine.py:1040-1047`、`1116-1123` |
| 令牌存储 | 浏览器 `sessionStorage`（`web/terminal/src/lib/api.ts:8-17`），只在当前标签页 | 低 |
| Prompt 注入 | 模型计划里出现目标文本中没有的地址会被拒绝（`planner.py:164-167`）；但附件文本会原样拼进 prompt | 低（目前没有写工具） |
| 依赖漏洞 | `pip-audit`：No known vulnerabilities；`npm audit`：0 vulnerabilities（本次实测） | — |

---

## 7. Browser Realtime Audit

**数据流：**

```
引擎/接口写 wf_messages（SQLite）
   ↓  服务端每个 SSE 连接各自每 0.5 秒 SELECT id > cursor LIMIT 500     workforce_api.py:519-535
   ↓  SSE（fetch 读 body，Bearer 在 header；断线按 after=lastId 续传，退避 1s→15s）   stream.ts:44-81
浏览器 listeners（时间线即时追加）
   ↓  按事件类型标记 query key，400ms 防抖后 invalidateQueries                stream.ts:27-41
React Query 重新拉 REST：/agents /missions /tasks /squads …（另有 overview 30s、chain 15s 定时刷新）
```

| 检查项 | 结论 |
|---|---|
| WebSocket | 无 |
| SSE | 有，真实推送，但服务端是轮询数据库，不是事件驱动 |
| HTTP Polling | 有，`overview` 30 秒、`chain` 15 秒（`queries.ts:18`、`:47`） |
| 状态管理 | React Query 缓存；偏好存 localStorage |
| 三个面板是否同一份后端状态 | **是**。Terminal、Workspace、Execution Inspector 都读 `/api/wf/*`，状态来自同一张任务表；没有前端本地模拟的状态 |
| 前端 Online、后端不可调度 | **存在**（见 §5.3），PR #13 仍存在 |
| Mission 已失败、Agent 仍 Running | 短时可能：取消/超时后线程继续跑，此期间子任务已 `cancelled`，Agent 的 `live` 会正确变回 idle；**但时间线会在失败之后出现 `task.completed`、`task.result`**（BUG-2） |
| Logs 更新但 Steps 不更新 | **会发生**，同 BUG-2：Logs 显示"完成"，Steps 显示"已取消" |
| 刷新后状态丢失 | 否，状态全在服务端；令牌在 sessionStorage，关标签页需重新登录 |
| 重复推送 | 服务端按递增 id 游标，断线续传不重复 |
| 服务端隐患 | SSE 生成器在 async 函数里直接调用同步 SQLite（`workforce_api.py:520`），与引擎共用一把锁；连接数多时会阻塞事件循环 |

---

## 8. Security Findings（汇总）

| ID | 严重程度 | 问题 | 位置 |
|---|---|---|---|
| S-1 | 中（多用户时高） | 单一共享令牌，操作者即审批者，没有角色权限、没有用户隔离 | `backend/app.py:68-71` |
| S-2 | 中 | Etherscan API key 放在 URL 查询参数，异常文本可能把它写进时间线 | `agent/workforce/__init__.py:39` |
| S-3 | 低 | 锚定交易单回执即"已确认"，重组后状态不回滚 | `anchor.py:69-74`，`missions.py:35` |
| S-4 | 低 | Workforce 启动不校验 RPC 链 ID（锚定只校验配置里的 chain_id） | `workforce/__init__.py:29-31`，`anchor.py:40` |
| S-5 | 正面 | 技能只读、写链需审批且限测试网、主网付款多重开关、密钥不入库 | 见 §6.4 |

---

## 9. Test Results

环境：云端容器，Python 3.13.17，Node 22.22.0，4 核。**未连接任何钱包、未调用付费 API、未使用生产数据库**（全部内存或临时 SQLite）。

| 代码版本 | 命令 | 结果 |
|---|---|---|
| `main@0879002` | `pytest -q` | **310 passed** |
| `PR#11@0993f2d` | `pytest -q` | **355 passed** |
| `PR#12@78a147b` | `pytest -q` | **370 passed** |
| `PR#13@d961a3d` | `pytest -q --cov` | **370 passed**，覆盖率 99%（`engine.py` 97%，`bot/main.py` 72%），25.5 秒 |
| PR #13 前端 | `npm ci && tsc --noEmit && npm run build` | 类型检查通过，构建通过 |
| PR #13 | `pip-audit --skip-editable` / `npm audit` | 0 已知漏洞 |

现有测试用的是 `tests/test_workforce.py` 里的 `FakeChain`（模拟 BSC 节点）和 `FakeLLM`，**不是真实链和真实模型**。

**按你列的 10 项验证：**

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | Agent 创建 | 通过 | `test_workforce_api.py:test_full_flow_over_http` |
| 2 | Agent 启动 | 不适用 | Agent 没有"启动"概念 |
| 3 | Mission 创建 | 通过 | 同上，及附录 A |
| 4 | Agent 分配 | 通过 | `test_squads.py`，附录 A |
| 5 | Mission 执行 | 通过（FakeChain） | 本次 P6c：60 次全部到达 `review` |
| 6 | 多 Agent 协作 | 通过（FakeChain） | `test_squads.py:37`，3 个 Agent 分到子任务 |
| 7 | Mission 失败 | 通过；发现 BUG-2 | 附录 A P4 |
| 8 | Mission 恢复 | 部分；发现 BUG-3 | 附录 A P3 |
| 9 | BSC RPC 查询 | **无法测试** | 容器出口代理拒绝：`data-seed-prebsc-1-s1.bnbchain.org:8545` → connection reset；`bsc-testnet-rpc.publicnode.com`、`bsc-dataseed.bnbchain.org` → CONNECT 403 |
| 10 | 浏览器状态同步 | 部分 | SSE `once` 模式接口测试通过；未在真实浏览器里测推送延迟 |

---

## 10. Performance Findings

**以下全部是"内部处理耗时"**：同进程、内存 SQLite、`FakeChain` 网络耗时为 0、没有模型调用。不能当作生产性能，只能说明**框架本身的开销**。

| 指标 | 测试条件 | P50 | P95 | P99 |
|---|---|---|---|---|
| Mission 入队（`submit_goal`，含预检和写库） | 60 次 | 0.82 ms | 1.17 ms | 1.34 ms |
| 规划 + 首批派发（一次 tick，规则规划） | 60 次 | 5.4 ms | 7.3 ms | 7.8 ms |
| Mission 端到端（同步驱动，15 次 RPC/Mission，0 网络延迟） | 60 次 | 45.9 ms | 55.6 ms | 57.3 ms |
| **Agent 调度延迟**（真实后台循环，`interval_s=1.0`，提交到首个子任务被派发） | 10 次 | **704 ms** | 1003 ms | 1003 ms |
| `GET /api/wf/agents` | 22 Agent、200 Mission、8,349 条时间线，30 次 | 18.0 ms | 24.4 ms | 33.5 ms |
| `GET /api/wf/overview` | 同上 | 23.9 ms | 36.3 ms | 62.4 ms |
| `GET /api/wf/missions?limit=100` | 同上（响应 315 KB） | 23.8 ms | 34.4 ms | 78.3 ms |
| `GET /api/wf/tasks?limit=50` | 同上 | 10.3 ms | 17.8 ms | 19.0 ms |
| `GET /api/wf/squads` | 同上 | 10.0 ms | 12.7 ms | 12.9 ms |
| `GET …/timeline` | 同上 | 7.4 ms | 8.4 ms | 9.5 ms |
| `POST /api/wf/missions/preflight` | 同上 | 8.7 ms | 10.5 ms | 10.6 ms |
| `GET /api/wf/stream?once=1`（一次推 500 条） | 同上 | 136.7 ms | 165.6 ms | 176.2 ms |

解读：
- 调度延迟主要来自 1 秒 tick（`engine.py:815-826`），不是计算开销。KAGOU 要做"实时"，需要改成事件触发。
- 浏览器推送延迟（由代码推导，未实测）：服务端最多 0.5 秒轮询间隔 + 客户端 400 ms 防抖后才重新拉列表。
- `agent_views`、`squad_views` 每次请求都全表计算（`squad_views` 对每个 Squad 跑一遍 preflight，`engine.py:490`），数据量上去后会线性变慢。

**无法测量的项目（没有用模拟数据代替）：**

| 指标 | 原因 |
|---|---|
| BSC RPC 查询延迟 | 容器无法访问公共 BSC RPC（见 §9） |
| 链上事件处理延迟 | 系统没有链上事件监听 |
| 区块链确认耗时 | 无测试网钱包、无网络 |
| LLM 首 Token 延迟 | 没有 API key；且代码不用流式，没有"首 Token"这个时刻 |
| AI 推理耗时 | 同上 |
| 真实浏览器推送延迟 | 未启动浏览器做端到端计时 |

---

## 11. Critical Bugs

| ID | 严重程度 | 问题 | 位置 | 复现 / 证据 |
|---|---|---|---|---|
| BUG-1 | **高**（PR #12 已修，未合并） | 工作区里没有在岗的员工：Mission 被创建后在规划阶段失败 | `planner.py:75`；`PR#11:engine.py:236-244, 256-283, 550-562` | 附录 A P1a/b/c |
| BUG-2 | 中 | 超时或取消后，仍在运行的技能线程继续调用 RPC，结束后照常发 `tool.done`、`task.completed`、`task.result`、写记忆、发交接，因为 `transition(running→completed)` 的返回值被忽略 | `engine.py:1178-1189` | P4：goal 已 `failed`、子任务 `cancelled`，之后又发生 4 次 RPC 调用，时间线追加 `task.completed`、`task.result` |
| BUG-3 | 中 | 进程在"认领 Mission"（pending→running）与"写出子任务"之间退出，重启后 `recover()` 不处理 goal，Mission 一直停在 `running`（显示 planning），直到截止时间才失败 | `engine.py:835-848`、`946-950` | P3：重启后 `recovered_items=0`，`goal_status=running`，`subtasks=0` |
| BUG-4 | 中 | 模型规划在调度线程里同步执行，Anthropic 客户端未设超时；一次慢调用会停住全部 Mission 的投递、超时检查和重派 | `engine.py:892`、`957`；`planner.py:190` | 代码路径确认；无 key，未实测时长 |
| BUG-5 | 低 | 成功执行后不把 `task.assign` 消息标记为 processed，下一轮 tick 会再提交一次 `_execute`（靠 CAS 变成空操作） | `engine.py:1178-1189` | P2：1 条 assign 消息触发 2 次 `_execute`，技能实际运行 1 次 |
| BUG-6 | 中 | 提交 Mission 没有幂等键，双击或网络重试会创建两个 Mission、执行两遍、花两份钱 | `engine.py:615-650`，`workforce_api.py:205-213` | P5：同一请求两次 → 2 个 Mission、2 次技能运行 |
| BUG-7 | 中 | Agent 级 `budget_usd` 只存不用 | `engine.py:141-142`，调度路径无引用 | `grep` 确认 |
| BUG-8 | 低 | `rpc_calls_total` 报的是全局累计值，且计数器跨线程无锁 | `chain.py:90-94`，`engine.py:1193` | 代码确认 |
| BUG-9 | 低 | Squad 暂停或并行上限触发时用 `break`，同一 Mission 后面的子任务这一轮都不派（下一秒重试），属预期但会拖慢 | `engine.py:1007`、`1021` | 代码确认 |

---

## 12. Missing Capabilities（对照 KAGOU 定位）

| KAGOU 需求 | 状态 | 说明 |
|---|---|---|
| 用户自由创建 AI Agent | **已经实现**（PR 栈） | 名称、角色、提示词、模型、技能、权限、并发 |
| 配置 AI 模型 | **部分实现** | 只有 Anthropic；配置只影响主管规划和报告摘要 |
| 自定义 Skills 和 Tools | **尚未实现** | 7 个内置技能写死在源码里 |
| 多 Agent 协作 | **已经实现**（规则执行） | DAG、交接、并行、改派；但执行者不推理 |
| 自然语言任务规划 | **部分实现** | 需要 API key；无 key 时只认地址 |
| 实时 BNB Chain 数据查询 | **部分实现** | HTTP 按需查询，无缓存/重试/故障切换；未经真实网络验证 |
| 链上事件监控 | **尚未实现**（Workforce）/ 部分（旧系统 Etherscan 5 分钟轮询） | 无订阅、无索引、无重组处理 |
| 自动化 Mission | **已经实现** | cron 定时（`engine.py:1395-1450`），连续 3 次失败自动暂停 |
| 实时浏览器控制台 | **已经实现**（PR 栈） | SSE + React；服务端轮询数据库 |
| Telegram 集成 | **尚未实现**（与 Workforce） | 机器人只连旧 FrontDesk |
| 可审计的任务执行记录 | **已经实现** | 全量时间线、工具日志、结果 SHA-256、审计日志、测试网锚定与 `/chain/verify` |
| Agent 的模型 ↔ 工具循环 | **尚未实现** | KAGOU 的核心 |
| 多用户 / 钱包登录 / 隔离 | **尚未实现** | 单令牌单所有者 |
| 流式输出 | **尚未实现** | — |
| 水平扩展 | **尚未实现** | 单进程线程池 + 单 SQLite 连接 |

---

## 13. Recommended Architecture

保留调度与审计骨架，在两个明确的接缝处新建能力：

```
Telegram ─┐                 ┌─ Browser Console (保留 web/terminal)
          ▼                 ▼
     API 网关（多用户鉴权 + RBAC，替换单令牌）
          │
     Mission Scheduler（保留 engine.py 的 DAG/总线/审批/预算；
          │             tick 改为事件触发；规划移出调度线程；提交加幂等键）
          │
     ┌────┴───────────────────────────┐
     ▼                                ▼
 Agent Executor（新建）           Skill/Tool Registry（重构 skills.py）
  模型 ↔ 工具循环（Tool Calling、    每个工具 = schema + 权限 + 超时 + 版本，
  流式、超时、重试、步数/预算闸）     可注册、可按 Agent 授权，支持第三方
     │                                │
     └──────────────┬─────────────────┘
                    ▼
        Chain Data Layer（新建）
         多 RPC 端点 + 故障切换 + 重试 + 限流 + 缓存
         WebSocket newHeads / logs 订阅，按区块号游标持久化
         确认深度、重组回滚、断线补拉
                    │
        存储：SQLite → PostgreSQL；消息总线可先留表结构，后续换队列
```

接缝的具体位置：
- **Agent Executor**：`engine.py:1157-1167`（`SkillContext` + `skill.run`）。把"直接运行技能"替换成"Agent 循环，技能作为工具"，其余调度不动。`engine.py` 文件头已经按这个思路预留（LangGraph 节点映射）。
- **Tool Registry**：`skills.py:302-325` 的 `SKILLS` 元组改为注册表，`can_run`（`engine.py:217-232`）保持不变。
- **Chain Data Layer**：`chain.py:84-94 ChainReader._call` 是所有读操作的唯一入口，可以在下面替换传输层。

---

## 14. Integration Roadmap

| 优先级 | 步骤 | 是否重构 | 说明 |
|---|---|---|---|
| P0 | 合并 PR #10–#13（或至少 #10–#12），更新 README 用例数 | 否 | 否则 `main` 上没有任何 KAGOU 相关能力，BUG-1 也没修 |
| P0 | 修 BUG-2、BUG-3、BUG-6 | 局部 | 都是几行到几十行的改动，配回归测试 |
| P0 | 规划移出调度线程 + 模型调用显式超时（BUG-4） | 局部 | 否则接入模型 Agent 后调度会被卡住 |
| P1 | 新建 Agent Executor（Tool Calling 循环 + 流式 + Token/成本 + 超时/重试） | **新建** | KAGOU 的核心能力 |
| P1 | Skill 注册表 + 技能级超时 | 局部重构 | 支持动态添加链上工具 |
| P1 | Chain Data Layer：多端点、重试、限流、缓存、链 ID 校验 | 新建 | 在 `ChainReader` 之下 |
| P2 | WebSocket 订阅 + 区块游标 + 重组处理 + 事件触发 Mission | 新建 | "链上事件监控"与"自动化 Mission"的基础 |
| P2 | Telegram 接入 Workforce（下达 Mission、推送结果、审批） | 局部 | 复用 `bot/main.py` 与 `submit_goal`/`decide` |
| P2 | 多用户鉴权与 RBAC、审批与操作分权 | 局部重构 | 替换 `backend/app.py:68-71` |
| P3 | PostgreSQL、多进程/多实例调度（租约或队列） | 重构 | 规模化前再做 |

---

## 15. Final Assessment

| 模块 | 当前实现 | 已验证 | 缺失 | 主要风险 | 是否重构 | 优先级 | 状态 |
|---|---|---|---|---|---|---|---|
| Agent Runtime | Agent = 配置行；执行 = 固定 Python 技能 | 创建/暂停/归档/删除、权限检查（测试） | 模型推理执行、Tool Calling、流式、技能超时 | 这是 KAGOU 核心，目前不存在 | **新建执行层** | P1 | 部分实现 |
| Mission Scheduler | 持久化总线 + DAG + CAS + 预算/步数/截止 + 审批 + 改派 | 370 测试 + 本次 P1–P6 | 事件触发、幂等提交、规划异步化、规划中恢复 | BUG-2/3/4/6 | 局部修复 | P0 | 已经实现（有缺陷） |
| BSC Infrastructure | HTTP JSON-RPC 只读 + 测试网锚定 | 仅 FakeChain | WS 订阅、故障切换、重试、缓存、重组 | 真实网络行为未验证 | **新建数据层** | P1–P2 | 部分实现 / 无法确认（真实网络） |
| Browser Console | React + SSE + React Query | 构建、类型检查、接口测试 | 事件驱动推送；"on duty"语义 | 服务端轮询、同步 DB 阻塞事件循环 | 局部 | P2 | 已经实现（PR 栈） |
| Telegram Integration | 旧 FrontDesk 规则员工 | 旧系统测试 | 与 Mission 打通 | — | 局部 | P2 | 尚未实现（对 Workforce） |
| Database | 单 SQLite 文件、单连接 + 全局锁、WAL | 测试 | 并发写、多实例 | 规模化瓶颈 | 后期重构 | P3 | 已经实现（单机） |
| Security | 只读技能、审批后测试网写、主网多重开关、单令牌 | 代码审查 + 依赖扫描 | 多用户、RBAC、分权审批 | 多用户上线前必须解决 | 局部 | P2 | 部分实现 |
| Scalability | 单进程，1 调度线程 + 4 工作线程，1 秒 tick | P6 内部耗时 | 水平扩展、队列 | 1 秒调度粒度、线性全表视图 | 重构 | P3 | 尚未实现 |

### 结论：**B. 需要局部重构**

理由：
- **不选 A**：Agent 不会用模型推理和调用工具，没有实时链上订阅，Telegram 不通 Mission，而且这些功能连 `main` 都没进。
- **不选 C**：调度、持久化、审批、审计、实时控制台这几层设计合理、测试充分（370 个测试、99% 覆盖率），并且在 `engine.py:1157-1167` 留有清晰的接缝，新的 Agent 执行器可以插进去而不推翻调度器。需要"重建"的是一个目前根本不存在的层，不是推倒已有的 Runtime。
- **不选 D**：可审计执行记录、结果哈希上链、人工审批、权限模型都直接符合 KAGOU "可审计" 的定位。

需要提醒的是，"局部"并不等于"工作量小"：Agent 执行层和链上实时数据层是两块全新的工程，是 KAGOU 和 ChainStaff 之间最大的差距。

---

## 附录 A：复现脚本与原始输出

脚本在仓库外运行（没有写入仓库），复用仓库自带的 `FakeChain`。核心复现（P1a，PR #11）：

```python
import sys; sys.path[:0] = [".", "tests"]
from test_workforce import EOA, TOKEN, FakeChain
from agent.execution.rpc import RpcClient
from agent.workforce.chain import ChainReader
from agent.workforce import Workforce
from agent.store import Store

reader = ChainReader(RpcClient("http://rpc", post=FakeChain()), "bsc-testnet", 97)
wf = Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)})
mgr = wf.create_agent({"name": "AI Manager", "is_manager": True,
                       "skills": ["wallet_analysis"], "permissions": ["chain:read"]}, "owner")
ws = wf.create_workspace({"name": "BSC Research", "manager_id": mgr["id"], "members": [mgr["id"]]}, "owner")
g = wf.submit_goal(ws["id"], f"Check wallet {EOA}", "owner")
wf.run_until_idle()
print(wf.db.task(g["id"])["status"], wf.db.task(g["id"])["error"])
# PR #11: failed 工作区里没有在岗的员工
# PR #13: review None
```

原始输出（节选）：

```
P1a pr11 workspace_members=[] agents_online=1 agents_total=1 idle=1 submit=accepted mission_status=failed mission_error=工作区里没有在岗的员工
P1b pr11 submit=accepted mission_status=failed mission_error=工作区里没有在岗的员工
P1c pr11 agents_online=2 idle=2 workspace_members=[] submit=accepted mission_status=failed mission_error=工作区里没有在岗的员工
P1a pr13 submit=accepted mission_status=review
P1b pr13 submit=refused: Squad Core squad 里除了主管还没有成员，主管自己也没有可用的技能：先把有技能的员工加进来 missions_created=0
P1c pr13 submit=refused: （同上） missions_created=0
P2  pr11/pr13 execute_calls_for_one_assign=2 distinct_assign_msgs=1 skill_runs=1
P3  pr11/pr13 recovered_items=0 goal_status_after_restart=running subtasks=0
P4  pr11/pr13 goal_after_deadline=failed subtask_after_deadline=cancelled rpc_calls_made_after_goal_failed=4 late_events=[tool.done, task.completed, task.result]
P5  pr11/pr13 two submits -> 2 distinct missions, both reached review, skill_runs=2
P1d pr13 （预检后暂停唯一执行者）failed 团队里没有能做链上分析的员工（需要钱包分析、合约分析等技能）
```

需要真实环境才能补做的测试：BSC 测试网 RPC（需要能访问公共 RPC 的网络）、模型规划与首 Token 延迟（需要 `ANTHROPIC_API_KEY`，会产生费用）、锚定交易确认耗时（需要测试网钱包和测试 BNB）。这些都没有执行。
