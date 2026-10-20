"""ERC-20 calldata. Kept tiny and pure so it can be checked byte by byte in tests."""

from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

TRANSFER_SELECTOR = keccak(text="transfer(address,uint256)")[:4]  # 0xa9059cbb
MAX_UINT256 = 2**256 - 1


def encode_transfer(to: str, units: int) -> bytes:
    if not 0 < units <= MAX_UINT256:
        raise ValueError("units out of range")
    return TRANSFER_SELECTOR + encode(["address", "uint256"], [to_checksum_address(to), units])
