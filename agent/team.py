"""The AI team: which roles exist, which ones this customer has hired, and whether each is on.

One deployment, one Telegram bot, several employees. The finance ops employee is the only
role that can move money; every other role reads, answers, drafts or alerts.
The roster lives in the shared SQLite settings table, so the console can hire or switch
an employee off and the bot picks it up on its next message.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from agent.store import Store

LIVE, SOON = "live", "soon"


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    summary: str
    duties: tuple[str, ...]
    status: str = LIVE
    touches_money: bool = False
    needs: tuple[str, ...] = ()  # what the deployment needs before this role can work fully
    commands: tuple[tuple[str, str, bool], ...] = field(default=())  # (command, what it does, owner only)


ROLES: tuple[Role, ...] = (
    Role(
        "finance_ops",
        "财务运营员",
        "群里一句话发起付款，按老板定的规则付、提案或拦截，每周出账。",
        ("白名单和限额内自动付款", "超额生成 Safe 提案等老板签字", "拦截陌生地址、重复付款和风险地址", "每周对账周报"),
        touches_money=True,
        needs=("规则文件", "测试网或主网钱包（实盘时）"),
        commands=(
            ("付 Alice 300 USDT 设计费", "发付款请求（不用斜杠）", False),
            ("/rules", "查看当前规则", False),
            ("/today", "今日账本", False),
            ("/pending", "等老板签字的付款", False),
            ("/report", "上周周报", False),
            ("/approve 编号", "老板确认已签字付款", True),
            ("/reject 编号", "老板驳回", True),
            ("/pause", "暂停所有付款", True),
            ("/resume", "恢复付款", True),
        ),
    ),
    Role(
        "community_support",
        "社区客服",
        "7×24 在群里回答常见问题、查交易状态，识别冒充管理员和索要助记词的骗子，答不了的转人工。",
        ("按项目 FAQ 回答问题", "识别骗局：索要助记词、冒充管理员、钓鱼链接、私聊诱导", "查交易状态", "答不了的开工单转给管理员"),
        commands=(
            ("/faq", "常见问题列表", False),
            ("/tx 交易哈希", "查一笔交易的状态", False),
            ("/human 问题", "转人工", False),
            ("/tickets", "未处理的工单", True),
        ),
    ),
    Role(
        "chain_monitor",
        "链上监控员",
        "盯住国库和关键地址：大额转出、陌生对手方、地址投毒、无限授权、余额过低，第一时间在群里报警，每天出简报。",
        ("大额转出预警", "陌生对手方和地址投毒识别", "无限授权和 NFT 全部授权预警", "余额低于下限提醒", "每日链上简报"),
        needs=("要盯的地址", "Etherscan API key（BSC 用 V2 多链接口）"),
        commands=(
            ("/watch", "正在盯的地址", False),
            ("/alerts", "最近的预警", False),
            ("/brief", "链上简报", False),
        ),
    ),
    Role(
        "announcement_translator",
        "公告翻译员",
        "把中文公告翻成英文、日文等，保留术语、数字和地址，老板确认后再发到群里。",
        ("按项目术语表翻译", "自动核对数字、地址和链接没有翻错", "草稿必须老板确认才发出", "多语言一次出稿"),
        needs=("ANTHROPIC_API_KEY",),
        commands=(
            ("/translate 公告原文", "生成翻译草稿", True),
            ("/post 编号", "确认并发到群里", True),
            ("/discard 编号", "作废草稿", True),
        ),
    ),
    Role("market_brief", "市场日报员", "每天早上汇总行情、项目和竞品动态，发一份简报到群里。",
         ("行情和链上数据汇总", "竞品和生态动态", "每日定时推送"), status=SOON),
    Role(
        "campaign_payouts",
        "营销结算员",
        "KOL 费用、活动奖励按名单和规则批量结算：核对去重、按规则算钱，生成 Safe 批量提案给老板签。",
        ("名单核对：地址格式、重复、风险名单、同一活动重复领取", "按固定金额或积分分奖池，单人上限和预算", "接上反女巫结果，自动剔除女巫地址", "导出 Safe 批量交易文件，老板在 Safe 里一次签完"),
        touches_money=True,
        needs=("Safe 多签地址", "代币合约地址（CHAINSTAFF_CHAIN）"),
        commands=(("/payouts", "最近的结算批次", True),),
    ),
    Role("growth_ops", "增长运营员", "跟踪社区增长、活动数据和留存，每周给出复盘。",
         ("社群和社媒数据跟踪", "活动效果复盘", "每周增长报告"), status=SOON),
    Role("listing_research", "上币尽调助理", "收到上币或合作申请时，先做合约、持仓和团队的基础尽调。",
         ("合约权限和代币分布检查", "团队和历史项目核查", "出一页尽调摘要"), status=SOON),
    Role(
        "airdrop_sybil",
        "空投反女巫",
        "空投前逐个地址查资金来源和链上行为，找出批量号和女巫团伙，导出干净名单和剔除理由。",
        ("同一资金来源、名单内互相打钱的团伙", "同一时间批量创建、操作完全相同的批量号", "几乎没活动和快照前新建的钱包", "每个剔除都有理由，干净名单一键交给营销结算员"),
        needs=("Etherscan API key（BSC 用 V2 多链接口）",),
        commands=(("/sybil", "最近的反女巫检查", True),),
    ),
    Role("deliverable_check", "验收员", "外包和赏金任务交付时，按验收标准逐条核对，再交给财务付款。",
         ("按验收清单逐条核对", "附证据链接", "通过后转财务运营员付款"), status=SOON),
)

ROLE_BY_ID = {r.id: r for r in ROLES}


@dataclass
class Member:
    role: str
    name: str
    enabled: bool = True
    hired_at: str = ""


class Team:
    KEY = "team"

    def __init__(self, store: Store) -> None:
        self.store = store

    def members(self) -> list[Member]:
        raw = self.store.get_setting(self.KEY)
        if raw is None:
            # Deployments from before the team existed only had the finance employee.
            return [Member("finance_ops", ROLE_BY_ID["finance_ops"].name, True, "")]
        return [Member(**m) for m in json.loads(raw) if m.get("role") in ROLE_BY_ID]

    def _save(self, members: list[Member]) -> None:
        self.store.set_setting(self.KEY, json.dumps([asdict(m) for m in members], ensure_ascii=False))

    def get(self, role: str) -> Member | None:
        return next((m for m in self.members() if m.role == role), None)

    def is_on(self, role: str) -> bool:
        m = self.get(role)
        return bool(m and m.enabled)

    def hire(self, role: str, name: str | None = None, by: str = "console") -> Member:
        spec = ROLE_BY_ID.get(role)
        if spec is None:
            raise ValueError(f"没有这个岗位：{role}")
        if spec.status != LIVE:
            raise ValueError(f"{spec.name}还没上线，暂时不能雇佣")
        members = self.members()
        if any(m.role == role for m in members):
            raise ValueError(f"{spec.name}已经在团队里了")
        m = Member(role, (name or spec.name).strip()[:30] or spec.name, True, datetime.now(timezone.utc).isoformat())
        self._save(members + [m])
        self.store.log_event("audit", f"雇佣 {m.name}", by, {"role": role}, employee=role)
        return m

    def update(self, role: str, enabled: bool | None = None, name: str | None = None, by: str = "console") -> Member:
        members = self.members()
        m = next((m for m in members if m.role == role), None)
        if m is None:
            raise ValueError(f"团队里没有 {ROLE_BY_ID[role].name if role in ROLE_BY_ID else role}")
        changes = []
        if enabled is not None and enabled != m.enabled:
            m.enabled = enabled
            changes.append("上岗" if enabled else "停用")
        if name is not None and name.strip() and name.strip()[:30] != m.name:
            m.name = name.strip()[:30]
            changes.append(f"改名为 {m.name}")
        if changes:
            self._save(members)
            self.store.log_event("audit", f"{ROLE_BY_ID[role].name}：" + "，".join(changes), by, {"role": role}, employee=role)
        return m

    def dismiss(self, role: str, by: str = "console") -> None:
        if role == "finance_ops":
            raise ValueError("财务运营员是基础岗位，只能停用，不能解雇")
        members = self.members()
        if not any(m.role == role for m in members):
            raise ValueError("团队里没有这个岗位")
        self._save([m for m in members if m.role != role])
        self.store.log_event("audit", f"解雇 {ROLE_BY_ID[role].name}（设置保留，可随时重新雇佣）", by, {"role": role}, employee=role)

    def commands(self) -> list[dict]:
        """Bot commands of the employees that are currently on, for /help and the console."""
        out = []
        for m in self.members():
            if m.enabled:
                for cmd, desc, owner_only in ROLE_BY_ID[m.role].commands:
                    out.append({"cmd": cmd, "desc": desc, "owner_only": owner_only, "role": m.role, "employee": m.name})
        out.append({"cmd": "/help", "desc": "显示这份说明", "owner_only": False, "role": "", "employee": ""})
        out.append({"cmd": "/team", "desc": "团队里有哪些 AI 员工", "owner_only": False, "role": "", "employee": ""})
        return out

    def help_text(self) -> str:
        lines = ["我们团队的 AI 员工："]
        for m in self.members():
            if not m.enabled:
                continue
            lines.append(f"\n【{m.name}】{ROLE_BY_ID[m.role].summary}")
            for cmd, desc, owner_only in ROLE_BY_ID[m.role].commands:
                lines.append(f"{cmd} · {desc}{'（老板）' if owner_only else ''}")
        if len(lines) == 1:
            lines.append("现在没有在岗的员工。")
        return "\n".join(lines)

    def roster_text(self) -> str:
        on = [m for m in self.members() if m.enabled]
        if not on:
            return "现在没有在岗的 AI 员工。"
        return "在岗的 AI 员工：\n" + "\n".join(f"· {m.name}：{ROLE_BY_ID[m.role].summary}" for m in on)
