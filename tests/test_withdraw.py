from scripts.withdraw import main

OWNER = "0x2e26a9a31b2a91aeb2f47eef286a3e2dc3b9ee2b"
MAINNET = "config/chain.mainnet.example.json"


class FakeRpc:
    def __init__(self, chain=56):
        self.chain = chain
        self.calls = []

    def __call__(self, url):
        return self

    def chain_id(self):
        return self.chain

    def call(self, method, params):
        self.calls.append((method, params))
        return {"eth_estimateGas": hex(21000), "eth_getTransactionCount": "0x0", "eth_gasPrice": hex(10**8), "eth_sendRawTransaction": "0x" + "ab" * 32}[method]


class Wallet:
    address = "0x7Ec7916aF309102d0ad4B92E722D35479e09848c"

    def sign_transaction(self, tx):
        self.tx = tx
        return {"rawTransaction": b"\x01"}


def env(**kw):
    return {"CHAINSTAFF_CHAIN": MAINNET, "CHAINSTAFF_WALLET": "turnkey", "CHAINSTAFF_ALLOW_MAINNET": "yes", **kw}


def test_print_policy(capsys):
    assert main(["--to", OWNER, "--print-policy"], env=env()) == 0
    assert capsys.readouterr().out.strip() == f"eth.tx.chain_id == 56 && eth.tx.to == '{OWNER}'"


def test_withdraw_bnb_sends_value_to_owner(capsys):
    rpc, w = FakeRpc(), Wallet()
    assert main(["--to", OWNER, "--amount", "0.003"], env=env(), wallet_factory=lambda k: w, rpc_factory=rpc, confirm=lambda _: "yes") == 0
    assert w.tx["value"] == 3 * 10**15 and w.tx["data"] == "0x" and w.tx["chainId"] == 56
    assert "bscscan.com/tx/0x" in capsys.readouterr().out


def test_withdraw_usdt_uses_transfer():
    rpc, w = FakeRpc(), Wallet()
    assert main(["--to", OWNER, "--amount", "1", "--token", "USDT"], env=env(), wallet_factory=lambda k: w, rpc_factory=rpc, confirm=lambda _: "yes") == 0
    assert w.tx["value"] == 0 and w.tx["data"].startswith("0xa9059cbb")


def test_withdraw_guards():
    rpc = FakeRpc()
    no = lambda _: "no"  # noqa: E731
    assert main(["--to", OWNER], env=env()) == 2
    assert main(["--to", OWNER, "--amount", "1"], env=env(CHAINSTAFF_ALLOW_MAINNET=""), rpc_factory=rpc) == 2
    assert main(["--to", OWNER, "--amount", "1", "--token", "DOGE"], env=env(), rpc_factory=rpc) == 2
    assert main(["--to", OWNER, "--amount", "1"], env=env(), rpc_factory=FakeRpc(97)) == 2
    assert main(["--to", OWNER, "--amount", "1"], env=env(), rpc_factory=rpc, confirm=no) == 1
