"""Skills (tools): the only things an agent can do. Each one declares its input and output schema,
the permissions it needs, a timeout and a version, and runs against real chain data.

They live in a registry (REGISTRY / SKILL_BY_ID): the built-ins below register on import, and more
tools register the same way (see chaintools.py) without touching the engine or the agent runtime.
An agent reasoning with a model calls them as tools; the engine checks permissions on every call.

Every registered tool is read-only. None of them can sign or send a transaction, and the registry
refuses a tool that says otherwise.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from eth_utils import keccak

from .chain import EIP1967_ADMIN, EIP1967_IMPL, SELECTORS, Cancelled, ChainReader, push4_selectors

ADDRESS = {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"}
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")

# Permissions a skill can require; an agent must hold every one of them to run it.
PERMISSIONS = {
    "chain:read": "读取链上数据（RPC）",
    "net:etherscan": "调用 Etherscan / BscScan API",
    "report:write": "生成报告并写入工作区记忆",
    "model:invoke": "调用自己配置的 AI 模型，自主选择工具完成任务",
}


class SkillError(Exception):
    """A skill could not run; the message is shown to the user as-is."""


class SkillTimeout(Exception):
    """A tool ran past its own timeout. Treated like a network blip: retried, then reassigned."""


@dataclass
class SkillContext:
    reader: ChainReader | None
    etherscan: Callable[[dict], Any] | None = None   # params -> parsed JSON, key already applied
    tokens: dict[str, tuple[str, int]] = field(default_factory=dict)  # symbol -> (address, decimals)
    upstream: list[dict] = field(default_factory=list)  # results handed over by other agents
    logs: list[str] = field(default_factory=list)
    cancelled: Callable[[], bool] | None = None  # true once the task was cancelled or timed out
    deadline: float | None = None  # time.monotonic() after which the running tool gives up

    def check(self) -> None:
        """Raise Cancelled if the task is gone, SkillTimeout past the tool's deadline; the readers call it
        around every RPC request, so a tool stops at its next request instead of running on."""
        if self.cancelled and self.cancelled():
            raise Cancelled("任务已取消")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise SkillTimeout("工具执行超时")

    def log(self, line: str) -> None:
        self.logs.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {line}")

    def need_chain(self) -> ChainReader:
        if self.reader is None:
            raise SkillError("没有配置链（CHAINSTAFF_CHAIN），读不到链上数据")
        return self.reader


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    description: str
    input_schema: dict
    output_schema: dict
    permissions: tuple[str, ...]
    version: str
    run: Callable[[SkillContext, dict], dict]
    developer: str = "KAGOU"
    compatibility: str = "BSC 主网 / 测试网"
    requires: tuple[str, ...] = ()  # environment the deployment needs, e.g. ETHERSCAN_API_KEY
    timeout_s: float = 30.0  # one run, all its RPC calls included
    read_only: bool = True  # registry refuses anything else: agents never sign or send
    agent_tool: bool = True  # offered to agents that reason with a model (aggregators like data_report are not)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "category": self.category, "description": self.description,
                "input_schema": self.input_schema, "output_schema": self.output_schema, "permissions": list(self.permissions),
                "version": self.version, "developer": self.developer, "compatibility": self.compatibility,
                "requires": list(self.requires), "timeout_s": self.timeout_s, "read_only": self.read_only,
                "agent_tool": self.agent_tool}

    def tool_param(self) -> dict:
        """The tool as the model sees it (Messages API tool definition)."""
        return {"name": self.id, "description": f"{self.name}: {self.description}", "input_schema": self.input_schema}


class ToolRegistry:
    """Every tool the platform knows. Register a Skill to add a tool; nothing else needs to change."""

    def __init__(self) -> None:
        self.by_id: dict[str, Skill] = {}
        self._lock = threading.Lock()

    def register(self, skill: Skill, *, replace: bool = False) -> Skill:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", skill.id):
            raise ValueError(f"工具 id 只能用小写字母、数字和下划线：{skill.id}")
        if not skill.read_only:
            raise ValueError(f"{skill.id}：本阶段只允许只读工具，链上写操作需要单独的审批流程")
        unknown = [p for p in skill.permissions if p not in PERMISSIONS]
        if unknown:
            raise ValueError(f"{skill.id}：未知权限 {unknown}")
        if skill.input_schema.get("type") != "object":
            raise ValueError(f"{skill.id}：input_schema 必须是 object")
        with self._lock:
            if skill.id in self.by_id and not replace:
                raise ValueError(f"工具 {skill.id} 已经注册")
            self.by_id[skill.id] = skill
        return skill

    def unregister(self, sid: str) -> None:
        with self._lock:
            self.by_id.pop(sid, None)

    def get(self, sid: str) -> Skill | None:
        return self.by_id.get(sid)

    def all(self) -> list[Skill]:
        return list(self.by_id.values())


def validate(schema: dict, value: Any, path: str = "input") -> None:
    """The subset of JSON Schema the skills use: type, required, properties, pattern, enum, min/max, items."""
    t = schema.get("type")
    types = {"object": dict, "string": str, "integer": int, "array": list, "boolean": bool, "number": (int, float)}
    if t and not isinstance(value, types[t]) or (t in ("integer", "number") and isinstance(value, bool)):
        raise SkillError(f"{path} 应该是 {t}")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                raise SkillError(f"缺少参数 {path}.{k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                validate(sub, value[k], f"{path}.{k}")
    if t == "string":
        if "pattern" in schema and not re.match(schema["pattern"], value):
            raise SkillError(f"{path} 格式不对：{value[:80]}")
        if "enum" in schema and value not in schema["enum"]:
            raise SkillError(f"{path} 只能是 {', '.join(schema['enum'])}")
    if t in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            raise SkillError(f"{path} 超出范围 {schema.get('minimum')}–{schema.get('maximum')}")
    if t == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SkillError(f"{path} 最多 {schema['maxItems']} 项")
        for i, item in enumerate(value):
            validate(schema.get("items", {}), item, f"{path}[{i}]")


def _addr_input(extra: dict | None = None) -> dict:
    return {"type": "object", "required": ["address"], "properties": {"address": ADDRESS, **(extra or {})}}


# ---- implementations ----

def wallet_analysis(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    a = inp["address"]
    ctx.log(f"eth_getBalance / eth_getTransactionCount / eth_getCode {a}")
    code = r.code(a)
    out = {"address": a.lower(), "network": r.network, "block": r.block_number(), "bnb": str(r.native_balance(a)),
           "nonce": r.nonce(a), "is_contract": code not in ("0x", "0x0"), "code_size": max(0, (len(code) - 2) // 2), "tokens": {}}
    for sym, (addr, dec) in ctx.tokens.items():
        bal = r.erc20_balance(addr, a, dec)
        if bal is not None:
            out["tokens"][sym] = str(bal)
    ctx.log(f"BNB {out['bnb']} · nonce {out['nonce']} · {'合约' if out['is_contract'] else '外部账户'}")
    return out


def token_info(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    a = inp["address"]
    ctx.log(f"eth_call name/symbol/decimals/totalSupply {a}")
    info = r.erc20(a)
    if not info["is_erc20"]:
        raise SkillError(f"{a} 不是标准 ERC-20 代币（读不到 symbol/decimals/totalSupply）")
    ctx.log(f"{info['symbol']} · decimals {info['decimals']} · supply {info['total_supply']}")
    return {"address": a.lower(), "network": r.network, **info}


def _verified_source(ctx: SkillContext, address: str) -> dict | None:
    if ctx.etherscan is None:
        return None
    ctx.log(f"Etherscan getsourcecode {address}")
    data = ctx.etherscan({"module": "contract", "action": "getsourcecode", "address": address})
    rows = data.get("result") if isinstance(data, dict) else None
    if not rows or not isinstance(rows, list) or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    verified = bool(row.get("SourceCode"))
    return {"verified": verified, "contract_name": row.get("ContractName") or None, "compiler": row.get("CompilerVersion") or None,
            "license": row.get("LicenseType") or None, "proxy": row.get("Proxy") == "1", "implementation": row.get("Implementation") or None,
            "source_lines": len(str(row.get("SourceCode", "")).splitlines()) if verified else 0}


def contract_analysis(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    a = inp["address"]
    ctx.log(f"eth_getCode {a}")
    code = r.code(a)
    if code in ("0x", "0x0"):
        raise SkillError(f"{a} 上没有合约代码（是外部账户或合约已自毁）")
    impl = r.slot_address(r.storage(a, EIP1967_IMPL))
    admin = r.slot_address(r.storage(a, EIP1967_ADMIN))
    ctx.log(f"EIP-1967 实现槽 {impl or '空'} · 管理槽 {admin or '空'}")
    selectors = push4_selectors(code)
    impl_code = None
    if impl:
        impl_code = r.code(impl)
        selectors |= push4_selectors(impl_code)
    found = sorted({(sig, why) for s, (sig, why) in SELECTORS.items() if s in selectors})
    owner = r.owner(a)
    risks = [f"{why}（{sig}）" for sig, why in found]
    if impl:
        risks.append(f"可升级代理：实现合约 {impl}，升级权限在 {admin or '实现合约内部'}")
    if owner and int(owner, 16) != 0:
        risks.append(f"有 owner：{owner}")
    source = _verified_source(ctx, a)
    if source is not None and not source["verified"]:
        risks.append("源码未在区块浏览器验证")
    ctx.log(f"发现 {len(found)} 个特权函数选择器")
    return {"address": a.lower(), "network": r.network, "code_size": (len(code) - 2) // 2,
            "code_hash": "0x" + keccak(hexstr=code).hex(), "proxy": bool(impl), "implementation": impl, "proxy_admin": admin,
            "owner": owner, "privileged_functions": [{"signature": s, "meaning": w} for s, w in found],
            "selector_count": len(selectors), "source": source, "risks": risks,
            "method": "字节码 PUSH4 选择器扫描 + EIP-1967 存储槽 + owner() 调用" + ("+ 区块浏览器源码" if source else "")}


def tx_history(ctx: SkillContext, inp: dict) -> dict:
    if ctx.etherscan is None:
        raise SkillError("交易历史需要 ETHERSCAN_API_KEY（BSC 走 Etherscan V2 多链接口），部署里还没有配置")
    a = inp["address"]
    limit = int(inp.get("limit", 100))
    ctx.log(f"Etherscan txlist {a} 最近 {limit} 笔")
    data = ctx.etherscan({"module": "account", "action": "txlist", "address": a, "sort": "desc", "page": 1, "offset": limit})
    rows = data.get("result") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        msg = str(data.get("result") or data.get("message")) if isinstance(data, dict) else "无响应"
        if "No transactions" in msg:
            rows = []
        else:
            raise SkillError(f"Etherscan 返回错误：{msg[:200]}")
    rows = [x for x in rows if isinstance(x, dict)]
    al = a.lower()
    out_n = sum(1 for x in rows if str(x.get("from", "")).lower() == al)
    failed = sum(1 for x in rows if x.get("isError") == "1")
    peers = Counter((x.get("to") if str(x.get("from", "")).lower() == al else x.get("from")) or "" for x in rows)
    selectors = Counter(str(x.get("input", ""))[:10] for x in rows if str(x.get("input", "")) not in ("", "0x"))
    times = [int(x["timeStamp"]) for x in rows if str(x.get("timeStamp", "")).isdigit()]
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None  # noqa: E731
    ctx.log(f"{len(rows)} 笔，转出 {out_n}，失败 {failed}")
    return {"address": al, "count": len(rows), "outgoing": out_n, "incoming": len(rows) - out_n, "failed": failed,
            "first_seen": iso(min(times)) if times else None, "last_seen": iso(max(times)) if times else None,
            "top_counterparties": [{"address": p, "count": n} for p, n in peers.most_common(5) if p],
            "top_methods": [{"selector": s, "count": n} for s, n in selectors.most_common(5)],
            "recent": [{"hash": x.get("hash"), "block": int(x.get("blockNumber", 0) or 0), "from": x.get("from"), "to": x.get("to"),
                        "value_bnb": str(Decimal(int(x.get("value", "0") or 0)) / Decimal(10**18)), "failed": x.get("isError") == "1",
                        "time": iso(int(x["timeStamp"])) if str(x.get("timeStamp", "")).isdigit() else None} for x in rows[:10]]}


def contract_events(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    a = inp["address"]
    span = int(inp.get("blocks", 1000))
    head = r.block_number()
    start = max(0, head - span + 1)
    ctx.log(f"eth_getLogs {a} 区块 {start}–{head}")
    try:
        logs = r.logs(a, start, head)
    except RuntimeError as e:
        raise SkillError(f"RPC 拒绝了日志查询（可能区块范围太大）：{str(e)[:200]}") from e
    decoded = [r.decode_log(x) for x in logs]
    counts = Counter(d["event"] for d in decoded)
    ctx.log(f"{len(decoded)} 条日志：" + "，".join(f"{k} {v}" for k, v in counts.most_common(5)))
    return {"address": a.lower(), "network": r.network, "from_block": start, "to_block": head, "count": len(decoded),
            "by_event": dict(counts.most_common()), "recent": decoded[-20:][::-1]}


def onchain_monitor(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    head = r.block("latest")
    rows = []
    for a in inp["addresses"]:
        ctx.log(f"读取 {a}")
        rows.append({"address": a.lower(), "bnb": str(r.native_balance(a)), "nonce": r.nonce(a)})
    gas = r.gas_price()
    return {"network": r.network, "block": head["number"], "block_time": head["timestamp"], "gas_price_gwei": str(Decimal(gas) / Decimal(10**9)),
            "addresses": rows}


AGENT_TASK = "agent_task"


def _agent_task(ctx: SkillContext, inp: dict) -> dict:
    raise SkillError("自主执行由 Agent Runtime 运行，不能当普通技能直接调用")


SECTION = {"agent_task": "自主分析", "wallet_analysis": "钱包", "token_info": "代币", "contract_analysis": "合约权限", "tx_history": "交易历史",
           "contract_events": "合约事件", "onchain_monitor": "链上状态"}


def data_report(ctx: SkillContext, inp: dict) -> dict:
    """Combine what other agents handed over into one report. Deterministic: same inputs, same text, same hash."""
    title = inp.get("title") or "链上分析报告"
    lines = [f"# {title}", ""]
    findings: list[str] = []
    missing: list[str] = []
    for up in ctx.upstream:
        skill, res, who = up.get("skill"), up.get("result"), up.get("agent_name") or up.get("agent")
        head = f"## {SECTION.get(skill, skill)}（{who}）"
        if up.get("error") or res is None:
            missing.append(f"{SECTION.get(skill, skill)}：{up.get('error') or '没有结果'}")
            lines += [head, "", f"未完成：{up.get('error') or '没有结果'}", ""]
            continue
        lines += [head, ""]
        if skill == "wallet_analysis":
            lines.append(f"- BNB 余额 {res['bnb']}，nonce {res['nonce']}，{'合约地址' if res['is_contract'] else '外部账户'}（区块 {res['block']}）")
            for s, v in res.get("tokens", {}).items():
                lines.append(f"- {s} 余额 {v}")
        elif skill == "token_info":
            lines.append(f"- {res['name']}（{res['symbol']}），精度 {res['decimals']}，总量 {res['total_supply']}")
        elif skill == "contract_analysis":
            lines.append(f"- 代码 {res['code_size']} 字节，{'可升级代理' if res['proxy'] else '非代理合约'}，owner {res['owner'] or '无'}")
            if res.get("source"):
                lines.append(f"- 源码{'已验证' if res['source']['verified'] else '未验证'}" + (f"：{res['source']['contract_name']}" if res['source'].get('contract_name') else ""))
            for risk in res["risks"]:
                lines.append(f"- 风险点：{risk}")
                findings.append(risk)
        elif skill == "tx_history":
            lines.append(f"- 最近 {res['count']} 笔：转出 {res['outgoing']}，转入 {res['incoming']}，失败 {res['failed']}")
            lines.append(f"- 首次 {res['first_seen'] or '—'}，最近 {res['last_seen'] or '—'}")
            if res["failed"]:
                findings.append(f"最近交易里有 {res['failed']} 笔失败")
        elif skill == "contract_events":
            lines.append(f"- 区块 {res['from_block']}–{res['to_block']} 共 {res['count']} 条事件：" +
                         ("，".join(f"{k} {v}" for k, v in res["by_event"].items()) or "无"))
        elif skill == "agent_task":
            lines.append(str(res.get("answer") or "（没有结论）").strip())
            used = [c.get("tool") for c in res.get("tool_calls") or []]
            if used:
                lines.append(f"\n（调用了 {len(used)} 次工具：{', '.join(dict.fromkeys(used))}）")
        elif skill == "onchain_monitor":
            lines.append(f"- 区块 {res['block']}，Gas {res['gas_price_gwei']} gwei")
            for x in res["addresses"]:
                lines.append(f"- {x['address']}：BNB {x['bnb']}，nonce {x['nonce']}")
        else:
            lines.append("```json\n" + json.dumps(res, ensure_ascii=False, indent=1)[:2000] + "\n```")
        lines.append("")
    lines += ["## 结论", ""]
    lines += [f"- {f}" for f in findings] or ["- 本次读取的数据里没有发现特权函数或失败交易"]
    if missing:
        lines += ["", "## 未完成的部分", ""] + [f"- {m}" for m in missing]
    md = "\n".join(lines)
    ctx.log(f"汇总 {len(ctx.upstream)} 份结果，{len(findings)} 个风险点")
    return {"title": title, "report_md": md, "findings": findings, "incomplete": missing,
            "sha256": "0x" + hashlib.sha256(md.encode()).hexdigest()}


SKILLS: tuple[Skill, ...] = (
    Skill("wallet_analysis", "钱包分析", "Analytics", "读取地址的 BNB 余额、nonce、是否合约，以及已配置代币的余额。",
          _addr_input(), {"type": "object", "properties": {"bnb": {"type": "string"}, "nonce": {"type": "integer"}, "is_contract": {"type": "boolean"}}},
          ("chain:read",), "1.0.0", wallet_analysis),
    Skill("token_info", "代币信息", "Research", "读取 ERC-20 的名称、符号、精度和总供应量。",
          _addr_input(), {"type": "object", "properties": {"symbol": {"type": "string"}, "total_supply": {"type": "string"}}},
          ("chain:read",), "1.0.0", token_info),
    Skill("contract_analysis", "合约分析", "Research", "扫描字节码里的特权函数（增发、暂停、黑名单、升级等），识别 EIP-1967 代理和 owner；有 Etherscan key 时附带源码验证情况。",
          _addr_input(), {"type": "object", "properties": {"risks": {"type": "array"}, "proxy": {"type": "boolean"}}},
          ("chain:read",), "1.0.0", contract_analysis),
    Skill("tx_history", "交易历史", "Analytics", "通过 Etherscan V2 读取最近的交易，统计方向、失败数、主要对手方和调用方法。",
          _addr_input({"limit": {"type": "integer", "minimum": 1, "maximum": 500}}), {"type": "object", "properties": {"count": {"type": "integer"}}},
          ("chain:read", "net:etherscan"), "1.0.0", tx_history, requires=("ETHERSCAN_API_KEY",)),
    Skill("contract_events", "合约事件读取", "Monitoring", "用 eth_getLogs 读取合约最近 N 个区块的事件，解码 Transfer / Approval / OwnershipTransferred。",
          _addr_input({"blocks": {"type": "integer", "minimum": 1, "maximum": 5000}}), {"type": "object", "properties": {"count": {"type": "integer"}}},
          ("chain:read",), "1.0.0", contract_events),
    Skill("onchain_monitor", "链上监控", "Monitoring", "读取最新区块、Gas 价格和一组地址的余额与 nonce。",
          {"type": "object", "required": ["addresses"], "properties": {"addresses": {"type": "array", "items": ADDRESS, "maxItems": 20}}},
          {"type": "object", "properties": {"block": {"type": "integer"}}}, ("chain:read",), "1.0.0", onchain_monitor),
    Skill("data_report", "数据报告", "Reporting", "汇总其他员工交来的结果，生成报告和 SHA-256 哈希（可审批后上链）。",
          {"type": "object", "properties": {"title": {"type": "string"}}}, {"type": "object", "properties": {"report_md": {"type": "string"}, "sha256": {"type": "string"}}},
          ("report:write",), "1.0.0", data_report, compatibility="任意网络", agent_tool=False),
    Skill(AGENT_TASK, "自主执行（AI 推理）", "Agent",
          "把任务交给员工自己的模型：它理解任务，从自己被授权的工具里选择要调用的，读取真实结果后决定下一步，最后给出结论。",
          {"type": "object", "required": ["instruction"], "properties": {"instruction": {"type": "string"}}},
          {"type": "object", "properties": {"answer": {"type": "string"}, "tool_calls": {"type": "array"}}},
          ("model:invoke",), "1.0.0", _agent_task, compatibility="需要 Anthropic 模型", agent_tool=False, timeout_s=900),
)
REGISTRY = ToolRegistry()
for _s in SKILLS:
    REGISTRY.register(_s)
# The live table: tools registered later appear here too.
SKILL_BY_ID = REGISTRY.by_id
