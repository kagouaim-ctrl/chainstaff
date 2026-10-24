# 部署 KAGOU 到 app.kagouai.com（只准备，未部署）

本阶段只准备配置，不部署到生产，也不改 DNS。kagouai.com 和落地页（KAGOUAI 仓库）不动。下面每一步都需要老板确认后由老板（或老板指定的人）在服务器上执行。

## 结构

```
浏览器 ──HTTPS──▶ Caddy（app.kagouai.com，自动证书）──▶ kagou 容器 :8000
                                                     ├─ /            旧控制台（ChainStaff 岗位）
                                                     ├─ /terminal/   KAGOU Console
                                                     ├─ /api/*       REST，/api/wf/stream（SSE），/api/chain/ws（WebSocket）
                                                     ├─ Agent 引擎、BSC 监听、后台巡检（同一进程）
                                                     └─ /data        SQLite 账本 + policy.json（卷）
```

只跑一个 worker：引擎、链上监听和后台巡检是进程内的单例，共用一个 SQLite 文件。要横向扩展，得先把账本换成服务端数据库，这不在本阶段。

## 文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 两阶段构建：先构建 `web/terminal`，再装 Python 服务。非 root 用户（uid 10001），`/healthz` 健康检查，首次启动从示例生成 `policy.json`（测试模式） |
| `.dockerignore` | 不把 `.git`、测试、真实配置和任何 `.env` 打进镜像 |
| `deploy/app.kagouai.com/compose.yml` | kagou + Caddy。kagou 不开公网端口，只读根文件系统，去掉全部 capability，`no-new-privileges` |
| `deploy/app.kagouai.com/Caddyfile` | 自动 HTTPS、HSTS 等安全头、访问日志里删掉 `Authorization`、SSE 不缓冲 |
| `deploy/app.kagouai.com/kagou.env.example` | 环境变量模板，默认只读：测试网、没有钱包、没有实盘付款 |

## 已验证（2026-10-24，在开发容器里）

- `docker build` 成功；容器以 `kagou` 用户运行，只读根文件系统下正常写 `/data`。
- `/healthz` 返回 `{"ok":true}`，Docker 健康状态为 `healthy`；`/`、`/terminal/` 返回 200；`/api/chain/status` 无令牌 401、有令牌 200。
- 容器里连不上 BSC 节点时（开发容器没有 DNS），监听自动退到 HTTP 轮询并报告错误，服务本身不受影响。
- `caddy validate` 通过；`docker compose config` 通过。
- BSC 测试网的真实连接在 GitHub Actions 的 `bsc-testnet` job 里验证过（3 个公共 RPC 节点、WebSocket 新区块、Agent 读链工具），见集成报告第 10 节。

## 没有验证

- 真实域名、证书签发、公网访问：要等 DNS 指向服务器后才能测。
- 生产负载、长时间运行（几天以上）的监听稳定性。
- 真实模型调用（需要 `ANTHROPIC_API_KEY`）。

## 老板需要准备

1. 一台 Linux 服务器（2 核 / 2 GB 起），装好 Docker 和 Compose，开放 80 和 443。
2. 在域名服务商把 `app.kagouai.com` 的 A 记录（和 AAAA，如果有 IPv6）指向这台服务器。**这一步由老板操作，开发不碰 DNS。**
3. 控制台令牌：`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`，只放在服务器的 `kagou.env` 里。
4. （可选）`ANTHROPIC_API_KEY`：只在服务器上，浏览器拿不到。
5. （可选）`ETHERSCAN_API_KEY`：链上监控员的转账历史和国库预警要用。

## 步骤

```bash
git clone https://github.com/kagouaim-ctrl/chainstaff.git kagou && cd kagou
git checkout <要上线的提交>
cd deploy/app.kagouai.com
cp kagou.env.example kagou.env && chmod 600 kagou.env      # 填令牌等
cp ../../config/chain.example.json chain.json               # 测试网；按需改 rpc_urls / ws_urls / confirmations
docker compose up -d --build
docker compose ps                                           # kagou 应为 healthy
curl -s https://app.kagouai.com/healthz                     # {"ok":true}
```

然后打开 `https://app.kagouai.com/terminal/`，用令牌登录，在 On-Chain → Live chain 页看监听状态和 RPC 节点健康。

## 从现有 ChainStaff 迁移数据

新版本只会**新增**表和列（`CREATE TABLE IF NOT EXISTS`、`ALTER TABLE ADD COLUMN`），不删不改旧数据。但仍然要先备份：

```bash
# 在运行旧版本的机器上，先停掉旧服务，再备份
sqlite3 ~/chainstaff/data/chainstaff.db ".backup '/safe/place/chainstaff-$(date +%F).db'"
```

要把旧账本带到新服务器：先 `docker compose up -d` 一次让卷建好，再 `docker compose stop kagou`，把备份拷进卷里命名为 `kagou.db`（以及旧的 `config/policy.json` 拷成 `policy.json`），再 `docker compose start kagou`。

## 备份

```bash
docker compose exec kagou python -c "import sqlite3; s=sqlite3.connect('/data/kagou.db'); d=sqlite3.connect('/tmp/b.db'); s.backup(d); d.close()"
docker compose cp kagou:/tmp/b.db ./kagou-$(date +%F).db
```

建议每天一次，异地保存，保留 30 天。

## 升级和回滚

```bash
git fetch && git checkout <新提交> && docker compose up -d --build     # 升级
git checkout <上一个提交> && docker compose up -d --build              # 回滚（先确认没有不兼容的数据变化）
```

升级前先备份。镜像标签可以用 `KAGOU_VERSION=<提交号> docker compose up -d --build` 区分。

## 上线前检查

- [ ] DNS 已指向服务器，80/443 可达
- [ ] `kagou.env` 权限 600，令牌足够长，没提交进 git
- [ ] 没有设置 `CHAINSTAFF_LIVE`、钱包或签名相关变量（本阶段只读）
- [ ] `chain.json` 是测试网（chain id 97），除非老板明确批准主网只读
- [ ] 旧账本已备份
- [ ] `/healthz` 正常，控制台能登录，Live chain 页显示 `websocket` 已连接
- [ ] 对照 [安全清单](security-checklist.md)
