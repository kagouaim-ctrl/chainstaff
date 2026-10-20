"""Loading and saving the owner's rules (policy JSON) and the chain settings."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from agent.execution import TokenInfo
from agent.rules import Payee, Policy, to_amount
from agent.rules.models import ADDRESS_RE


def policy_from_dict(cfg: dict) -> tuple[Policy, frozenset[str]]:
    """Build and validate a Policy. Raises ValueError with a readable message on bad input."""
    try:
        payees = []
        for p in cfg["payees"]:
            if not ADDRESS_RE.match(str(p["address"]).strip()):
                raise ValueError(f"收款方 {p.get('name')} 的地址格式不对：{p['address']}")
            payees.append(Payee(p["address"].strip(), p["name"], tuple(p.get("aliases", ())), bool(p.get("needs_approval", False))))
        for key in ("single_limit", "daily_limit"):
            for token, v in cfg[key].items():
                try:
                    amount = to_amount(str(v))
                except ValueError:
                    raise ValueError(f"{key} 里 {token} 的额度格式不对：{v}") from None
                if not amount.is_finite() or amount < 0:
                    raise ValueError(f"{key} 里 {token} 的额度不能是负数")
        for a in cfg.get("risk_addresses", ()):
            if not ADDRESS_RE.match(str(a).strip()):
                raise ValueError(f"风险地址格式不对：{a}")
        policy = Policy.build(
            payees,
            single_limit={t: str(v) for t, v in cfg["single_limit"].items()},
            daily_limit={t: str(v) for t, v in cfg["daily_limit"].items()},
            approval_categories=frozenset(cfg.get("approval_categories", ())),
            risk_addresses=cfg.get("risk_addresses", ()),
            duplicate_window=timedelta(days=int(cfg.get("duplicate_window_days", 30))),
            timezone=cfg.get("timezone", "UTC"),
            read_only=bool(cfg.get("read_only", False)),
        )
    except KeyError as e:
        raise ValueError(f"规则缺少字段：{e.args[0]}") from e
    except (TypeError, ArithmeticError) as e:
        raise ValueError(f"规则格式不对：{e}") from e
    owners = frozenset(str(o).lstrip("@").lower() for o in cfg.get("owners", ()))
    return policy, owners


def load_policy(path: str | Path) -> tuple[Policy, frozenset[str]]:
    return policy_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class PolicyFile:
    """The rules file shared by the bot and the console. Reloads when the file changes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._cached: tuple[Policy, frozenset[str]] | None = None

    def raw(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def load(self) -> tuple[Policy, frozenset[str]]:
        with self._lock:
            mtime = self.path.stat().st_mtime_ns
            if self._cached is None or mtime != self._mtime:
                self._cached = load_policy(self.path)
                self._mtime = mtime
            return self._cached

    def save(self, cfg: dict) -> tuple[Policy, frozenset[str]]:
        result = policy_from_dict(cfg)  # validate before touching the file
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)  # atomic: readers see the old or the new file, never half
            self._cached = None
        return result


@dataclass(frozen=True)
class ChainConfig:
    network: str
    chain_id: int
    rpc_url: str
    tokens: dict[str, TokenInfo] = field(default_factory=dict)
    safe_address: str | None = None
    treasury_addresses: tuple[str, ...] = ()
    alert_outflow_threshold: dict[str, Decimal] = field(default_factory=dict)
    explorer_api_url: str | None = None
    explorer_tx_url: str | None = None


def load_chain(path: str | Path) -> ChainConfig:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    tokens = {
        sym: TokenInfo(sym, int(t["decimals"]), t.get("address"))
        for sym, t in cfg.get("tokens", {}).items()
        if t.get("address") or t.get("native")
    }
    return ChainConfig(
        network=cfg["network"],
        chain_id=int(cfg["chain_id"]),
        rpc_url=cfg["rpc_url"],
        tokens=tokens,
        safe_address=cfg.get("safe_address") or None,
        treasury_addresses=tuple(cfg.get("treasury_addresses", ())),
        alert_outflow_threshold={k: to_amount(str(v)) for k, v in cfg.get("alert_outflow_threshold", {}).items()},
        explorer_api_url=cfg.get("explorer_api_url"),
        explorer_tx_url=cfg.get("explorer_tx_url"),
    )
