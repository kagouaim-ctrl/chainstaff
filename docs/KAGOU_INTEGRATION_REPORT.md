# KAGOU 三源整合报告

日期：2026-10-24　｜　开发分支：`claude/project-thread-hfxnft`（草稿 PR #15，叠在 PR #13 上，未合并）　｜　对比基线：PR #13 head `d961a3d`

状态用语（全文统一）：

| 标记 | 含义 |
|---|---|
| **已验证** | 在真实环境跑过（真实 BSC 测试网、真实浏览器），有记录 |
| **已验证（Mock）** | 自动化测试通过，但用的是模拟节点、脚本化模型或假数据 |
| **已实现未验证** | 代码在，没有在真实环境跑过（通常缺 API Key 或网络） |
| **部分** | 只有一部分能力，缺口写在备注里 |
| **仅 UI** | 只有界面，后面没有真实能力 |
| **Mock** | 只有模拟实现 |
| **未实现** | 没有 |

没有 `ANTHROPIC_API_KEY`，所以**真实模型调用没有验证**。Mock 功能都不算生产可用。

---

## 1. 三个来源的关系

| 来源 | 是什么 | 和其他两个的关系 |
|---|---|---|
| **A. 浏览器里跑的 ChainStaff** | 你 Mac 上 `~/chainstaff`，PR #11 的 head `0993f2d` | GitHub ChainStaff PR 栈（#10 → #11 → #12 → #13）的中间一层 |
| **B. GitHub `kagouaim-ctrl/chainstaff`** | `main@0879002`（旧 Telegram AI 团队 + `web/console.html`）加上未合并的 PR #10–#13（Workforce 引擎、Mission、Squad、React 终端） | A 是它的子集。#12、#13 在 A 之上只做了增量：Squad、Preflight（修了"工作区里没有在岗的员工"）、统一设计系统。数据库只新增表和列 |
| **C. GitHub `kagouaim-ctrl/KAGOUAI`** | Next.js 官网（kagouai.com 落地页）和一份旧控制台的静态 demo | 和 A、B 没有共同代码。没有可用的 Agent、权限或合约代码。可借鉴的只有多 RPC 回退的写法和 ERC-8004 测试网身份记录（#2648，注册脚本本来就在 chainstaff 的 `scripts/register_agent.py`） |

结论：以 **B 的 PR #13** 为开发基线，就包含了 A 的全部代码，不会丢失浏览器版本的任何功能或数据。C 不并入代码库，落地页不修改。

你本机还有一个 `kagouaim-ctrl/kagou` 仓库（最新提交 "Phase 11: LINE integration"），不在这次的三个来源里，也没有读取或整合。

## 2. 浏览器版本的来源

2026-10-24 通过 Remote Control 在你的 Mac 上只读确认（没有修改、提交或停止任何东西，没有读出密钥）：

| 项目 | 结果 |
|---|---|
| 代码目录 | `~/chainstaff` |
| 分支 / Commit | `claude/project-thread-pw48i0` / `0993f2d`（PR #11 head） |
| 未提交 / 未推送 | 无，工作区干净 |
| 后端 | uvicorn `backend.app`，`127.0.0.1:8000` |
| 前端 | React + Vite 构建的 `/terminal/` |
| 数据库 | `~/chainstaff/data/chainstaff.db`（SQLite） |
| 其他副本 | 桌面 `前端代码/chainstaff` 是旧的 `main` 副本，没有在用 |

这和之前的技术审计（`docs/KAGOU_TECHNICAL_AUDIT.md`）的推断一致：截图里 "Agents online 1/1 · Idle 1" 这个标签只存在于 PR #11。

**没有覆盖浏览器里正在运行的控制台。** 所有改动都在新分支上，你本机的 `~/chainstaff` 没动过。

## 3. 三方功能对照

A = 浏览器（PR #11），B = GitHub chainstaff（main + PR #12/#13），C = KAGOUAI，KAGOU = 当前开发分支。

| 功能 | A | B | C | KAGOU 现状 | 状态 |
|---|---|---|---|---|---|
| Mission 调度（任务图、消息总线、CAS 状态、预算、截止时间） | 有 | 有 | 无 | 保留，修了 P0 问题（取消、重启后重规划、异步规划、幂等提交、重复投递、员工预算） | 已验证（Mock），调度测试连跑 15 次稳定 |
| Squad / Preflight（"没有在岗员工"） | 有 bug | #12 已修 | 无 | 保留 #12 的修复 | 已验证（Mock） |
| Agent 自主推理（模型 ↔ 工具循环） | 无 | 无 | 无 | 新建 `agent/workforce/runtime.py`：流式调用、限次、限 Token、超时、取消、成本记账 | 已实现未验证（只有脚本化模型测试；没有 API Key） |
| Tool Registry | 无 | 无 | 无 | 新建：注册即可用，拒绝非只读工具和未知权限，每个工具有超时 | 已验证（Mock） |
| 多 Agent 协作（主管规划、上下游传结果） | 部分（只有固定技能） | 部分 | 无 | 主管可以把 `agent_task` 分给不同员工，并行或按依赖执行 | 已实现未验证（模型规划需要 API Key；规则规划已验证（Mock）） |
| 旧的 7 个只读链上技能 | 有 | 有 | 无 | 保留 | 已实现未验证（真实链上没有单独跑过） |
| 新的 6 个链上工具（block_info、tx_lookup、contract_read、event_search、wallet_activity、chain_events） | 无 | 无 | 无 | 新建 | block_info、contract_read、event_search：**已验证**（测试网）；其余：已验证（Mock） |
| 多节点 RPC 池（故障切换、限流、重组安全缓存、链 ID 核对） | 无（单 URL） | 无 | 有回退写法 | 新建 `agent/bsc/rpc.py` | 连通和链 ID 核对：**已验证**（测试网，3 个公共节点）；故障切换和冷却：已验证（Mock） |
| WebSocket 新区块监听（重连、补块、轮询兜底、卡死检测） | 无 | 无 | 无 | 新建 `agent/bsc/listener.py` | **已验证**（测试网，跟到 11 个真实区块，0 次重连）；断线、补块、轮询兜底：已验证（Mock）；长时间运行：未验证 |
| 重组处理、确认数 | 无 | 无 | 无 | 新建：回退到共同祖先，事件标记 reorged | 已验证（Mock）。测试网短时间运行里没有遇到真实重组 |
| 链上事件监控（地址、代币转账、合约事件） | 无 | 无 | 无 | 新建，可选触发 Mission（每个事件只触发一次） | 匹配：**已验证**（测试网，WBNB 转账 4 条确认、2 条待确认）；触发 Mission：已验证（Mock） |
| 浏览器实时推送 | SSE（0.5 秒轮询 SQLite） | 同 A | 无 | SSE 保留；新增 `/api/chain/ws`（WebSocket，序号续传、慢客户端重同步） | 已验证（本机真实 WebSocket 连接，链数据来自模拟节点） |
| KAGOU 控制台（三栏 Workspace） | 有 | 有（#13 设计系统） | 静态 demo | 保留布局和流程；KAGOU 品牌；任务实时阶段；On-Chain → Live chain 页 | 已验证（真实浏览器，桌面和手机宽度；数据来自模拟节点） |
| 人工审批 | 有 | 有 | 无 | 保留 | 已验证（Mock） |
| 结果哈希上链（BSC 测试网） | 有 | 有 | 无 | 保留，默认关闭（需要 `CHAINSTAFF_ANCHOR_WALLET`；部署模板里没有） | 已实现未验证 |
| 财务运营员付款（规则引擎、Safe 提案） | 有 | 有 | 无 | 规则和执行代码保留；**付款请求原来只从 Telegram 进来，现在没有入口** | 部分 |
| 社区客服 / 公告翻译 / 链上监控员 / 营销结算 / 空投反女巫 | 有 | 有 | 无 | 保留在旧控制台（`/`）；社区客服没有对外渠道，只能试算；翻译需要 API Key | 部分 / 已实现未验证 |
| Telegram 机器人、群聊、通知 | 有 | 有 | 无 | **已移除** | — |
| ERC-8004 链上身份 | 脚本 | 脚本 | 记录 #2648 | 保留 `scripts/register_agent.py` | 已实现未验证（本次没跑） |
| 官网 / 落地页 | 无 | 无 | 有 | 不修改 | — |
| 部署到 app.kagouai.com | 无 | 无 | 无 | 只准备配置：Dockerfile、compose、Caddy、环境模板、`/healthz` | 镜像构建和容器运行：已验证（本地）；真实域名：未验证 |

## 4. 架构

```
                     浏览器（KAGOU Console /terminal/，旧控制台 /）
                        │ REST /api/*    │ SSE /api/wf/stream    │ WebSocket /api/chain/ws
                        ▼                ▼                       ▼
 ┌───────────────────────── FastAPI（backend/app.py，单进程单 worker）─────────────────────────┐
 │  workforce_api.py            chain_api.py                     旧岗位 API（财务、客服、翻译…）  │
 │        │                          │                                                         │
 │  Workforce 引擎（engine.py）   ChainService（agent/bsc/service.py）  BackgroundDuties          │
 │   ├ 规划器（规则 / 模型）        ├ ChainListener ── WebSocket newHeads ──┐   （链上监控巡检、     │
 │   ├ 消息总线、任务 DAG、审批     │    └ 断线 → HTTP 轮询兜底              │    国库预警，只写账本） │
 │   ├ Agent Runtime（runtime.py）  ├ ChainProcessor：补块、去重、重组、确认、监控匹配               │
 │   │   └ 模型（只在后端）↔ 工具  ├ Hub（环形缓冲，按序号推送）                                   │
 │   └ Tool Registry（只读）  ◀──── 事件触发 Mission（幂等键 chain-event:{id}）                    │
 │            │                     │                                                         │
 │            └──── RpcPool（多节点、故障切换、限流、重组安全缓存、链 ID 核对）──── BSC 节点          │
 │                                                                                             │
 │  SQLite 账本：旧表 + wf_* + chain_blocks / chain_monitors / chain_events / chain_state         │
 └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

Agent 的工具和链上监听共用同一个 `RpcPool`。

安全边界：
- 工具全部只读，Registry 拒绝注册非只读工具；`eth_sendRawTransaction` 不重试，Agent 也拿不到。
- Agent 不能转移资产或签交易；私钥和助记词不进模型；模型调用和 API Key 只在后端。
- 前端没有任何密钥；控制台令牌只在请求头里（WebSocket 在第一条消息里），部署时访问日志会删掉 `Authorization`。

## 5. 保留的模块

| 模块 | 来源 | 说明 |
|---|---|---|
| `agent/workforce/`（engine、db、planner、missions、skills、chain、anchor、cron） | B（#10–#12） | 调度核心，在其上修复和扩展 |
| `backend/workforce_api.py` | B | 增加了执行限制等字段 |
| `web/terminal/`（Terminal、Agents、Workspace、Tasks、On-Chain、Skills） | A/B（#13 设计系统） | 三栏 Workspace 和全部流程不变 |
| `agent/rules/`、`agent/execution/`、`agent/store.py` | B（main） | 规则引擎、执行、账本，不动 |
| `agent/skills/*`、`agent/team.py`、`agent/front_desk.py` | B（main） | 旧岗位；去掉了 Telegram 的说法 |
| `web/console.html`、`backend/app.py` | B（main） | 旧控制台；去掉了 Telegram 入口 |
| `scripts/register_agent.py` 等 | B | 不动 |

## 6. 迁移的模块

| 原来 | 现在 | 说明 |
|---|---|---|
| `bot/main.py` 里的 `monitor_once` / `alert_loop` / `check_alerts_once` | `agent/background.py`（`BackgroundDuties`），由服务器进程启动 | 链上监控员巡检和国库预警照常写进账本，只是不再发到群里。`KAGOU_BACKGROUND=0` 可关闭 |
| 国库预警开关（原 `/api/telegram/settings`） | `PUT /api/treasury/alerts` | 设置仍存在原来的 `telegram_settings` 行里，兼容已有账本 |
| 单 URL `RpcClient`（Workforce 部分） | `RpcPool`（`build_workforce` 默认使用） | 旧财务执行器仍用原 `agent/execution/rpc.py`，未改动 |
| KAGOUAI 的多 RPC 回退思路 | `agent/bsc/rpc.py` 重写 | 没有复制代码 |

## 7. 移除或停用的重复功能

| 移除 | 原因 / 处理 |
|---|---|
| `bot/` 整个包、`aiogram` 依赖 | KAGOU 不要 Telegram。先把后台职责迁到服务器，再删入口 |
| `/api/telegram`、`/api/telegram/settings`、`/api/telegram/chat`、`/api/telegram/send` | 同上 |
| `/api/translator/drafts/{id}/post`（发到群） | 同上；草稿仍可编辑、核对、复制 |
| 旧控制台的 Telegram 页、群聊模拟器、"发到群里"按钮、审计日志的 Telegram 筛选 | 同上；简报改成"复制" |
| KAGOUAI 的静态控制台 demo | 和 KAGOU Console 重复，不迁移 |

保留但不再对外使用（被测试和旧数据引用，按要求不删）：`FrontDesk` 和聊天命令（`/api/chat/simulate` 试算接口仍在）、定时报告文本生成、账本里的 `outbox` 表、`TelegramSettings` 这个类名、`CHAINSTAFF_*` 环境变量名。

## 8. 新增功能

1. **Agent Runtime**：员工用自己的模型推理、调用自己被授权的只读工具，直到给出结论。流式输出；每次运行限制工具调用次数、模型调用次数、Token、单次超时、effort；按 Mission、员工预算和截止时间停止；成本记到任务、Mission 和员工。
2. **Tool Registry**：注册一个 Skill 就同时出现在规划器、Agent 和 Skills 页；拒绝非只读工具。
3. **调度 P0 修复**：协作式取消、规划中途重启后只重规划一次、模型规划移出调度线程、提交幂等键、去重投递、员工自己的预算生效。
4. **BSC 实时数据层**（`agent/bsc/`）：RPC 池、WebSocket 新区块监听、补块、去重、重组回滚、确认数、监控、事件触发 Mission、`/api/chain/*` REST 和 `/api/chain/ws`。
5. **6 个只读链上工具**（`agent/workforce/chaintools.py`）。
6. **控制台**：KAGOU 品牌；Workspace 里的任务实时阶段（排队、推理中、调用工具、读链、完成、失败、取消）；On-Chain → Live chain 页（监听状态、RPC 节点和健康检查、监控增删改、实时事件流）；员工表单的执行限制。
7. **服务器后台任务**（`agent/background.py`）。
8. **部署准备**：Dockerfile、`deploy/app.kagouai.com/`、`/healthz`、`docs/deploy-kagou.md`。
9. **CI**：独立的 `bsc-testnet` job，在真实测试网上做只读冒烟测试，不卡 PR。

## 9. 变更文件

相对 PR #13 head `d961a3d`：68 个文件（含本报告），+6,888 / −815 行（新增 30，修改 36，删除 2）。提交：

| 提交 | 内容 |
|---|---|
| `133c72a` | 合并 PR #10 的测试修复和审计报告 |
| `3d18882` | 调度 P0 修复 |
| `31dc492` | Agent Runtime + Tool Registry |
| `4208f24` | BSC 实时数据层 |
| `2736c99` | 控制台：KAGOU 品牌、实时阶段、Live chain 页、执行限制 |
| `c70db4b` | 覆盖率补齐 + 修复补块途中遇到重组时停在半路的问题 |
| `07a8565` | 修复一个在有网络的 CI 上会连到真实节点的测试 |
| `f111397` | BSC 测试网只读冒烟测试（独立 CI job） |
| `8e8fd39` | 移除 Telegram |
| `6d9f77f` | 部署准备（只写配置） |
| `ffb5082` | 本报告 |

按目录：

- **后端 Python**：新增 `agent/background.py`、`agent/bsc/{__init__,hub,listener,rpc,service,store}.py`、`agent/workforce/{runtime,chaintools}.py`、`backend/chain_api.py`；修改 `agent/config.py`、`agent/team.py`、`agent/workforce/{__init__,chain,db,engine,planner,skills}.py`、`backend/{app,workforce_api}.py`；删除 `bot/__init__.py`、`bot/main.py`。
- **前端**：新增 `web/terminal/src/components/{ChainLive,LiveBoard}.tsx`、`src/lib/{chainstream,live}.ts`；修改 `AgentForm`、`ExecutionFeed`、`RunPanel`、`TaskComposer`、`TerminalHeader`、`OnChain`、`Workspace`、`api.ts`、`events.ts`、`types.ts`、`main.tsx`、`styles.css`、`index.html`；旧控制台 `web/console.html`。
- **配置和依赖**：`pyproject.toml`（加 `websockets`，去掉 `aiogram`）、`config/chain.example.json`（`rpc_urls`、`ws_urls`、`confirmations`）、`config/policy.example.json`、`.github/workflows/ci.yml`、`.gitignore`、`.dockerignore`、`Dockerfile`、`deploy/app.kagouai.com/*`。
- **测试**：新增 `tests/test_{agent_runtime,agent_runtime_edges,bsc,bsc_edges,bsc_perf,bsc_testnet,engine_edges,scheduler_p0}.py`；修改 `tests/test_{console_full,employee,team,workforce}.py`。
- **文档**：`README.md`、`docs/{KAGOU_TECHNICAL_AUDIT,KAGOU_INTEGRATION_REPORT,deploy-kagou,deploy-bnb,mainnet-turnkey}.md`。

依赖变化：新增 `websockets>=13,<16`；移除 `aiogram`。前端依赖没有变化。

## 10. 测试

| 类别 | 内容 | 结果 | 性质 |
|---|---|---|---|
| 单元 + 集成 | `pytest --cov=agent` | **456 通过**，3 个测试网测试默认跳过；覆盖率 **99.04%**（CI 门槛 99%） | Mock |
| Agent 执行 | 脚本化模型：多轮工具调用、暂停续写、截断、Token 和预算上限、坏的工具调用、工具报错、取消 | 通过 | Mock，**不代表真实模型行为** |
| Mission 调度 | 取消、重启、并发、幂等、重复投递、超时 | 通过；调度测试连跑 15 次稳定 | Mock |
| BSC 数据层 | 模拟节点（HTTP + 真 WebSocket 服务）：故障切换、限流、缓存、补块、重组、确认、重连、卡死检测、轮询兜底、网关鉴权和续传 | 通过 | Mock |
| **BSC 测试网** | CI `bsc-testnet` job（2026-10-24，[运行记录](https://github.com/kagouaim-ctrl/chainstaff/actions/runs/40143397797)） | **3/3 通过** | **真实** |
| 前端 | `tsc` + `vite build`（CI），`npm audit`；Playwright 在演示服务器上检查 Live chain 页（桌面和手机宽度）、旧控制台去掉 Telegram 后各页面无 JS 错误 | 通过 | 真实浏览器，模拟链数据 |
| 安全扫描 | `pip-audit`、Semgrep `p/python` + `p/secrets` | 通过 | — |
| 部署 | `docker build`、容器以非 root 只读运行、`/healthz` 健康、`caddy validate`、`docker compose config` | 通过 | 本地，未上公网 |

**BSC 测试网实测数据**（GitHub Actions 机器，chain id 97）：

| 项目 | 结果 |
|---|---|
| RPC 节点 | `data-seed-prebsc-1-s1.bnbchain.org`（285 ms）、`bsc-testnet-rpc.publicnode.com`（226 ms）、`data-seed-prebsc-2-s1.bnbchain.org`（388 ms），都返回 chain id 97 |
| WebSocket 监听 | `wss://bsc-testnet-rpc.publicnode.com`，5.9 秒跟到 11 个新区块，0 次重连，0 次补块，0 次重组 |
| 单区块处理（含 RPC 往返） | P50 134 ms，P95 421 ms |
| WBNB 转账监控 | 4 条已确认，2 条待确认（3 个确认数） |
| Agent 工具 | `contract_read` 读到 WBNB `symbol()`=WBNB、`decimals()`=18；`event_search` 近 200 个区块 54 条 Transfer |

**本地性能测试**（进程内，不含真实网络往返）：缓存命中 P95 0.003 ms；单区块（200 笔交易、60 条日志、5 个监控）P95 约 16 ms；Hub 到浏览器 WebSocket P95 约 0.5 ms。

## 11. 未完成和已知限制

1. **真实模型调用没有验证**：需要你提供 `ANTHROPIC_API_KEY`（付费服务，要你确认后才会用）。
2. **长时间测试网监听没有验证**：只跑了约 6 秒，没有遇到真实重组。建议部署到测试服务器后连续跑 24 小时以上再看。
3. **主网没有配置**，也没有验证。
4. **付款请求没有入口**：原来只从 Telegram 进来。财务运营员的实盘付款暂时不能用；链上写操作要先单独设计权限和人工审批，本阶段不做。
5. **社区客服没有对外渠道**，只能在控制台试算。
6. **定时周报、日报、简报不再推送**，只能在控制台按需查看。
7. **SSE 事件流仍是每 0.5 秒轮询 SQLite**，没有改成事件驱动。
8. **两个控制台并存**：旧岗位在 `/`，KAGOU Console 在 `/terminal/`，还没合并成一个。
9. **只能单 worker**：引擎、监听、后台任务都是进程内单例，共用一个 SQLite。要扩展先换数据库。
10. **内部旧名字**：`TelegramSettings`、`FrontDesk` 的聊天命令、`outbox` 表、`CHAINSTAFF_*` 环境变量、前端包名 `chainstaff-terminal`。不影响功能，后续可以统一改名。
11. **PR 状态**：PR #10–#13 和本分支的草稿 PR #15 都没合并。合并和关闭由你决定。
12. **`docs/KAGOU_TECHNICAL_AUDIT.md`** 是整合前的审计快照，里面提到的 `bot/main.py` 等已不存在。
13. **一次偶发测试失败**：本地在 Docker 构建刚结束、机器负载高时，整套测试里有 1 项失败过一次，之后连跑 10 次都通过，没能复现出是哪一项。CI 上没有出现过。
14. 需要你确认：`kagouaim-ctrl/kagou`（LINE 集成）这个仓库是否也要纳入。

## 12. 部署方案

详见 [deploy-kagou.md](deploy-kagou.md)。要点：

- **目标**：`app.kagouai.com`。只准备了配置，**没有部署，没有改 DNS，kagouai.com 和落地页不动**。
- **组成**：一个 KAGOU 容器（非 root、只读根文件系统、无公网端口）加一个 Caddy（自动 HTTPS、安全头、日志去掉令牌）。数据（SQLite 账本和 `policy.json`）在 Docker 卷里。
- **默认只读**：测试网、没有钱包、没有实盘付款、不设任何签名相关变量。
- **你需要做的**：准备服务器；把 `app.kagouai.com` 的 DNS 指向它；在服务器上填 `kagou.env`（控制台令牌，可选的 API Key）；`docker compose up -d --build`。
- **上线前**：先备份 `~/chainstaff/data/chainstaff.db`。新版本只新增表和列，不删旧数据，但第一次启动会改表结构。
- **建议顺序**：先在测试服务器上跑测试网 24 小时以上，确认监听稳定、预警正常，再决定是否上 `app.kagouai.com`，最后再讨论主网只读和任何链上写操作。
