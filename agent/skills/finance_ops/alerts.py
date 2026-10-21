"""Treasury anomaly alerts: large outflows, unknown counterparties, address poisoning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Protocol

from agent.rules import normalize_address


@dataclass(frozen=True)
class Transfer:
    tx_hash: str
    token: str
    from_addr: str
    to_addr: str
    amount: Decimal
    at: datetime


APPROVE, INCREASE_ALLOWANCE, SET_APPROVAL_FOR_ALL = "0x095ea7b3", "0x39509351", "0xa22cb465"


@dataclass(frozen=True)
class Approval:
    """An approve / increaseAllowance / setApprovalForAll sent from a watched address."""

    tx_hash: str
    owner: str
    contract: str
    spender: str
    amount: int | None  # None for setApprovalForAll
    at: datetime

    @property
    def unlimited(self) -> bool:
        return self.amount is None or self.amount >= 2**255


def decode_approval(row: Mapping[str, Any]) -> Approval | None:
    from eth_abi import decode

    data = str(row.get("input") or "")
    selector = data[:10].lower()
    if selector not in (APPROVE, INCREASE_ALLOWANCE, SET_APPROVAL_FOR_ALL) or row.get("isError") == "1":
        return None
    try:
        if selector == SET_APPROVAL_FOR_ALL:
            spender, approved = decode(["address", "bool"], bytes.fromhex(data[10:]))
            if not approved:
                return None  # revoking is good news
            amount = None
        else:
            spender, amount = decode(["address", "uint256"], bytes.fromhex(data[10:]))
            if amount == 0:
                return None
    except Exception:  # noqa: BLE001 - malformed calldata is not an approval we can judge
        return None
    return Approval(row["hash"], row["from"], row["to"], spender.lower(), amount,
                    datetime.fromtimestamp(int(row["timeStamp"]), timezone.utc))


@dataclass(frozen=True)
class Alert:
    kind: str  # LARGE_OUTFLOW | UNKNOWN_COUNTERPARTY | ADDRESS_POISONING
    tx_hash: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.tx_hash}"


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def _looks_like(a: str, b: str) -> bool:
    # Poisoning addresses copy the first and last few characters people glance at.
    return a != b and a[:6] == b[:6] and a[-4:] == b[-4:]


def check_transfers(
    transfers: Iterable[Transfer],
    treasury: Iterable[str],
    known: Iterable[str],
    thresholds: Mapping[str, Decimal],
) -> list[Alert]:
    treasury_set = {normalize_address(a) for a in treasury}
    known_set = {normalize_address(a) for a in known} | treasury_set
    alerts: list[Alert] = []
    for t in transfers:
        src, dst = normalize_address(t.from_addr), normalize_address(t.to_addr)
        if src in treasury_set:  # outflow
            limit = thresholds.get(t.token)
            if limit is not None and t.amount >= limit:
                alerts.append(Alert("LARGE_OUTFLOW", t.tx_hash, f"🚨 国库 {_short(src)} 转出 {t.amount.normalize():f} {t.token} 到 {_short(dst)}，超过预警线 {limit.normalize():f}"))
            if dst not in known_set:
                alerts.append(Alert("UNKNOWN_COUNTERPARTY", t.tx_hash, f"⚠️ 国库 {_short(src)} 向陌生地址 {_short(dst)} 转出 {t.amount.normalize():f} {t.token}"))
        elif dst in treasury_set and src not in known_set:
            lookalike = next((k for k in known_set if _looks_like(src, k)), None)
            if lookalike:
                alerts.append(Alert("ADDRESS_POISONING", t.tx_hash, f"☠️ 疑似地址投毒：{_short(src)} 冒充 {_short(lookalike)} 向国库转入 {t.amount.normalize():f} {t.token}。复制地址付款前请核对完整地址。"))
    return alerts


class ChainDataSource(Protocol):
    def transfers(self, address: str, since: datetime) -> list[Transfer]: ...


class EtherscanV2Source:
    """Token and native transfers for one address via the Etherscan V2 multichain API (covers BSC).

    Needs an API key (free tier). `http_get` is injectable for tests.
    """

    def __init__(self, api_url: str, api_key: str, tokens: Mapping[str, str], http_get: Callable[..., Any] | None = None) -> None:
        if http_get is None:  # pragma: no cover - real network
            import httpx

            http_get = lambda url, params: httpx.get(url, params=params, timeout=20).json()  # noqa: E731
        self.api_url = api_url
        self.api_key = api_key
        self.tokens = {normalize_address(addr): sym for sym, addr in tokens.items() if addr}
        self.http_get = http_get

    def approvals(self, address: str, since: datetime) -> list[Approval]:
        data = self.http_get(self.api_url, {"module": "account", "action": "txlist", "address": address, "sort": "desc", "page": 1, "offset": 100, "apikey": self.api_key})
        out = []
        for row in data.get("result") or []:
            if not isinstance(row, dict) or int(row["timeStamp"]) < since.timestamp():
                continue
            if normalize_address(row["from"]) != normalize_address(address):
                continue
            a = decode_approval(row)
            if a:
                out.append(a)
        return out

    def transfers(self, address: str, since: datetime) -> list[Transfer]:
        out: list[Transfer] = []
        cutoff = since.timestamp()
        for action in ("tokentx", "txlist"):
            data = self.http_get(self.api_url, {"module": "account", "action": action, "address": address, "sort": "desc", "page": 1, "offset": 100, "apikey": self.api_key})
            for row in data.get("result") or []:
                if not isinstance(row, dict) or int(row["timeStamp"]) < cutoff:
                    continue
                if action == "tokentx":
                    sym = self.tokens.get(normalize_address(row["contractAddress"]))
                    if sym is None:
                        continue  # ignore tokens we don't track (spam tokens are common)
                    decimals = int(row["tokenDecimal"])
                else:
                    if row.get("isError") == "1" or int(row["value"]) == 0:
                        continue
                    sym, decimals = "BNB", 18
                out.append(
                    Transfer(
                        tx_hash=row["hash"],
                        token=sym,
                        from_addr=row["from"],
                        to_addr=row["to"],
                        amount=Decimal(int(row["value"])).scaleb(-decimals),
                        at=datetime.fromtimestamp(int(row["timeStamp"]), timezone.utc),
                    )
                )
        return out
