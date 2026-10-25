# KAGOU 架构

KAGOU — AI-Native On-Chain Agent Platform。本文件描述当前分支上**实际存在**的结构；还没做的部分在 [路线图](KAGOU_PRODUCTION_ROADMAP.md) 里，不在这里冒充已有。

## 1. 运行形态

```
                    ┌──────────── Demo（www.kagouai.com/demo/，静态）────────────┐
                    │ 同一份控制台代码，VITE_DEMO=1 构建：GET 读 demo-data.json，   │
                    │ 写操作在浏览器里直接拒绝，不开 SSE / WebSocket                │
                    └──────────────────────────────────────────────────────────┘

 浏览器（KAGOU Console，/terminal/）
   │  REST /api/*            （Authorization: Bearer <令牌>）
   │  SSE  /api/wf/stream     （fetch 读，令牌在请求头，断线按 id 续传）
   │  WS   /api/chain/ws      （第一条消息带令牌，按序号续传）
   ▼
 ┌──────────────────── FastAPI（backend/app.py，单进程单 worker）────────────────────┐
 │ 鉴权：所有者令牌 / 只读演示令牌（非 GET 一律 403）                                  │
 │ CORS：KAGOU_CORS_ORIGINS 精确名单（默认同源）；WS 自查 Origin                       │
 │ /healthz（存活）  /readyz（数据库可读）  /api/system（真实能力说明）                  │
 │                                                                                │
 │ workforce_api.py ── Workforce 引擎（agent/workforce/engine.py）                   │
 │   ├ 规划：模型规划（有 key）或规则规划（只认 0x 地址），提交前 preflight 拒绝跑不了的任务 │
 │   ├ 任务图、消息总线（去重）、CAS 状态、预算、截止时间、取消、重启恢复                 │
 │   ├ Agent Runtime（runtime.py）：模型 ↔ 工具循环，步数/Token/超时/预算上限，成本记账  │
 │   └ Tool Registry（skills.py + chaintools.py）：只读工具，权限、超时、输入输出 schema  │
 │ chain_api.py ── ChainService（agent/bsc/）：RPC 池、newHeads 监听、补块、重组、监控   │
 │ 旧岗位 API（财务、客服、翻译…，旧控制台 /）                                         │
 │                                                                                │
 │ SQLite（CHAINSTAFF_DB，生产在 /data 卷）：账本 + wf_* + chain_*                      │
 └────────────────────────────────────────────────────────────────────────────────┘
          │ 模型调用只在后端（ANTHROPIC_API_KEY 不出服务器）
          │ RPC：BSC 公共节点（测试网 97），只读
```

## 2. Demo / Live 隔离

| 方面 | 做法 | 代码 |
|---|---|---|
| 模式判断 | 编译时 `import.meta.env.VITE_DEMO`；Live 构建里 demo 分支是死代码 | `web/terminal/src/lib/demo.ts` |
| 数据来源 | Demo 只读快照；Live 只调用后端 | `src/lib/api.ts` |
| 后端故障 | Live：任何请求网络失败或 502/503/504、SSE 断开 → 顶部红色横幅，写明原因和数据停在几点，自动重试；**没有回退到示例数据** | `src/lib/health.ts`、`TerminalShell.tsx` |
| 能力说明 | `/api/system` 说清模型、链、数据库是否持久、是否允许写；Demo 自己回答 `mode: "demo"` | `backend/app.py`、`demo.ts` |
| 模型缺失 | 顶栏 `Model off`；preflight 写明“会改用规则规划”；Settings 列出缺哪个变量 | `TerminalHeader.tsx`、`engine.preflight` |
| 只读演示账号 | `KAGOU_DEMO_TOKEN`：能看全部页面，所有非 GET 由一个中间件拒绝 | `backend/app.py` |

## 3. 前端 ↔ 后端接口

控制台调用 50 个不同的 API 路径（REST + SSE + WS），全部在后端有对应路由；`tests/test_live_mode.py::test_every_console_api_path_exists_on_the_server` 扫描前端源码自动核对，新增前端调用而后端没有时测试失败。

主要分组：

| 分组 | 路径 | 用途 |
|---|---|---|
| 会话和系统 | `/api/session`、`/api/system`、`/api/wf/meta` | 角色、真实能力、元数据和事件游标 |
| Agent | `/api/wf/agents[/{id}][/run|/duplicate|/status]`、`/api/wf/agents/bulk` | 创建、编辑、复制、启停、归档、删除、单独运行 |
| Squad / Workspace | `/api/wf/squads…`、`/api/wf/workspaces…` | 团队、主管、成员、权限 |
| Mission | `/api/wf/missions/preflight`、`/api/wf/workspaces/{id}/goals`、`/api/wf/squads/{id}/missions`、`/api/wf/missions[/{id}]` | 预检、提交（`Idempotency-Key`）、读取 |
| 任务 | `/api/wf/tasks…`（含 cancel / retry / anchor / CSV） | 执行明细 |
| 审批 | `/api/wf/approvals…` | 人工审核 |
| 链上 | `/api/wf/chain…`、`/api/wf/registry`、`/api/chain/*`、`/api/chain/ws` | 执行记录验证、链上身份、实时链数据和监控 |
| 实时 | `/api/wf/stream`、`/api/wf/activity` | 事件流和历史 |
| 其他 | `/api/wf/skills…`、`/api/wf/memory…`、`/api/wf/schedules…`、`/api/wf/ledger` | 工具、记忆、定时任务、执行账本 |

数据结构以后端返回为准，前端类型在 `web/terminal/src/lib/types.ts`。

## 4. 实时事件

`/api/wf/stream` 推送的是数据库里的消息行（每条有自增 id），不是前端动画。一个 Mission 在真实服务器上的事件顺序（`scripts/live_check.py` 实测）：

`user.instruction → task.created → goal.submitted → agent.started → plan.created → task.assign → task.assigned → agent.working → tool.start → tool.done → task.completed → handoff → task.result → … → goal.review → approval.requested`

对应需求里的阶段：Mission Created（`task.created`）、Planning（`agent.started` / `plan.created`）、Agent Assigned（`task.assigned`）、Tool Called（`tool.start`）、Tool Completed（`tool.done`）、Agent Result（`task.result`）、Mission Completed / Failed（`task.completed` / `task.failed` 在目标任务上）。

SSE 目前每 0.5 秒查一次 SQLite（单进程足够；多实例前需要换成事件驱动）。链上数据单独走 `/api/chain/ws`，原因是它是推送型的高频流（新区块、事件确认、重组），需要按序号续传和慢客户端重同步。

## 5. 链上：两件不同的事

| | On-Chain Intelligence | Agent Execution Verification |
|---|---|---|
| 是什么 | 读真实链上数据（区块、余额、合约、事件） | 记录和审核 Agent 的执行结果 |
| 来源 | BSC RPC（`agent/bsc/`、只读工具） | 引擎的任务和审批记录，可选把结果哈希写到测试网 |
| 状态 | 数据带区块高度 | `in_progress → awaiting_review → offchain_completed`；只有真实广播并确认的锚定交易才是 `onchain_confirmed` |

`agent/workforce/missions.py::proof` 决定状态；没有锚定交易时永远不会显示为链上确认（`live_check` 实测：审批后为 `offchain_completed`）。

## 6. 安全边界（现状）

- 只读：工具注册表拒绝非只读工具；Agent 没有签名或转账能力；私钥不进模型。
- 模型 key 只在后端；前端没有任何密钥；令牌只在请求头或 WS 第一条消息。
- 单一所有者令牌 + 可选只读演示令牌。**多用户、角色权限、API 限流还没有**（见路线图）。
- 审计日志：员工、Mission、审批、配置变更写入 `events` 表。
