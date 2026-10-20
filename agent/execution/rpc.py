"""Minimal read-only JSON-RPC client for balances. No signing, no state changes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Iterable

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from .tokens import TokenInfo, from_base_units

BALANCE_OF = keccak(text="balanceOf(address)")[:4]
SAFE_NONCE = keccak(text="nonce()")[:4]


class RpcClient:
    def __init__(self, url: str, post: Callable[[str, dict], Any] | None = None) -> None:
        if post is None:  # pragma: no cover - real network
            import httpx

            post = lambda u, body: httpx.post(u, json=body, timeout=20).json()  # noqa: E731
        self.url = url
        self.post = post

    def call(self, method: str, params: list) -> Any:
        res = self.post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in res:
            raise RuntimeError(f"RPC {method} failed: {res['error']}")
        return res["result"]

    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def balance(self, token: TokenInfo, holder: str) -> Decimal:
        if token.is_native:
            raw = self.call("eth_getBalance", [to_checksum_address(holder), "latest"])
        else:
            data = "0x" + (BALANCE_OF + encode(["address"], [to_checksum_address(holder)])).hex()
            raw = self.call("eth_call", [{"to": to_checksum_address(token.address), "data": data}, "latest"])
        return from_base_units(int(raw, 16), token.decimals)

    def total_balance(self, tokens: Iterable[TokenInfo], holders: Iterable[str]) -> dict[str, Decimal]:
        holders = list(holders)
        return {t.symbol: sum((self.balance(t, h) for h in holders), Decimal(0)) for t in tokens}

    def safe_nonce(self, safe: str) -> int:
        raw = self.call("eth_call", [{"to": to_checksum_address(safe), "data": "0x" + SAFE_NONCE.hex()}, "latest"])
        return int(raw, 16)
