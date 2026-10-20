"""How a decided payment actually leaves (or doesn't leave) a wallet.

- Executor: pays AUTO_PAY requests from the employee's small budget wallet.
- SafeProposer: turns NEEDS_APPROVAL requests into a SafeTx for owners to sign.

Nothing here decides *whether* to pay; that already happened in agent.rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.rules import PaymentRequest

from eth_utils import to_checksum_address

from .erc20 import encode_transfer
from .rpc import RpcClient
from .safe import SafeTx, build_payment_tx, safe_tx_hash
from .tokens import TokenInfo, to_base_units


class Executor(Protocol):
    live: bool

    def pay(self, req: PaymentRequest) -> str:
        """Send the payment and return a transaction hash. Raise on failure."""


class DryRunExecutor:
    """Moves no money. Used until a wallet is configured; every reply says so."""

    live = False

    def pay(self, req: PaymentRequest) -> str:
        return f"dry-run:{req.request_id}"


class Sender(Protocol):
    """The one thing a wallet backend must offer: send a call and return its tx hash."""

    def send(self, to: str, value: int, data: bytes) -> str: ...


class WalletExecutor:
    live = True

    def __init__(self, sender: Sender, tokens: dict[str, TokenInfo]) -> None:
        self.sender = sender
        self.tokens = tokens

    def pay(self, req: PaymentRequest) -> str:
        token = self.tokens.get(req.token)
        if token is None:
            raise ValueError(f"{req.token} has no contract address configured for this network")
        units = to_base_units(req.amount, token.decimals)
        if token.is_native:
            return self.sender.send(req.payee, units, b"")
        return self.sender.send(token.address, 0, encode_transfer(req.payee, units))


class RpcSender:
    """Signs with a bnbagent WalletProvider (anything with .address and
    .sign_transaction(dict)) and broadcasts over plain JSON-RPC.

    Spending limits must ALSO be configured in the wallet's signing layer
    (e.g. Turnkey policies); this class is not a substitute for that.
    """

    def __init__(self, wallet: Any, rpc: RpcClient, chain_id: int) -> None:
        self.wallet = wallet
        self.rpc = rpc
        self.chain_id = chain_id

    def send(self, to: str, value: int, data: bytes) -> str:
        sender = to_checksum_address(self.wallet.address)
        call = {"from": sender, "to": to_checksum_address(to), "value": hex(value), "data": "0x" + data.hex()}
        # estimateGas doubles as a pre-flight simulation: a transfer that would revert fails here.
        gas = int(self.rpc.call("eth_estimateGas", [call]), 16)
        tx = {
            "chainId": self.chain_id,
            "from": sender,
            "to": call["to"],
            "value": value,
            "data": call["data"],
            "nonce": int(self.rpc.call("eth_getTransactionCount", [sender, "pending"]), 16),
            "gasPrice": int(self.rpc.call("eth_gasPrice", []), 16),
            "gas": gas * 12 // 10,  # 20% headroom
        }
        signed = self.wallet.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None) or signed
        raw_hex = raw if isinstance(raw, str) else "0x" + bytes(raw).hex()
        return self.rpc.call("eth_sendRawTransaction", [raw_hex])


@dataclass(frozen=True)
class Proposal:
    safe_tx_hash: str
    safe_tx: SafeTx
    submitted: bool  # True once owners can see it in the Safe UI


class SafeProposer(Protocol):
    def propose(self, req: PaymentRequest) -> Proposal: ...


class DraftSafeProposer:
    """Builds the exact SafeTx and its hash but does not submit it.

    Owners can still execute it by hand. Submitting to the Safe Transaction
    Service needs the employee registered as a Safe delegate; that is wired up
    after Safe's BSC support is confirmed (see docs/specs/execution.md).
    """

    def __init__(self, chain_id: int, safe_address: str, tokens: dict[str, TokenInfo], nonce: Callable[[], int]) -> None:
        self.chain_id = chain_id
        self.safe_address = safe_address
        self.tokens = tokens
        self.nonce = nonce

    def propose(self, req: PaymentRequest) -> Proposal:
        token = self.tokens.get(req.token)
        if token is None:
            raise ValueError(f"{req.token} has no contract address configured for this network")
        tx = build_payment_tx(token, req.payee, req.amount, self.nonce())
        return Proposal(safe_tx_hash(self.chain_id, self.safe_address, tx), tx, submitted=False)
