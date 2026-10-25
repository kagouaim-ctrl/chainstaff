"""More read-only BSC tools, registered into the same REGISTRY as the built-in skills.

block_info, tx_lookup, contract_read, event_search, wallet_activity and chain_events. Agents call them as
tools; missions can run them as subtasks. All of them read (eth_getBlock*, eth_getTransaction*,
eth_call, eth_getLogs, or the local event store); none can sign or send.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from .chain import TRANSFER_TOPIC, _hex_int
from .skills import ADDRESS, REGISTRY, SECTION, Skill, SkillContext, SkillError, _addr_input

TX_HASH = {"type": "string", "pattern": "^0x[0-9a-fA-F]{64}$"}
MAX_LOG_RANGE = 5000
SIG_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(([^()]*)\)$")
ABI_TYPE_RE = re.compile(r"^(address|bool|string|bytes([1-9]|[12][0-9]|3[0-2])?|u?int(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)?)(\[\])?$")


def _types(spec: str) -> list[str]:
    out = [t.strip() for t in spec.split(",") if t.strip()]
    for t in out:
        if not ABI_TYPE_RE.match(t):
            raise SkillError(f"不支持的 ABI 类型：{t}（支持 address/bool/string/bytes/bytesN/intN/uintN 及其数组）")
    return [re.sub(r"^(u?int)(\[\])?$", r"\g<1>256\2", t) for t in out]


def _jsonable(v):
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v) if abs(v) > 2**53 else v
    return v


def _coerce(t: str, v):
    if t.endswith("[]"):
        if not isinstance(v, list):
            raise SkillError(f"{t} 参数应该是数组")
        return [_coerce(t[:-2], x) for x in v]
    if t == "address":
        if not isinstance(v, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", v):
            raise SkillError(f"地址参数格式不对：{v}")
        return to_checksum_address(v)
    if t.startswith(("uint", "int")):
        try:
            return int(v, 0) if isinstance(v, str) else int(v)
        except (TypeError, ValueError):
            raise SkillError(f"{t} 参数应该是整数：{v}") from None
    if t == "bool":
        return v in (True, "true", "1", 1)
    if t.startswith("bytes"):
        if not isinstance(v, str) or not v.startswith("0x"):
            raise SkillError(f"{t} 参数应该是 0x 开头的十六进制")
        return bytes.fromhex(v[2:])
    return str(v)


# ---- implementations ----

def block_info(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    tag = inp.get("block", "latest")
    ctx.log(f"eth_getBlockByNumber {tag}")
    raw = r._call("eth_getBlockByNumber", [hex(tag) if isinstance(tag, int) else tag, False])
    if not raw:
        raise SkillError(f"节点上没有区块 {tag}")
    out = {"network": r.network, "number": _hex_int(raw.get("number")), "hash": raw.get("hash"), "parent_hash": raw.get("parentHash"),
           "timestamp": _hex_int(raw.get("timestamp")), "miner": raw.get("miner"), "tx_count": len(raw.get("transactions") or []),
           "gas_used": _hex_int(raw.get("gasUsed")), "gas_limit": _hex_int(raw.get("gasLimit")), "base_fee_wei": _hex_int(raw.get("baseFeePerGas")),
           "explorer": r.explorer("block", str(_hex_int(raw.get("number"))))}
    ctx.log(f"区块 {out['number']} · {out['tx_count']} 笔交易")
    return out


def tx_lookup(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    h = inp["hash"]
    ctx.log(f"eth_getTransactionByHash / eth_getTransactionReceipt {h}")
    tx = r.transaction(h)
    if not tx:
        raise SkillError(f"节点上找不到交易 {h}（可能还没广播，或不在这个网络）")
    rc = r.receipt(h)
    value = _hex_int(tx.get("value")) or 0
    out = {"hash": h.lower(), "network": r.network, "from": tx.get("from"), "to": tx.get("to"), "nonce": _hex_int(tx.get("nonce")),
           "value_bnb": str(Decimal(value) / Decimal(10**18)), "method": (tx.get("input") or "0x")[:10] if (tx.get("input") or "0x") != "0x" else None,
           "block": _hex_int(tx.get("blockNumber")), "status": "pending", "explorer": r.explorer("tx", h)}
    if rc:
        gas_used = _hex_int(rc.get("gasUsed")) or 0
        price = _hex_int(rc.get("effectiveGasPrice")) or _hex_int(tx.get("gasPrice")) or 0
        logs = [r.decode_log(x) for x in rc.get("logs") or []]
        out.update(status="success" if _hex_int(rc.get("status")) == 1 else "failed", gas_used=gas_used,
                   fee_bnb=str(Decimal(gas_used * price) / Decimal(10**18)), contract_created=rc.get("contractAddress"),
                   log_count=len(logs), events=logs[:30])
        try:
            head = r.block_number()
            out["confirmations"] = max(0, head - out["block"] + 1) if out["block"] is not None else 0
        except Exception:  # noqa: BLE001 - confirmations are a nicety
            pass
    ctx.log(f"{out['status']} · 区块 {out['block']}")
    return out


def contract_read(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    m = SIG_RE.match(inp["signature"].replace(" ", ""))
    if not m:
        raise SkillError("函数签名格式应该像 balanceOf(address)")
    arg_types = _types(m.group(2))
    ret_types = _types(",".join(inp.get("returns") or []))
    args = inp.get("args") or []
    if len(args) != len(arg_types):
        raise SkillError(f"{m.group(1)} 需要 {len(arg_types)} 个参数，给了 {len(args)} 个")
    sig = f"{m.group(1)}({','.join(arg_types)})"
    data = "0x" + (keccak(text=sig)[:4] + encode(arg_types, [_coerce(t, v) for t, v in zip(arg_types, args)])).hex()
    tag = inp.get("block", "latest")
    ctx.log(f"eth_call {inp['address']} {sig} @ {tag}")
    try:
        raw = r._call("eth_call", [{"to": to_checksum_address(inp["address"]), "data": data}, hex(tag) if isinstance(tag, int) else tag])
    except RuntimeError as e:
        raise SkillError(f"调用被拒绝或回滚：{str(e)[:200]}") from e
    out = {"address": inp["address"].lower(), "network": r.network, "function": sig, "block": tag, "raw": raw}
    if ret_types and raw and raw != "0x":
        try:
            out["decoded"] = _jsonable(list(decode(ret_types, bytes.fromhex(raw[2:]))))
        except Exception as e:  # noqa: BLE001 - wrong return types: still show the raw answer
            out["decode_error"] = f"按 {ret_types} 解码失败：{str(e)[:120]}"
    return out


def _range(r, inp: dict) -> tuple[int, int]:
    head = r.block_number()
    to = min(int(inp.get("to_block", head)), head)
    start = int(inp.get("from_block", to - int(inp.get("blocks", 1000)) + 1))
    start = max(0, start)
    if to < start:
        raise SkillError("区块范围不对：from_block 大于 to_block")
    if to - start + 1 > MAX_LOG_RANGE:
        raise SkillError(f"一次最多查 {MAX_LOG_RANGE} 个区块")
    return start, to


def event_search(ctx: SkillContext, inp: dict) -> dict:
    r = ctx.need_chain()
    start, to = _range(r, inp)
    topics: list | None = None
    if inp.get("event"):
        sig = inp["event"].replace(" ", "")
        if not SIG_RE.match(sig):
            raise SkillError("事件签名格式应该像 Transfer(address,address,uint256)")
        topics = ["0x" + keccak(text=sig).hex()]
    ctx.log(f"eth_getLogs {inp['address']} {inp.get('event') or '全部事件'} 区块 {start}–{to}")
    try:
        logs = r.logs(inp["address"], start, to, topics)
    except RuntimeError as e:
        raise SkillError(f"RPC 拒绝了日志查询（可能区块范围太大）：{str(e)[:200]}") from e
    decoded = [r.decode_log(x) for x in logs]
    counts = Counter(d["event"] for d in decoded)
    return {"address": inp["address"].lower(), "network": r.network, "event": inp.get("event"), "from_block": start, "to_block": to,
            "count": len(decoded), "by_event": dict(counts.most_common()), "recent": decoded[-int(inp.get("limit", 20)):][::-1]}


def wallet_activity(ctx: SkillContext, inp: dict) -> dict:
    """Token transfers in and out of an address over recent blocks, straight from eth_getLogs (no explorer key needed)."""
    r = ctx.need_chain()
    a = inp["address"].lower()
    start, to = _range(r, inp)
    pad = "0x" + a[2:].rjust(64, "0")
    ctx.log(f"eth_getLogs Transfer 转出/转入 {a} 区块 {start}–{to}")
    rows = []
    for direction, topics in (("out", [TRANSFER_TOPIC, pad]), ("in", [TRANSFER_TOPIC, None, pad])):
        flt = {"fromBlock": hex(start), "toBlock": hex(to), "topics": topics}
        try:
            logs = r._call("eth_getLogs", [flt]) or []
        except RuntimeError as e:
            raise SkillError(f"RPC 拒绝了日志查询（可能区块范围太大）：{str(e)[:200]}") from e
        for x in logs:
            t = x.get("topics") or []
            if len(t) != 3:
                continue
            rows.append({"direction": direction, "token": str(x.get("address", "")).lower(), "block": _hex_int(x.get("blockNumber")),
                         "tx_hash": x.get("transactionHash"), "from": "0x" + t[1][-40:], "to": "0x" + t[2][-40:],
                         "value_raw": str(int(x.get("data") or "0x0", 16) if (x.get("data") or "0x") != "0x" else 0)})
    rows.sort(key=lambda x: (x["block"] or 0), reverse=True)
    tokens = Counter(x["token"] for x in rows)
    peers = Counter(x["to"] if x["direction"] == "out" else x["from"] for x in rows)
    out = {"address": a, "network": r.network, "from_block": start, "to_block": to, "transfers": len(rows),
           "outgoing": sum(1 for x in rows if x["direction"] == "out"), "incoming": sum(1 for x in rows if x["direction"] == "in"),
           "tokens": [{"token": k, "count": v} for k, v in tokens.most_common(10)],
           "top_counterparties": [{"address": k, "count": v} for k, v in peers.most_common(5)], "recent": rows[:20]}
    if ctx.events is not None:
        out["monitored_events"] = [{k: e[k] for k in ("id", "kind", "block", "tx_hash", "status", "data")}
                                   for e in ctx.events.events(address=a, limit=20)]
    ctx.log(f"{len(rows)} 笔代币转账：转出 {out['outgoing']}，转入 {out['incoming']}")
    return out


def chain_events(ctx: SkillContext, inp: dict) -> dict:
    if ctx.events is None:
        raise SkillError("实时链上监听没有运行（需要配置链和 BSC 节点），没有可查的监控事件")
    rows = ctx.events.events(monitor_id=inp.get("monitor_id"), address=inp.get("address"), status=inp.get("status"),
                             limit=int(inp.get("limit", 50)))
    ctx.log(f"本地事件库 {len(rows)} 条")
    return {"count": len(rows), "events": [{k: e[k] for k in ("id", "monitor_id", "kind", "block", "tx_hash", "status", "address", "data", "created_at")}
                                           for e in rows]}


OBJ = {"type": "object"}
TOOLS: tuple[Skill, ...] = (
    Skill("block_info", "区块信息", "Monitoring", "读取一个区块（默认最新）的高度、哈希、时间、交易数、Gas 用量和基础费。",
          {"type": "object", "properties": {"block": {"type": "integer", "minimum": 0}}}, OBJ, ("chain:read",), "1.0.0", block_info, timeout_s=15),
    Skill("tx_lookup", "交易查询", "Analytics", "按哈希查询一笔交易：发送方、接收方、金额、调用方法、执行状态、手续费、确认数和解码后的事件。",
          {"type": "object", "required": ["hash"], "properties": {"hash": TX_HASH}}, OBJ, ("chain:read",), "1.0.0", tx_lookup, timeout_s=15),
    Skill("contract_read", "合约只读调用", "Research",
          "用 eth_call 调用合约的只读函数（如 balanceOf(address)、owner()），按给定返回类型解码。不会发送交易，也不会改变链上状态。",
          {"type": "object", "required": ["address", "signature"], "properties": {
              "address": ADDRESS, "signature": {"type": "string"}, "args": {"type": "array", "maxItems": 10},
              "returns": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "block": {"type": "integer", "minimum": 0}}},
          OBJ, ("chain:read",), "1.0.0", contract_read, timeout_s=15),
    Skill("event_search", "事件检索", "Monitoring", "按合约地址、事件签名（可选）和区块范围检索日志（一次最多 5000 个区块），解码常见事件。",
          _addr_input({"event": {"type": "string"}, "from_block": {"type": "integer", "minimum": 0}, "to_block": {"type": "integer", "minimum": 0},
                       "blocks": {"type": "integer", "minimum": 1, "maximum": MAX_LOG_RANGE}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
          OBJ, ("chain:read",), "1.0.0", event_search),
    Skill("wallet_activity", "钱包动态", "Analytics", "读取地址最近 N 个区块里的代币转入转出（直接查链上日志，不需要浏览器 API key），附带实时监控记录。",
          _addr_input({"blocks": {"type": "integer", "minimum": 1, "maximum": MAX_LOG_RANGE}}), OBJ, ("chain:read",), "1.0.0", wallet_activity),
    Skill("chain_events", "监控事件查询", "Monitoring", "查询实时监听记录下来的链上事件（按监控、地址或状态筛选），包括待确认、已确认和被重组移除的。",
          {"type": "object", "properties": {"monitor_id": {"type": "string"}, "address": ADDRESS,
                                            "status": {"type": "string", "enum": ["pending", "confirmed", "reorged"]},
                                            "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
          OBJ, ("chain:read",), "1.0.0", chain_events, compatibility="需要实时监听在运行", timeout_s=10),
)

for _t in TOOLS:
    if REGISTRY.get(_t.id) is None:
        REGISTRY.register(_t)
SECTION.update({"block_info": "区块", "tx_lookup": "交易", "contract_read": "合约读取", "event_search": "事件检索",
                "wallet_activity": "钱包动态", "chain_events": "监控事件"})
