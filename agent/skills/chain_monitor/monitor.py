"""The AI chain monitor: watches the treasury and other key addresses and raises alerts.

Read only. It never signs anything; it reads the explorer API and the RPC node, decides
with fixed rules, posts each alert once (even across restarts) and writes a daily brief.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from agent.rules import normalize_address, to_amount
from agent.rules.models import ADDRESS_RE
from agent.skills.finance_ops.alerts import Alert, Approval, Transfer, check_transfers
from agent.store import Store

ROLE = "chain_monitor"
HIGH, MEDIUM = "high", "medium"
SEVERITY = {
    "LARGE_OUTFLOW": HIGH,
    "ADDRESS_POISONING": HIGH,
    "UNLIMITED_APPROVAL": HIGH,
    "NFT_APPROVAL_ALL": HIGH,
    "UNKNOWN_COUNTERPARTY": MEDIUM,
    "APPROVAL_UNKNOWN_SPENDER": MEDIUM,
    "LOW_BALANCE": MEDIUM,
}
KIND_NAMES = {
    "LARGE_OUTFLOW": "大额转出",
    "UNKNOWN_COUNTERPARTY": "陌生对手方",
    "ADDRESS_POISONING": "地址投毒",
    "UNLIMITED_APPROVAL": "无限授权",
    "NFT_APPROVAL_ALL": "NFT 全部授权",
    "APPROVAL_UNKNOWN_SPENDER": "授权给陌生合约",
    "LOW_BALANCE": "余额过低",
}
MAX_LOOKBACK = timedelta(hours=24)


class MonitorSource(Protocol):
    def transfers(self, address: str, since: datetime) -> list[Transfer]: ...
    def approvals(self, address: str, since: datetime) -> list[Approval]: ...


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def _amounts(data, name: str) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} 必须是 代币: 数量")
    out = {}
    for token, v in data.items():
        try:
            amount = to_amount(str(v).strip())
        except ValueError:
            raise ValueError(f"{name} 里 {token} 的数量格式不对：{v}") from None
        if not amount.is_finite() or amount < 0:
            raise ValueError(f"{name} 里 {token} 不能是负数")
        out[str(token).strip().upper()] = str(amount)
    return out


def _addresses(data, name: str) -> list[dict]:
    if not isinstance(data, list):
        raise ValueError(f"{name} 必须是列表")
    out, seen = [], set()
    for item in data:
        item = item if isinstance(item, dict) else {"address": item}
        a = str(item.get("address", "")).strip()
        if not ADDRESS_RE.match(a):
            raise ValueError(f"{name} 里的地址格式不对：{a or '（空）'}")
        if a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append({"address": a, "label": str(item.get("label", "")).strip()[:40]})
    return out


@dataclass
class MonitorSettings:
    watch: list[dict] = field(default_factory=list)  # [{"address", "label"}]; empty = the treasury addresses from the chain config
    thresholds: dict[str, str] = field(default_factory=dict)  # large outflow line per token; empty = chain config
    low_balance: dict[str, str] = field(default_factory=dict)  # alert when a watched address drops below
    known_spenders: list[dict] = field(default_factory=list)  # contracts you approve on purpose (DEX routers, Safe modules)
    alert_unknown_counterparty: bool = True
    alert_poisoning: bool = True
    alert_approvals: bool = True
    daily_brief: bool = True
    brief_hour: int = 9

    KEY = "monitor_settings"

    @classmethod
    def load(cls, store: Store) -> "MonitorSettings":
        raw = store.get_setting(cls.KEY)
        if not raw:
            return cls()
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def validate(cls, data: dict) -> "MonitorSettings":
        s = cls()
        if "watch" in data:
            s.watch = _addresses(data["watch"], "盯盘地址")
            if len(s.watch) > 50:
                raise ValueError("最多盯 50 个地址")
        if "known_spenders" in data:
            s.known_spenders = _addresses(data["known_spenders"], "可信授权合约")
        if "thresholds" in data:
            s.thresholds = _amounts(data["thresholds"], "大额预警线")
        if "low_balance" in data:
            s.low_balance = _amounts(data["low_balance"], "余额下限")
        for name in ("alert_unknown_counterparty", "alert_poisoning", "alert_approvals", "daily_brief"):
            if name in data:
                if not isinstance(data[name], bool):
                    raise ValueError(f"{name} 必须是开或关")
                setattr(s, name, data[name])
        if "brief_hour" in data:
            v = data["brief_hour"]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 23:
                raise ValueError("brief_hour 要在 0 到 23 之间")
            s.brief_hour = v
        return s

    def save(self, store: Store) -> None:
        store.set_setting(self.KEY, json.dumps(asdict(self), ensure_ascii=False))


def check_approvals(approvals: Iterable[Approval], known_spenders: Iterable[str], labels: dict[str, str], tokens: dict[str, str]) -> list[Alert]:
    known = {normalize_address(a) for a in known_spenders}
    out = []
    for a in approvals:
        who = labels.get(normalize_address(a.owner)) or _short(a.owner)
        what = tokens.get(normalize_address(a.contract)) or _short(a.contract)
        if a.spender in known:
            continue
        if a.amount is None:
            out.append(Alert("NFT_APPROVAL_ALL", a.tx_hash, f"🔓 {who} 把合约 {what} 里的全部 NFT 授权给了 {_short(a.spender)}。如果不是你们自己操作的，立刻撤销授权。"))
        elif a.unlimited:
            out.append(Alert("UNLIMITED_APPROVAL", a.tx_hash, f"🔓 {who} 给 {_short(a.spender)} 开了 {what} 的无限授权。对方可以随时转走这个代币，不是你们自己操作的话立刻撤销。"))
        else:
            out.append(Alert("APPROVAL_UNKNOWN_SPENDER", a.tx_hash, f"🔑 {who} 授权陌生合约 {_short(a.spender)} 动用 {what}。确认是你们自己操作的。"))
    return out


def check_low_balance(balances: dict[str, dict[str, Decimal]], floors: dict[str, Decimal], labels: dict[str, str], day: str) -> list[Alert]:
    out = []
    for addr, by_token in balances.items():
        for token, floor in floors.items():
            have = by_token.get(token)
            if have is not None and have < floor:
                who = labels.get(normalize_address(addr)) or _short(addr)
                out.append(Alert("LOW_BALANCE", f"{normalize_address(addr)}:{token}:{day}",
                                 f"🪫 {who} 的 {token} 只剩 {have.normalize():f}，低于下限 {floor.normalize():f}。"))
    return out


class ChainMonitor:
    def __init__(
        self,
        store: Store,
        source: MonitorSource | None = None,
        balances: Callable[[str], dict[str, Decimal]] | None = None,
        known: Callable[[], Iterable[str]] = lambda: (),
        default_watch: Iterable[str] = (),
        default_thresholds: dict[str, Decimal] | None = None,
        tokens: dict[str, str] | None = None,  # contract address -> symbol, for readable approval alerts
        explorer_tx_url: str | None = None,
        timezone_name: str = "UTC",
    ) -> None:
        self.store = store
        self.source = source
        self.balances = balances
        self.known = known
        self.default_watch = list(default_watch)
        self.default_thresholds = default_thresholds or {}
        self.tokens = {normalize_address(k): v for k, v in (tokens or {}).items()}
        self.explorer_tx_url = explorer_tx_url
        self.tz = timezone_name

    @property
    def settings(self) -> MonitorSettings:
        return MonitorSettings.load(self.store)

    def watch(self) -> list[dict]:
        s = self.settings
        if s.watch:
            return s.watch
        return [{"address": a, "label": f"国库 {i}" if len(self.default_watch) > 1 else "国库"} for i, a in enumerate(self.default_watch, 1)]

    def thresholds(self) -> dict[str, Decimal]:
        s = self.settings
        return {k: Decimal(v) for k, v in s.thresholds.items()} if s.thresholds else dict(self.default_thresholds)

    @property
    def ready(self) -> bool:
        return self.source is not None and bool(self.watch())

    def run_once(self, now: datetime | None = None) -> list[dict]:
        """Check every watched address once. Returns the alerts that are new (already logged)."""
        now = now or datetime.now(timezone.utc)
        s = self.settings
        watch = self.watch()
        labels = {normalize_address(w["address"]): w["label"] or _short(w["address"]) for w in watch}
        addrs = [w["address"] for w in watch]
        last = self.store.get_setting("monitor:last_check")
        since = max(datetime.fromisoformat(last), now - MAX_LOOKBACK) if last else now - timedelta(hours=1)
        found: list[Alert] = []
        status = {"at": now.isoformat(), "ok": True, "error": None, "transfers": 0, "approvals": 0, "addresses": len(addrs)}
        try:
            if self.source is not None:
                transfers, approvals = [], []
                for a in addrs:
                    transfers += self.source.transfers(a, since)
                    if s.alert_approvals:
                        approvals += self.source.approvals(a, since)
                status["transfers"], status["approvals"] = len(transfers), len(approvals)
                known = list(self.known()) + [k["address"] for k in s.known_spenders]
                for alert in check_transfers(transfers, addrs, known, self.thresholds()):
                    if alert.kind == "UNKNOWN_COUNTERPARTY" and not s.alert_unknown_counterparty:
                        continue
                    if alert.kind == "ADDRESS_POISONING" and not s.alert_poisoning:
                        continue
                    found.append(alert)
                found += check_approvals(approvals, [k["address"] for k in s.known_spenders], labels, self.tokens)
            if self.balances is not None and s.low_balance:
                floors = {k: Decimal(v) for k, v in s.low_balance.items()}
                day = now.astimezone(ZoneInfo(self.tz)).date().isoformat()
                found += check_low_balance({a: self.balances(a) for a in addrs}, floors, labels, day)
        except Exception as e:  # noqa: BLE001 - keep the error for the console instead of losing it
            status.update(ok=False, error=str(e) or type(e).__name__)
            self.store.set_setting("monitor:last_run", json.dumps(status, ensure_ascii=False))
            return []
        self.store.set_setting("monitor:last_check", now.isoformat())
        new = []
        for alert in found:
            key = "alert:" + alert.key
            if self.store.get_setting(key) is not None:
                continue  # each alert once, even across restarts
            self.store.set_setting(key, now.isoformat())
            tx = alert.tx_hash if alert.tx_hash.startswith("0x") and len(alert.tx_hash) == 66 else None
            data = {"key": alert.key, "kind": alert.kind, "severity": SEVERITY.get(alert.kind, MEDIUM), "tx_hash": tx,
                    "url": f"{self.explorer_tx_url}{tx}" if tx and self.explorer_tx_url else None}
            self.store.log_event("alert", alert.text, "chain_monitor", data, at=now, employee=ROLE)
            new.append({"text": alert.text, **data})
        status["new_alerts"] = len(new)
        self.store.set_setting("monitor:last_run", json.dumps(status, ensure_ascii=False))
        return new

    def last_run(self) -> dict | None:
        raw = self.store.get_setting("monitor:last_run")
        return json.loads(raw) if raw else None

    def alerts(self, limit: int = 100, since: datetime | None = None) -> list[dict]:
        return self.store.events(("alert",), limit=limit, employee=ROLE, since=since)

    def stats(self, now: datetime | None = None, days: int = 7) -> dict:
        now = now or datetime.now(timezone.utc)
        by_kind: dict[str, int] = {}
        high = 0
        for e in self.alerts(5000, now - timedelta(days=days)):
            d = e["data"] or {}
            by_kind[d.get("kind", "OTHER")] = by_kind.get(d.get("kind", "OTHER"), 0) + 1
            high += d.get("severity") == HIGH
        return {"days": days, "total": sum(by_kind.values()), "high": high, "by_kind": by_kind}

    # ---- chat ----

    def watch_text(self) -> str:
        w = self.watch()
        if not w:
            return "还没有设置要盯的地址。"
        lines = [f"正在盯 {len(w)} 个地址："] + [f"· {x['label'] or '未命名'} {_short(x['address'])}" for x in w]
        t = self.thresholds()
        if t:
            lines.append("大额转出预警线：" + "，".join(f"{v.normalize():f} {k}" for k, v in sorted(t.items())))
        return "\n".join(lines)

    def alerts_text(self, limit: int = 5) -> str:
        items = self.alerts(limit)
        if not items:
            return "最近没有链上预警。"
        return "最近的链上预警：\n" + "\n".join(f"{e['at'][5:16].replace('T', ' ')} {e['text']}" for e in items)

    def brief_text(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        st = self.stats(now, days=1)
        lines = ["📡 链上简报（过去 24 小时）", f"盯盘地址 {len(self.watch())} 个"]
        if st["total"]:
            lines.append(f"预警 {st['total']} 条，其中高危 {st['high']} 条：" + "，".join(f"{KIND_NAMES.get(k, k)} {n}" for k, n in st["by_kind"].items()))
        else:
            lines.append("没有预警，一切正常。")
        if self.balances is not None:
            for w in self.watch():
                try:
                    b = self.balances(w["address"])
                except Exception:  # noqa: BLE001
                    lines.append(f"{w['label'] or _short(w['address'])}：余额读取失败")
                    continue
                lines.append(f"{w['label'] or _short(w['address'])}：" + "，".join(f"{v.normalize():f} {k}" for k, v in sorted(b.items())))
        run = self.last_run()
        if run and not run["ok"]:
            lines.append(f"⚠️ 最近一次检查失败：{run['error']}")
        return "\n".join(lines)

    def due_brief(self, now: datetime) -> str | None:
        s = self.settings
        local = now.astimezone(ZoneInfo(self.tz))
        if not s.daily_brief or local.hour < s.brief_hour:
            return None
        key = f"sent:monitor_brief:{local.date()}"
        if self.store.get_setting(key) is not None:
            return None
        self.store.set_setting(key, now.isoformat())
        return self.brief_text(now)
