"""Safe (Gnosis Safe) transaction building and EIP-712 SafeTx hashing.

The employee only ever *proposes* a SafeTx; owners sign it in their own Safe.
Hash layout follows Safe v1.3+ (domain = chainId + verifyingContract).
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from .erc20 import encode_transfer
from .tokens import TokenInfo, to_base_units

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

DOMAIN_TYPEHASH = keccak(text="EIP712Domain(uint256 chainId,address verifyingContract)")
SAFE_TX_TYPEHASH = keccak(
    text="SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,"
    "uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)"
)


@dataclass(frozen=True)
class SafeTx:
    to: str
    value: int
    data: bytes
    nonce: int
    operation: int = 0  # 0 = CALL; DELEGATECALL (1) is never produced by the employee
    safe_tx_gas: int = 0
    base_gas: int = 0
    gas_price: int = 0
    gas_token: str = ZERO_ADDRESS
    refund_receiver: str = ZERO_ADDRESS


def safe_tx_hash(chain_id: int, safe_address: str, tx: SafeTx) -> str:
    domain_separator = keccak(encode(["bytes32", "uint256", "address"], [DOMAIN_TYPEHASH, chain_id, safe_address]))
    struct_hash = keccak(
        encode(
            ["bytes32", "address", "uint256", "bytes32", "uint8", "uint256", "uint256", "uint256", "address", "address", "uint256"],
            [
                SAFE_TX_TYPEHASH,
                tx.to,
                tx.value,
                keccak(tx.data),
                tx.operation,
                tx.safe_tx_gas,
                tx.base_gas,
                tx.gas_price,
                tx.gas_token,
                tx.refund_receiver,
                tx.nonce,
            ],
        )
    )
    return "0x" + keccak(b"\x19\x01" + domain_separator + struct_hash).hex()


def build_payment_tx(token: TokenInfo, payee: str, amount, nonce: int) -> SafeTx:
    """A plain transfer from the Safe: native coin sends value, ERC-20 calls transfer()."""
    units = to_base_units(amount, token.decimals)
    if token.is_native:
        return SafeTx(to=to_checksum_address(payee), value=units, data=b"", nonce=nonce)
    return SafeTx(to=to_checksum_address(token.address), value=0, data=encode_transfer(payee, units), nonce=nonce)
