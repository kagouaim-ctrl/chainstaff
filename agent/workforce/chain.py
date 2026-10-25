"""Read-only BSC access for workforce skills, on top of the plain JSON-RPC client.

Everything here reads; nothing signs. The one write path (anchoring a task result hash)
lives in anchor.py and only runs after a human approval.
"""

from __future__ import annotations

import copy
import threading
from decimal import Decimal
from typing import Any, Callable

from eth_abi import decode
from eth_utils import keccak, to_checksum_address

from agent.execution.rpc import RpcClient

EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
APPROVAL_TOPIC = "0x" + keccak(text="Approval(address,address,uint256)").hex()
OWNERSHIP_TOPIC = "0x" + keccak(text="OwnershipTransferred(address,address)").hex()
KNOWN_TOPICS = {TRANSFER_TOPIC: "Transfer", APPROVAL_TOPIC: "Approval", OWNERSHIP_TOPIC: "OwnershipTransferred"}

# Function selectors whose presence in bytecode is worth a reviewer's attention.
PRIVILEGED = {
    "mint(address,uint256)": "可增发",
    "burn(address,uint256)": "可销毁他人代币",
    "pause()": "可暂停转账",
    "unpause()": "可恢复转账",
    "blacklist(address)": "可拉黑地址",
    "addToBlacklist(address)": "可拉黑地址",
    "setFee(uint256)": "可改手续费",
    "setTaxFee(uint256)": "可改税率",
    "setMaxTxAmount(uint256)": "可限制单笔交易额",
    "transferOwnership(address)": "所有权可转移",
    "renounceOwnership()": "所有权可放弃",
    "upgradeTo(address)": "可升级实现合约",
    "upgradeToAndCall(address,bytes)": "可升级实现合约",
    "withdraw(uint256)": "可提取合约资金",
    "setApprovalForAll(address,bool)": "NFT 全部授权",
}
SELECTORS = {keccak(text=sig)[:4].hex(): (sig, why) for sig, why in PRIVILEGED.items()}


def _sel(sig: str) -> str:
    return "0x" + keccak(text=sig)[:4].hex()


def _hex_int(v: str | None) -> int | None:
    return int(v, 16) if v else None


def push4_selectors(code_hex: str) -> set[str]:
    """Selectors that appear as PUSH4 operands in runtime bytecode (how Solidity dispatchers compare them).
    Walks opcodes, so PUSH data is skipped correctly instead of matching random byte runs."""
    code = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
    out, i = set(), 0
    while i < len(code):
        op = code[i]
        if 0x60 <= op <= 0x7F:  # PUSH1..PUSH32
            n = op - 0x5F
            if n == 4 and i + 5 <= len(code):
                out.add(code[i + 1:i + 5].hex())
            i += n + 1
        else:
            i += 1
    return out


def _decode_string(raw: str) -> str | None:
    if not raw or raw == "0x":
        return None
    b = bytes.fromhex(raw[2:])
    try:
        return decode(["string"], b)[0]
    except Exception:  # some old tokens return bytes32
        s = b[:32].rstrip(b"\0")
        try:
            return s.decode("utf-8") or None
        except UnicodeDecodeError:
            return None


class Cancelled(Exception):
    """The task this read belongs to was cancelled or timed out; stop instead of reading more."""


class ChainReader:
    def __init__(self, rpc: RpcClient, network: str, chain_id: int, explorer_tx_url: str | None = None) -> None:
        self.rpc = rpc
        self.network = network
        self.chain_id = chain_id
        self.explorer_tx_url = explorer_tx_url
        self.calls = 0  # RPC calls made through this reader
        self._count_lock = threading.Lock()
        self._check: Callable[[], None] | None = None

    def fork(self, check: Callable[[], None] | None = None) -> "ChainReader":
        """A reader for one skill run: its own call counter, and a check that runs before and after every
        RPC call (it raises Cancelled once the task is cancelled, so a cancelled run stops reading)."""
        r = copy.copy(self)
        r.calls, r._count_lock, r._check = 0, threading.Lock(), check
        return r

    def _call(self, method: str, params: list) -> Any:
        if self._check:
            self._check()
        with self._count_lock:
            self.calls += 1
        out = self.rpc.call(method, params)
        if self._check:
            self._check()  # a request already in flight finishes, but its answer is not used
        return out

    def explorer(self, kind: str, value: str) -> str | None:
        if not self.explorer_tx_url:
            return None
        return self.explorer_tx_url.replace("/tx/", f"/{kind}/") + value

    # ---- primitives ----

    def block_number(self) -> int:
        return int(self._call("eth_blockNumber", []), 16)

    def block(self, number: int | str = "latest") -> dict:
        tag = hex(number) if isinstance(number, int) else number
        b = self._call("eth_getBlockByNumber", [tag, False]) or {}
        return {"number": _hex_int(b.get("number")), "timestamp": _hex_int(b.get("timestamp")),
                "gas_used": _hex_int(b.get("gasUsed")), "base_fee": _hex_int(b.get("baseFeePerGas")),
                "tx_count": len(b.get("transactions") or [])}

    def gas_price(self) -> int:
        return int(self._call("eth_gasPrice", []), 16)

    def native_balance(self, address: str) -> Decimal:
        wei = int(self._call("eth_getBalance", [to_checksum_address(address), "latest"]), 16)
        return Decimal(wei) / Decimal(10**18)

    def nonce(self, address: str) -> int:
        return int(self._call("eth_getTransactionCount", [to_checksum_address(address), "latest"]), 16)

    def code(self, address: str) -> str:
        return self._call("eth_getCode", [to_checksum_address(address), "latest"]) or "0x"

    def storage(self, address: str, slot: str) -> str:
        return self._call("eth_getStorageAt", [to_checksum_address(address), slot, "latest"]) or "0x"

    def eth_call(self, to: str, data: str) -> str | None:
        try:
            return self._call("eth_call", [{"to": to_checksum_address(to), "data": data}, "latest"])
        except RuntimeError:
            return None  # reverted or not implemented

    def logs(self, address: str, from_block: int, to_block: int, topics: list | None = None) -> list[dict]:
        flt: dict[str, Any] = {"address": to_checksum_address(address), "fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if topics:
            flt["topics"] = topics
        return self._call("eth_getLogs", [flt]) or []

    def receipt(self, tx_hash: str) -> dict | None:
        return self._call("eth_getTransactionReceipt", [tx_hash])

    def transaction(self, tx_hash: str) -> dict | None:
        return self._call("eth_getTransactionByHash", [tx_hash])

    # ---- composites ----

    def erc20(self, address: str) -> dict:
        name = _decode_string(self.eth_call(address, _sel("name()")) or "")
        symbol = _decode_string(self.eth_call(address, _sel("symbol()")) or "")
        dec_raw = self.eth_call(address, _sel("decimals()"))
        supply_raw = self.eth_call(address, _sel("totalSupply()"))
        decimals = int(dec_raw, 16) if dec_raw and dec_raw != "0x" else None
        supply = int(supply_raw, 16) if supply_raw and supply_raw != "0x" else None
        return {
            "name": name, "symbol": symbol, "decimals": decimals,
            "total_supply_raw": str(supply) if supply is not None else None,
            "total_supply": str(Decimal(supply) / Decimal(10**decimals)) if supply is not None and decimals is not None else None,
            "is_erc20": symbol is not None and decimals is not None and supply is not None,
        }

    def erc20_balance(self, token: str, holder: str, decimals: int) -> Decimal | None:
        data = _sel("balanceOf(address)") + to_checksum_address(holder)[2:].lower().rjust(64, "0")
        raw = self.eth_call(token, data)
        if not raw or raw == "0x":
            return None
        return Decimal(int(raw, 16)) / Decimal(10**decimals)

    def owner(self, address: str) -> str | None:
        raw = self.eth_call(address, _sel("owner()"))
        if not raw or len(raw) < 66:
            return None
        return "0x" + raw[-40:]

    @staticmethod
    def slot_address(raw: str) -> str | None:
        if not raw or int(raw, 16) == 0:
            return None
        return "0x" + raw[-40:]

    def decode_log(self, log: dict) -> dict:
        topics = log.get("topics") or []
        name = KNOWN_TOPICS.get(topics[0]) if topics else None
        out = {"block": _hex_int(log.get("blockNumber")), "tx_hash": log.get("transactionHash"),
               "log_index": _hex_int(log.get("logIndex")), "event": name or (topics[0][:10] if topics else "anonymous"),
               "topic0": topics[0] if topics else None}
        if name in ("Transfer", "Approval") and len(topics) >= 3:
            out["from"] = "0x" + topics[1][-40:]
            out["to"] = "0x" + topics[2][-40:]
            data = log.get("data") or "0x"
            out["value_raw"] = str(int(data, 16)) if data not in ("0x", "") and len(topics) == 3 else (str(int(topics[3], 16)) if len(topics) > 3 else None)
        elif name == "OwnershipTransferred" and len(topics) >= 3:
            out["from"] = "0x" + topics[1][-40:]
            out["to"] = "0x" + topics[2][-40:]
        return out
