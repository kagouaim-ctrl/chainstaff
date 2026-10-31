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

- 视觉照搬 fomo 的实测数值：背景 #060510、面板 #12111A / #161522、1px rgba(203,208,235,0.1) 细线、主文字 #F7F7F7、次文字 #9899A3、强调色 #516AF6、涨 #21C95E、跌 #FF622E；默认字重 500，表格 14/500、表头 12/400；圆角 8 / 12 / 全圆；基本无阴影，过渡 0.15s。颜色在 `src/index.css` 和 `tailwind.config.js`。
- 字体：fomo 用的 Aeonik 是商业授权字体，这里用免费可商用的 Inter Tight 代替；买了 Aeonik 授权后放进 `@font-face`，`tailwind.config.js` 里已把 Aeonik 排在第一位。
- 布局照 fomo 网页版：顶栏（高 64）放 logo、一级入口、居中 384×44 搜索、现金 / 24 小时盒子和头像；底部状态栏；手机上是底部悬浮导航。发现页是首页。模拟盘徽标、紧急暂停全局常驻。演示标识统一放在顶部横幅和底部状态栏。
- 代币详情是独立页面，照 fomo 代币页三栏：代币列表 / 头部数据盒 + K 线 + 群聊、持有者、交易、观点标签 / 买卖面板、关于卡片、你的持仓、安全检查，K 线上标注 Agent 的发现、NO_TRADE 和买卖点。
- 每个代币一个群（LINE / Grok 群组样式的气泡聊天，真人和 Agent 混排）：不持有也能看；持有中的主 Agent 和持有过的真人可以发言；群聊不影响任何交易。另有「观点」标签（参考 fomo Thesis）。
- Agent 详情、交易详情以右侧抽屉打开，可以通过 URL 直接访问（`?agent=risk`、`?order=ord_7f3a`）。

产品计划见 [docs/PLAN.md](docs/PLAN.md)。
