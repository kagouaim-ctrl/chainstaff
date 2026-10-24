"""Write a task result hash to BSC testnet, and follow the transaction until it is mined.

No custom contract (the plan's hard rule: no own smart contracts before stage 4). The record is
a zero-value transaction from the anchoring wallet to itself whose calldata is

    b"CSTASK01" ‖ sha256(result) (32 bytes) ‖ task id (utf-8)

Anyone can read it back on BscScan and recompute the hash from the stored report.
Only runs after a human approves the result, only on chain id 97, and never moves value.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.store import _iso

from .chain import ChainReader

TESTNET_CHAIN_ID = 97
MAGIC = b"CSTASK01"


def anchor_calldata(result_hash: str, task_id: str) -> bytes:
    h = bytes.fromhex(result_hash[2:] if result_hash.startswith("0x") else result_hash)
    if len(h) != 32:
        raise ValueError("结果哈希必须是 32 字节")
    return MAGIC + h + task_id.encode()[:64]


def decode_calldata(data_hex: str) -> dict | None:
    b = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    if not b.startswith(MAGIC) or len(b) < len(MAGIC) + 32:
        return None
    return {"result_hash": "0x" + b[8:40].hex(), "task_id": b[40:].decode(errors="replace")}


class Anchor:
    def __init__(self, sender, reader: ChainReader, from_address: str) -> None:
        if reader.chain_id != TESTNET_CHAIN_ID:
            raise RuntimeError(f"第一阶段只允许在 BSC 测试网（chain id 97）写入，当前配置是 {reader.chain_id}")
        self.sender = sender  # agent.execution.executors.Sender
        self.reader = reader
        self.address = from_address

    def anchor(self, db, goal: dict) -> dict:
        data = anchor_calldata(goal["result_hash"], goal["id"])
        rid = db.add_chain_record(task_id=goal["id"], kind="task_hash", network=self.reader.network, chain_id=self.reader.chain_id,
                                  payload_hash=goal["result_hash"], from_addr=self.address.lower(), to_addr=self.address.lower(), status="pending")
        try:
            tx = self.sender.send(self.address, 0, data)
        except Exception as e:  # simulation (estimateGas) or broadcast failed: nothing was sent
            db.update_chain_record(rid, status="failed", error=f"{type(e).__name__}: {e}"[:500])
        else:
            db.update_chain_record(rid, tx_hash=tx)
        return next(r for r in db.chain_records(task_id=goal["id"]) if r["id"] == rid)

    def poll(self, db, rec: dict) -> dict | None:
        """Returns the updated record once it is final, else None."""
        if not rec["tx_hash"]:
            return None
        receipt = self.reader.receipt(rec["tx_hash"])
        if not receipt:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(rec["at"])
            if age > timedelta(minutes=15) and not self.reader.transaction(rec["tx_hash"]):
                db.update_chain_record(rec["id"], status="failed", error="15 分钟内没有被打包，节点里也查不到这笔交易")
                return next(r for r in db.chain_records(task_id=rec["task_id"]) if r["id"] == rec["id"])
            return None
        gas_used = int(receipt.get("gasUsed", "0x0"), 16)
        price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
        ok = int(receipt.get("status", "0x0"), 16) == 1
        db.update_chain_record(rec["id"], status="confirmed" if ok else "failed", block=int(receipt["blockNumber"], 16), gas_used=gas_used,
                               gas_price=str(price), fee_wei=str(gas_used * price), confirmed_at=_iso(datetime.now(timezone.utc)),
                               error=None if ok else "交易执行失败（status 0）")
        return next(r for r in db.chain_records(task_id=rec["task_id"]) if r["id"] == rec["id"])
