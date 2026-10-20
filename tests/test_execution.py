from datetime import datetime, timezone
from decimal import Decimal

import pytest
from eth_account.messages import encode_typed_data
from eth_utils import keccak

from agent.execution import (
    DraftSafeProposer,
    DryRunExecutor,
    SafeTx,
    TokenInfo,
    WalletExecutor,
    RpcSender,
    build_payment_tx,
    encode_transfer,
    from_base_units,
    safe_tx_hash,
    to_base_units,
)
from agent.execution.rpc import RpcClient
from agent.rules import PaymentRequest

ALICE = "0xA11ce00000000000000000000000000000007e2D"
USDT = TokenInfo("USDT", 18, "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd")
BNB = TokenInfo("BNB", 18, None)
SAFE = "0x1111111111111111111111111111111111111111"


def req(amount="300", token="USDT"):
    return PaymentRequest("r1", "@lina", ALICE, amount, token, datetime(2026, 10, 20, tzinfo=timezone.utc))


# ---------- units ----------

@pytest.mark.parametrize("amount,dec,units", [("1", 18, 10**18), ("300", 18, 300 * 10**18), ("0.5", 6, 500000), ("1.000001", 6, 1000001)])
def test_to_base_units(amount, dec, units):
    assert to_base_units(amount, dec) == units
    assert from_base_units(units, dec) == Decimal(amount)


@pytest.mark.parametrize("amount", ["0", "-1", "0.0000001"])
def test_to_base_units_rejects(amount):
    with pytest.raises(ValueError):
        to_base_units(amount, 6)


# ---------- ERC-20 calldata ----------

def test_encode_transfer_layout():
    data = encode_transfer(ALICE, 300 * 10**18)
    assert data[:4].hex() == "a9059cbb"
    assert data[4:36] == bytes(12) + bytes.fromhex(ALICE[2:])
    assert int.from_bytes(data[36:68], "big") == 300 * 10**18
    assert len(data) == 68


@pytest.mark.parametrize("units", [0, -1, 2**256])
def test_encode_transfer_range(units):
    with pytest.raises(ValueError):
        encode_transfer(ALICE, units)


# ---------- Safe hash, checked against eth_account's independent EIP-712 implementation ----------

def _reference_hash(chain_id, safe, tx: SafeTx) -> str:
    typed = {
        "types": {
            "EIP712Domain": [{"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "SafeTx": [
                {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}, {"name": "data", "type": "bytes"},
                {"name": "operation", "type": "uint8"}, {"name": "safeTxGas", "type": "uint256"}, {"name": "baseGas", "type": "uint256"},
                {"name": "gasPrice", "type": "uint256"}, {"name": "gasToken", "type": "address"}, {"name": "refundReceiver", "type": "address"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "SafeTx",
        "domain": {"chainId": chain_id, "verifyingContract": safe},
        "message": {"to": tx.to, "value": tx.value, "data": tx.data, "operation": tx.operation, "safeTxGas": tx.safe_tx_gas,
                    "baseGas": tx.base_gas, "gasPrice": tx.gas_price, "gasToken": tx.gas_token, "refundReceiver": tx.refund_receiver, "nonce": tx.nonce},
    }
    m = encode_typed_data(full_message=typed)
    return "0x" + keccak(b"\x19" + m.version + m.header + m.body).hex()


@pytest.mark.parametrize("token,amount,nonce", [(USDT, "1500", 0), (USDT, "0.01", 7), (BNB, "2", 42)])
def test_safe_tx_hash_matches_reference(token, amount, nonce):
    tx = build_payment_tx(token, ALICE, amount, nonce)
    for chain in (97, 56):
        assert safe_tx_hash(chain, SAFE, tx) == _reference_hash(chain, SAFE, tx)


def test_build_payment_tx_shapes():
    erc = build_payment_tx(USDT, ALICE, "1500", 1)
    assert erc.to.lower() == USDT.address.lower() and erc.value == 0 and erc.data == encode_transfer(ALICE, 1500 * 10**18)
    native = build_payment_tx(BNB, ALICE, "2", 1)
    assert native.to.lower() == ALICE.lower() and native.value == 2 * 10**18 and native.data == b""
    assert erc.operation == 0  # never DELEGATECALL


def test_hash_changes_with_chain_and_nonce():
    tx = build_payment_tx(USDT, ALICE, "1", 0)
    assert safe_tx_hash(97, SAFE, tx) != safe_tx_hash(56, SAFE, tx)
    assert safe_tx_hash(97, SAFE, tx) != safe_tx_hash(97, SAFE, build_payment_tx(USDT, ALICE, "1", 1))


def test_draft_proposer():
    p = DraftSafeProposer(97, SAFE, {"USDT": USDT}, lambda: 5)
    prop = p.propose(req("1500"))
    assert prop.safe_tx.nonce == 5 and not prop.submitted
    assert prop.safe_tx_hash == safe_tx_hash(97, SAFE, prop.safe_tx)
    with pytest.raises(ValueError):
        p.propose(req("1", "USDC"))


# ---------- executors ----------

class FakeSender:
    def __init__(self):
        self.calls = []

    def send(self, to, value, data):
        self.calls.append((to, value, data))
        return "0xhash"


def test_dry_run():
    assert DryRunExecutor().pay(req()) == "dry-run:r1"
    assert DryRunExecutor.live is False


def test_wallet_executor_erc20_and_native():
    s = FakeSender()
    ex = WalletExecutor(s, {"USDT": USDT, "BNB": BNB})
    assert ex.pay(req("300")) == "0xhash"
    assert s.calls[-1] == (USDT.address, 0, encode_transfer(ALICE, 300 * 10**18))
    ex.pay(req("0.5", "BNB"))
    assert s.calls[-1] == (ALICE, 5 * 10**17, b"")
    with pytest.raises(ValueError):
        ex.pay(req("1", "USDC"))


class Signed:
    raw_transaction = b"\x01\x02"


class FakeWallet:
    address = "0x00000000000000000000000000000000000000e1"

    def __init__(self):
        self.tx = None

    def sign_transaction(self, tx):
        self.tx = tx
        return Signed()


def test_rpc_sender_simulates_signs_broadcasts():
    rpc, calls = make_rpc({"eth_estimateGas": hex(50000), "eth_getTransactionCount": "0x9", "eth_gasPrice": hex(10**9), "eth_sendRawTransaction": "0x" + "ab" * 32})
    wallet = FakeWallet()
    h = RpcSender(wallet, rpc, 97).send(USDT.address, 0, b"\x12")
    assert h == "0x" + "ab" * 32
    assert [c["method"] for c in calls] == ["eth_estimateGas", "eth_getTransactionCount", "eth_gasPrice", "eth_sendRawTransaction"]
    assert wallet.tx["chainId"] == 97 and wallet.tx["nonce"] == 9 and wallet.tx["gas"] == 60000 and wallet.tx["data"] == "0x12"
    assert calls[-1]["params"] == ["0x0102"]


def test_rpc_sender_stops_when_simulation_reverts():
    rpc, calls = make_rpc({"eth_estimateGas": {"code": 3, "message": "execution reverted: transfer amount exceeds balance"}})
    wallet = FakeWallet()
    with pytest.raises(RuntimeError, match="exceeds balance"):
        RpcSender(wallet, rpc, 97).send(USDT.address, 0, b"\x12")
    assert wallet.tx is None  # nothing was signed


# ---------- read-only RPC ----------

def make_rpc(results):
    calls = []

    def post(url, body):
        calls.append(body)
        r = results[body["method"]]
        return {"error": r} if isinstance(r, dict) else {"result": r}

    return RpcClient("http://rpc", post), calls


def test_rpc_balances_and_nonce():
    rpc, calls = make_rpc({"eth_getBalance": hex(2 * 10**18), "eth_call": hex(1500 * 10**18), "eth_chainId": "0x61"})
    assert rpc.chain_id() == 97
    assert rpc.balance(BNB, ALICE) == Decimal(2)
    assert rpc.balance(USDT, ALICE) == Decimal(1500)
    assert calls[-1]["params"][0]["data"].startswith("0x70a08231")  # balanceOf
    assert rpc.total_balance([USDT, BNB], [ALICE, SAFE]) == {"USDT": Decimal(3000), "BNB": Decimal(4)}
    rpc2, calls2 = make_rpc({"eth_call": "0x7"})
    assert rpc2.safe_nonce(SAFE) == 7 and calls2[0]["params"][0]["data"] == "0xaffed0e0"  # nonce()


def test_rpc_error_raises():
    rpc, _ = make_rpc({"eth_chainId": {"code": -32000, "message": "down"}})
    with pytest.raises(RuntimeError):
        rpc.chain_id()
