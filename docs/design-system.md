# ChainStaff Terminal 设计系统

终端（`web/terminal/`）所有页面共用一套样式：`src/styles.css` 顶部的 Token 块加下面的组件类。组件里不写颜色值、不写随意的字号和间距，都从 Token 读。

## 原则

- **结构硬朗，交互柔和**：布局、侧栏、表格是直角；按钮、输入框、浮层按作用给圆角。
- **少画线**：层级先靠背景色阶、间距、字号和字重。线只留在表格行、吸顶表头、Composer 这类真正分隔信息的地方。
- **黄色只用于**：Primary 按钮、当前导航、等人处理的事（审批、Review）。结果用状态色，其余是灰阶。
- **字体**：Inter（英文）→ PingFang SC / Noto Sans SC（中文）→ IBM Plex Mono（数字、哈希、地址、ID、时间）。数字一律 `tabular-nums`。标签用句首大写，不用全大写和字距。

## Token

| 类别 | Token |
| --- | --- |
| 背景 | `--bg-primary` #0B0E11 · `--bg-secondary` #14181D · `--bg-elevated` · `--bg-hover` #1E2329 · `--bg-active` · `--bg-field` · `--bg-control` |
| 文字 | `--text-primary` #EAECEF · `--text-regular` · `--text-secondary` #848E9C · `--text-muted` #5E6673 |
| 线 | `--border-primary` #252A30 · `--border-subtle` · `--border-strong` |
| 品牌 | `--accent-primary` #F0B90B · `--accent-hover` · `--accent-active` · `--accent-soft` · `--accent-border` |
| 状态 | `--status-success` · `--status-danger` · `--status-warning` · `--status-info` · `--status-neutral`，各带 `-soft` |
| 圆角 | `--radius-none` 0 · `-xs` 4 · `-sm` 6 · `-md` 8 · `-lg` 12 · `-xl` 16 · `-full` |
| 字号 | `--text-2xs` 11 · `-xs` 12 · `-sm` 13 · `-base` 14 · `-md` 15 · `-lg` 16 · `-xl` 20 · `-2xl` 24 |
| 间距 | `--space-1..8` = 4 / 8 / 12 / 16 / 20 / 24 / 32 |
| 控件高度 | `--control-xs` 24（表格行内）· `-sm` 28 · `-md` 32 · `-lg` 40 |
| 动效 | `--dur-hover` 140ms · `--dur-menu` 180ms · `--dur-modal` 220ms · `--dur-drawer` 200ms，`prefers-reduced-motion` 下全部关闭 |

## 圆角对照

| 元素 | 圆角 |
| --- | --- |
| 页面布局、侧栏、表格 | 0（带框的小表格 4） |
| 状态徽章、Tag | 4 |
| 普通按钮、输入框、Tooltip、分段控件 | 6 |
| Primary 按钮、下拉菜单、Popover、Toast、Agent 头像 | 8 |
| Modal | 12 |
| 手机底部抽屉 | 16（上方两角） |

## 组件

- **按钮** `.btn` + 变体 `primary` / `secondary`（默认）/ `tertiary` / `ghost` / `danger` / `accent-soft`，尺寸 `xs` / `sm` / 默认 / `lg`，`icon` 方形，`loading` 显示转圈并保持宽度。React 里也可以用 `ui.tsx` 的 `<Button variant size loading>`。
- **输入** `.input`（`sm`），`select.input` 自带箭头；`.search` 包一个图标。
- **状态** 表格和列表里用 `.st`（点 + 文字）；需要跳出来的用 `.badge`；技能、ID 这类标识用 `.tag`。
- **Agent 状态色**：Running 蓝（脉冲）· Waiting 蓝 · In review 黄 · Idle 绿 · Failed 红 · Paused 灰。
- **空状态** `<EmptyState title icon>`：图标、一句话说明现状、下一步按钮。
- **失败** 用 `.fail-sum`（Mission 顶部汇总）、`.feed-line.bad`（时间轴）、`.err-callout`（Inspector），都写出原因原文。
