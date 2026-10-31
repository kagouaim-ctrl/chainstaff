# Meme Agent

7×24 小时自主运行的 Meme 币交易 Agent 平台。用户的主 Agent（交易 Agent，同时担任总策划）在云端盯盘、研究、决策、执行经风控批准的交易、确认链上结果并管理持仓。

> **当前状态：只有前端，全部是演示数据。** 后端（数据适配器、Agent 运行时、风控、执行、持仓）尚未实现。界面上所有数字、Agent 状态、活动和交易都是固定的演示样例，每个模块都标有「演示」，不代表真实行情或交易，也不会实时变化。需要后端的操作（交给 Agent 研究、下单、保存风控参数、群聊发言、紧急暂停）都会明确提示「后端未接入」，不会假装成功。

## 本地运行

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # 单元与页面测试
npm run lint && npm run typecheck && npm run build
```

截图自查（每个页面 1440px 和 375px 各一张，同时检查控制台报错和横向溢出）：

```bash
npm run build && npm run preview &
TOKEN=<演示代币地址> npm run screenshots   # 输出到 frontend/screenshots/
```

## 前端结构

```
frontend/src
├── app/          路由、顶部导航、全局横幅、搜索（/）、状态（暂停、自选）
├── api/          数据源接口 source.ts + 演示数据 demo.ts + TanStack Query hooks
├── domain/       类型、订单状态机、风控默认值与放宽检测、角色定义
├── components/   基础组件（四态 DataState、抽屉、对话框、标签……）
├── features/     K 线与资金曲线、交易/Agent 详情抽屉、决策链、代币群聊、交易面板
└── pages/        总览、发现、代币详情、Agents、实时动态、持仓、交易记录、策略
```

后端实现后，只需新增一个实现 `DataSource` 接口的 HTTP/WebSocket 数据源并替换 `demoSource`。

## 设计

- 深色主题，绿涨红跌；颜色全部走 CSS 变量（`src/index.css`）。
- 一级入口全部在顶部导航，没有左侧边栏；模拟盘徽标、Agent 运行数、紧急暂停全局常驻。
- 代币详情是独立页面（参考 gmgn 的三栏：代币列表 / K 线 + 信息标签 / 交易面板 + 安全检查），K 线上标注 Agent 的发现、NO_TRADE 和买卖点。
- 每个代币一个群：不持有也能看；持有中的主 Agent 和持有过的真人可以发言；群聊不影响任何交易。
- Agent 详情、交易详情以右侧抽屉打开，可以通过 URL 直接访问（`?agent=risk`、`?order=ord_7f3a`）。

产品计划见 [docs/PLAN.md](docs/PLAN.md)。
