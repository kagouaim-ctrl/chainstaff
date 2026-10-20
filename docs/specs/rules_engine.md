# SPEC · 规则引擎（放款判断）

位置：`agent/rules/`。这是整个产品里唯一决定"钱动不动"的代码。模型只负责把一句话变成结构化请求，结果只是建议；最终结果由这里的确定性代码给出，之后还要再过钱包签名层的限额。

## 输入

- `PaymentRequest`：`request_id`（全局唯一）、`requester`、`payee`（0x 地址）、`amount`（Decimal）、`token`、`category`、`memo`、`created_at`（带时区）。
- `Policy`：白名单（地址 → 名称、是否需审批）、每个币种的单笔上限和每日上限、需审批的类别、风险地址列表、重复检测窗口、支持的币种及精度、`paused`（一键暂停）、`read_only`（试用期只读）、计算"每日"的时区。
- `LedgerView`：只读账本接口，提供 `has_request(request_id)`、`auto_paid_total(token, start, end)`、`recent_payments(payee, token, since)`。

## 输出

`Decision`：`outcome` ∈ {`AUTO_PAY` 绿, `NEEDS_APPROVAL` 黄, `BLOCK` 红}，加上所有命中的原因（代码 + 中文说明）。原因全部列出，不在第一个命中时停下，方便老板一次看清。

## 判断顺序（红优先于黄，黄优先于绿）

红（拦截，一分钱不动）：
1. `PAUSED`：一键暂停已打开。
2. `INVALID_AMOUNT`：金额 ≤ 0、不是有限数、小数位超过币种精度。
3. `UNSUPPORTED_TOKEN`：币种不在支持列表。
4. `INVALID_ADDRESS`：不是 `0x` + 40 位十六进制。
5. `ALREADY_PROCESSED`：同一 `request_id` 已处理过（防止同一笔付两次）。
6. `RISK_FLAGGED`：收款地址在风险列表。
7. `NOT_WHITELISTED`：收款地址不在白名单。
8. `DUPLICATE`：窗口期内（默认 30 天）已有同一收款方、同一币种、同一金额的付款记录（自动付和待签字的都算；被拦截的没动过钱，不算）。

黄（生成 Safe 提案，老板签字）：
9. `READ_ONLY_MODE`：试用期只读，所有付款都要人工确认。
10. `OVER_SINGLE_LIMIT`：金额 > 单笔上限。
11. `OVER_DAILY_LIMIT`：今日已自动付款合计 + 本笔 > 每日上限。只累计 `AUTO_PAY` 的金额，老板签字的不占员工额度。
12. `CATEGORY_NEEDS_APPROVAL`：类别在需审批列表。
13. `PAYEE_NEEDS_APPROVAL`：该白名单条目标记为需审批。

以上都没命中 → 绿。

## 边界情况

- 地址大小写不同视为同一地址（统一转小写比较）。
- 金额刚好等于单笔上限：允许（≤ 上限）。今日累计刚好等于每日上限：允许。
- 某币种没配置上限：按上限 0 处理，即任何金额都要审批，绝不默认放行。
- "每日"按策略时区的自然日计算；跨过零点后额度重置。
- 金额用字符串或 Decimal 传入；传 float 直接报错，避免精度问题。
- 备注或用途里写"忽略规则""紧急"等文字，不影响任何判断（引擎不读 memo）。
- 白名单为空时，所有请求都是红。
- 风险列表和白名单同时包含一个地址时，按红处理。

## 绝对不允许发生

- 白名单外的地址得到绿或黄。
- 同一 `request_id` 第二次得到绿。
- 超过单笔或每日上限的请求得到绿。
- 暂停状态下任何请求得到绿或黄。
- 引擎内部出现 float。

## 验收标准

`tests/test_rules_engine.py` 覆盖以上每条规则、每个边界和每条"不允许发生"，全部通过；规则引擎覆盖率 100%。
