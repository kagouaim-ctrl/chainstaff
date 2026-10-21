"""The AI campaign payouts clerk: turns a reward list into a checked batch for the Safe.

Who gets how much is decided by agent/rules/batch.py. This employee keeps the batches,
shows the owner what was excluded and why, and exports the batch in the formats the
Safe{Wallet} app imports. It never signs or sends: the Safe owners do.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from agent.execution.batch_export import csv_airdrop, tx_builder_json
from agent.execution.tokens import TokenInfo
from agent.rules import Policy, to_amount
from agent.rules.batch import FIXED, POOL, BatchRules, compute, parse_rows
from agent.store import Store

ROLE = "campaign_payouts"
DRAFT, PROPOSED, EXECUTED, CANCELLED = "draft", "proposed", "executed", "cancelled"
STATUS_NAMES = {DRAFT: "待老板确认", PROPOSED: "已生成 Safe 提案", EXECUTED: "已执行", CANCELLED: "已作废"}
MAX_ROWS = 5000


class PayoutError(ValueError):
    pass


def _dec(v, name: str) -> Decimal | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        d = to_amount(str(v).strip())
    except ValueError:
        raise PayoutError(f"{name}格式不对：{v}") from None
    return d


class CampaignPayouts:
    def __init__(self, store: Store, policy: Callable[[], Policy] | None = None,
                 tokens: dict[str, TokenInfo] | None = None, chain_id: int | None = None,
                 safe_address: str | None = None, explorer_tx_url: str | None = None) -> None:
        self.store = store
        self.policy = policy
        self.tokens = tokens or {}
        self.chain_id = chain_id
        self.safe_address = safe_address
        self.explorer_tx_url = explorer_tx_url

    # ---- computing ----

    def token_choices(self) -> list[str]:
        if self.tokens:
            return sorted(self.tokens)
        return sorted(self.policy().token_decimals) if self.policy else ["USDT"]

    def _decimals(self, token: str) -> int:
        if token in self.tokens:
            return self.tokens[token].decimals
        if self.policy and token in self.policy().token_decimals:
            return self.policy().token_decimals[token]
        raise PayoutError(f"不支持的币种：{token}")

    def rules_from(self, data: dict) -> BatchRules:
        token = str(data.get("token") or "").strip().upper()
        if not token:
            raise PayoutError("请选择币种")
        mode = data.get("mode") or FIXED
        try:
            return BatchRules(
                token=token,
                mode=mode,
                pool=_dec(data.get("pool"), "奖池总额"),
                budget=_dec(data.get("budget"), "预算"),
                per_person_cap=_dec(data.get("per_person_cap"), "单人上限"),
                min_amount=_dec(data.get("min_amount"), "最低发放额") or Decimal(0),
                decimals=self._decimals(token),
            )
        except PayoutError:
            raise
        except ValueError as e:
            raise PayoutError(str(e)) from None

    def paid_before(self, campaign: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for b in self.store.batches(limit=500, campaign=campaign):
            if b["status"] in (PROPOSED, EXECUTED, DRAFT):
                for i in b["items"]:
                    if i["status"] == "ok":
                        out.setdefault(i["address"], f"批次 #{b['id']}（{STATUS_NAMES[b['status']]}）")
        return out

    def sybil_exclusions(self, check_id: int | None) -> dict[str, str]:
        if not check_id:
            return {}
        c = self.store.sybil_check(int(check_id))
        if not c or not c.get("result"):
            raise PayoutError(f"反女巫检查 #{check_id} 不存在或还没跑完")
        return {a["address"]: "；".join(r["text"] for r in a["reasons"] if r["severity"] == "high")[:120] or "反女巫剔除"
                for a in c["result"]["addresses"] if a["verdict"] == "excluded"}

    def preview(self, text: str, rules: dict, campaign: str = "", sybil_check: int | None = None) -> dict:
        if not text.strip():
            raise PayoutError("名单是空的：每行一个地址和数量，例如 0xabc…,120")
        rows, problems = parse_rows(text)
        if len(rows) > MAX_ROWS:
            raise PayoutError(f"一批最多 {MAX_ROWS} 行，请拆成几批")
        r = self.rules_from(rules)
        risk = self.policy().risk_addresses if self.policy else ()
        result = compute(rows, r, risk, self.sybil_exclusions(sybil_check), self.paid_before(campaign.strip()) if campaign.strip() else {})
        summary = result.summary()
        summary["parse_problems"] = problems
        summary["token"] = r.token
        return {"items": [i.to_dict() for i in result.items], "summary": summary, "ok": result.ok and not problems,
                "rules": self._rules_dict(r, sybil_check)}

    @staticmethod
    def _rules_dict(r: BatchRules, sybil_check: int | None) -> dict:
        s = lambda d: None if d is None else f"{d.normalize():f}"  # noqa: E731
        return {"token": r.token, "mode": r.mode, "pool": s(r.pool), "budget": s(r.budget),
                "per_person_cap": s(r.per_person_cap), "min_amount": s(r.min_amount), "sybil_check": sybil_check}

    # ---- lifecycle ----

    def create(self, campaign: str, text: str, rules: dict, by: str, sybil_check: int | None = None) -> dict:
        campaign = campaign.strip()
        if not campaign:
            raise PayoutError("请填活动名称，同一个活动里同一个地址只发一次")
        p = self.preview(text, rules, campaign, sybil_check)
        if p["summary"]["parse_problems"]:
            raise PayoutError("名单有格式问题：" + "；".join(p["summary"]["parse_problems"][:3]))
        if p["summary"]["errors"]:
            raise PayoutError("；".join(p["summary"]["errors"]))
        bid = self.store.add_batch(campaign, p["rules"]["token"], p["rules"], p["items"], p["summary"], text, by)
        s = p["summary"]
        self.store.log_event("audit", f"生成结算批次 #{bid}「{campaign}」：{s['payable']} 个地址，合计 {s['total']} {s['token']}，剔除 {s['excluded']} 个",
                             by, {"batch": bid}, employee=ROLE)
        return self.store.batch(bid)

    def get(self, bid: int) -> dict:
        b = self.store.batch(bid)
        if not b:
            raise PayoutError(f"没有批次 #{bid}")
        return b

    def propose(self, bid: int, by: str) -> dict:
        b = self.get(bid)
        if b["status"] != DRAFT:
            raise PayoutError(f"批次 #{bid} 现在是「{STATUS_NAMES[b['status']]}」，不能再确认")
        self.store.set_batch_status(bid, (DRAFT,), PROPOSED, by)
        self.store.log_event("audit", f"老板确认结算批次 #{bid}，生成 Safe 批量提案（{b['summary']['payable']} 笔，{b['summary']['total']} {b['token']}）",
                             by, {"batch": bid}, employee=ROLE)
        return self.get(bid)

    def mark_executed(self, bid: int, tx_hash: str, by: str) -> dict:
        tx_hash = tx_hash.strip()
        if tx_hash and not (tx_hash.startswith("0x") and len(tx_hash) == 66):
            raise PayoutError("交易哈希格式不对（0x 开头，66 位）")
        if not self.store.set_batch_status(bid, (PROPOSED,), EXECUTED, by, tx_hash or None):
            raise PayoutError(f"批次 #{bid} 要先确认生成提案，才能标记已执行")
        self.store.log_event("audit", f"结算批次 #{bid} 已在 Safe 执行" + (f"：{tx_hash}" if tx_hash else ""), by, {"batch": bid}, employee=ROLE)
        return self.get(bid)

    def cancel(self, bid: int, by: str, note: str = "") -> dict:
        if not self.store.set_batch_status(bid, (DRAFT, PROPOSED), CANCELLED, by, note=note[:300] or None):
            raise PayoutError(f"批次 #{bid} 已执行或已作废，不能作废")
        self.store.log_event("audit", f"作废结算批次 #{bid}" + (f"：{note[:100]}" if note else ""), by, {"batch": bid}, employee=ROLE)
        return self.get(bid)

    # ---- export ----

    def export(self, bid: int, fmt: str) -> tuple[str, str, str]:
        """(filename, media type, content). Only batches the owner has confirmed can be exported."""
        b = self.get(bid)
        if b["status"] not in (PROPOSED, EXECUTED):
            raise PayoutError("老板确认后才能导出提案文件")
        items = [(i["address"], Decimal(i["amount"])) for i in b["items"] if i["status"] == "ok"]
        if fmt == "list":
            lines = ["address,amount"] + [f"{a},{amt.normalize():f}" for a, amt in items]
            return f"batch-{bid}.csv", "text/csv", "\n".join(lines) + "\n"
        token = self.tokens.get(b["token"])
        if token is None or (not token.is_native and not token.address):
            raise PayoutError(f"没有配置 {b['token']} 的合约地址（CHAINSTAFF_CHAIN），只能导出名单 CSV")
        if fmt == "safe_json":
            if not self.chain_id:  # pragma: no cover - tokens come from the chain config
                raise PayoutError("没有配置链")
            desc = f"{b['campaign']} · {len(items)} 笔 · {b['summary']['total']} {b['token']} · ChainStaff 批次 #{bid}"
            return f"safe-batch-{bid}.json", "application/json", tx_builder_json(self.chain_id, self.safe_address, token, items, f"{b['campaign']} #{bid}", desc)
        if fmt == "csv_airdrop":
            return f"safe-csv-airdrop-{bid}.csv", "text/csv", csv_airdrop(token, items)
        raise PayoutError(f"不支持的导出格式：{fmt}")

    # ---- views ----

    def overview(self) -> dict:
        batches = self.store.batches(limit=100)
        totals: dict[str, Decimal] = {}
        for b in batches:
            if b["status"] in (PROPOSED, EXECUTED):
                totals[b["token"]] = totals.get(b["token"], Decimal(0)) + Decimal(b["summary"]["total"])
        return {
            "batches": [{k: v for k, v in b.items() if k not in ("items", "source")} for b in batches],
            "pending": sum(1 for b in batches if b["status"] == DRAFT),
            "awaiting_signature": sum(1 for b in batches if b["status"] == PROPOSED),
            "paid_totals": {k: f"{v.normalize():f}" for k, v in totals.items()},
            "tokens": self.token_choices(),
            "safe": self.safe_address,
            "exportable": sorted(s for s, t in self.tokens.items() if t.is_native or t.address),
            "modes": [FIXED, POOL],
        }

    def payouts_text(self) -> str:
        batches = self.store.batches(limit=5)
        if not batches:
            return "还没有结算批次。老板在控制台「营销结算」里粘贴名单就能生成。"
        lines = ["最近的结算批次："]
        for b in batches:
            s = b["summary"]
            lines.append(f"#{b['id']} {b['campaign']} · {s['payable']} 个地址 · {s['total']} {b['token']} · {STATUS_NAMES[b['status']]}")
        return "\n".join(lines)
