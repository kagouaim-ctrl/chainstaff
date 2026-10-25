"""Edge cases of the BSC data layer and the chain tools: bad inputs, node errors, odd answers, transport failures."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest
from eth_abi import encode
from eth_utils import keccak

from agent.bsc.hub import Hub
from agent.bsc.listener import ChainListener, _transfer
from agent.bsc.rpc import RpcPool, TTLCache, cache_ttl
from agent.bsc.service import ChainService
from agent.bsc.store import ChainStore
from agent.config import load_chain
from agent.store import Store
from agent.workforce.chain import TRANSFER_TOPIC, ChainReader
from agent.workforce.skills import SkillContext, SkillError
from test_bsc import OTHER, POOL, ROOT, TOKEN, WALLET, FakeBSC, head, pad, pool, processor, run_tool, transfer, wait_for


class Stub:
    """An RPC client answering from a dict of method -> value or callable; raising ones raise."""

    def __init__(self, **answers) -> None:
        self.answers = answers

    def call(self, method, params):
        a = self.answers[method]
        return a(*params) if callable(a) else a


def ctx_of(**answers) -> SkillContext:
    return SkillContext(ChainReader(Stub(**answers), "bsc-testnet", 97))


def boom(*a):
    raise RuntimeError("execution reverted")


# ------------------------------------------------------------------ chain tools

def test_contract_read_argument_types_and_return_decoding():
    seen = {}

    def call(c, tag):
        seen["data"], seen["tag"] = c["data"], tag
        return "0x" + encode(["uint256", "bytes32", "bool", "address[]"], [2**60, b"\x01" * 32, True, [WALLET]]).hex()

    ctx = ctx_of(eth_call=call)
    out = run_tool("contract_read", ctx, {"address": TOKEN, "signature": "f(uint,int,bool,bytes32,string,address[],uint[])",
                                          "args": ["0x10", -3, "true", "0x" + "ab" * 32, "hi", [WALLET], [1, "2"]],
                                          "returns": ["uint256", "bytes32", "bool", "address[]"], "block": 5})
    assert out["decoded"] == [str(2**60), "0x" + "01" * 32, True, [WALLET]] and seen["tag"] == "0x5"
    assert seen["data"].startswith("0x" + keccak(text="f(uint256,int256,bool,bytes32,string,address[],uint256[])")[:4].hex())
    bad = run_tool("contract_read", ctx, {"address": TOKEN, "signature": "f()", "returns": ["string", "string"]})
    assert "解码失败" in bad["decode_error"] and bad["raw"].startswith("0x")
    for args, sig, msg in [(["x"], "f(uint256)", "整数"), (["zz"], "f(bytes32)", "十六进制"), ([1], "f(address[])", "数组")]:
        with pytest.raises(SkillError, match=msg):
            run_tool("contract_read", ctx, {"address": TOKEN, "signature": sig, "args": args})
    with pytest.raises(SkillError, match="回滚"):
        run_tool("contract_read", ctx_of(eth_call=boom), {"address": TOKEN, "signature": "f()"})


def test_block_and_tx_tools_on_missing_and_pending_data():
    with pytest.raises(SkillError, match="没有区块"):
        run_tool("block_info", ctx_of(eth_getBlockByNumber=None), {"block": 7})

    def no_head():
        raise ConnectionError("node down")

    tx = {"from": WALLET, "to": None, "value": "0x0", "input": "0x", "nonce": "0x1", "blockNumber": "0x10", "gasPrice": "0x1"}
    rc = {"status": "0x0", "gasUsed": "0x5", "blockNumber": "0x10", "logs": [], "contractAddress": POOL}
    out = run_tool("tx_lookup", ctx_of(eth_getTransactionByHash=tx, eth_getTransactionReceipt=rc, eth_blockNumber=no_head), {"hash": "0x" + "1" * 64})
    assert out["status"] == "failed" and out["contract_created"] == POOL and "confirmations" not in out and out["method"] is None
    pending = run_tool("tx_lookup", ctx_of(eth_getTransactionByHash={**tx, "blockNumber": None}, eth_getTransactionReceipt=None), {"hash": "0x" + "2" * 64})
    assert pending["status"] == "pending" and pending["block"] is None


def test_log_tools_reject_bad_ranges_and_node_errors():
    ctx = ctx_of(eth_blockNumber="0x100", eth_getLogs=boom)
    with pytest.raises(SkillError, match="区块范围不对"):
        run_tool("event_search", ctx, {"address": TOKEN, "from_block": 200, "to_block": 100})
    with pytest.raises(SkillError, match="事件签名"):
        run_tool("event_search", ctx, {"address": TOKEN, "event": "Transfer", "blocks": 10})
    with pytest.raises(SkillError, match="拒绝了日志查询"):
        run_tool("event_search", ctx, {"address": TOKEN, "blocks": 10})
    with pytest.raises(SkillError, match="拒绝了日志查询"):
        run_tool("wallet_activity", ctx, {"address": WALLET, "blocks": 10})
    nft = {"address": TOKEN, "topics": [TRANSFER_TOPIC, pad(WALLET), pad(OTHER), "0x" + "0" * 63 + "1"], "data": "0x", "blockNumber": "0x99"}
    erc20 = {**transfer(TOKEN, WALLET, OTHER, 0), "data": "0x", "blockNumber": "0x98", "transactionHash": "0x1"}
    out = run_tool("wallet_activity", ctx_of(eth_blockNumber="0x100", eth_getLogs=lambda f: [nft, erc20]), {"address": WALLET, "blocks": 10})
    assert out["transfers"] == 2 and out["recent"][0]["value_raw"] == "0"  # the NFT transfer (4 topics) is skipped, both directions


# ------------------------------------------------------------------ RPC pool, cache, hub

def test_pool_odd_answers_and_cache_rules():
    p = RpcPool(["http://a", "http://b"], post=lambda u, b, t: "oops" if u == "http://a" else {"id": 1}, sleep=lambda s: None, retries=1)
    with pytest.raises(ConnectionError, match="result"):
        p.call("eth_blockNumber", [])
    assert "非 JSON-RPC" in p.endpoints[0].last_error
    assert cache_ttl("eth_getTransactionReceipt", ["0x1"], {}) == 3
    assert cache_ttl("eth_getBlockByNumber", ["latest", False], {}) == 0.5
    assert cache_ttl("eth_getBalance", [WALLET, "latest"], "0x1") == 1.0
    assert cache_ttl("eth_feeHistory", [], {}) == 0
    c = TTLCache()
    c.put("k", 1, 0)
    assert c.get("k") == (False, None)
    now = [0.0]
    c = TTLCache(clock=lambda: now[0])
    c.put("k", 1, 1)
    now[0] = 5
    assert c.get("k") == (False, None) and "k" not in c.data
    slow = RpcPool(["http://a"], post=FakeBSC(), rate_per_s=1, sleep=lambda s: waits.append(s))
    waits: list[float] = []
    slow.call("eth_chainId", [])
    slow.call("eth_blockNumber", [])
    assert waits and waits[0] > 0


def test_pool_health_reports_a_dead_endpoint():
    fake = FakeBSC()
    fake.down.add("http://b")
    rows = RpcPool(["http://a", "http://b"], post=fake, sleep=lambda s: None).health()
    assert rows[0]["ok"] and not rows[1]["ok"] and "refused" in rows[1]["error"]


def test_hub_drops_a_subscriber_whose_loop_is_closed():
    hub = Hub()
    loop = asyncio.new_event_loop()
    hub.subscribe(loop)
    loop.close()
    hub.publish("block", number=1)
    assert hub.stats()["subscribers"] == 0


# ------------------------------------------------------------------ processor corner cases

def test_reorg_found_while_backfilling():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    p.ingest(head(fake))
    base = fake.head
    fake.mine()
    p.ingest(head(fake))
    fake.reorg(1, [{}, {}, {}])  # base+1 replaced, then base+2 and base+3: we only hear about base+3
    p.ingest(head(fake))
    assert p.counters["reorgs"] == 1 and store.get("cursor") == base + 3
    assert store.block_hash(base + 1) == fake.blocks[base + 1]["hash"]


def test_reorg_deeper_than_what_is_stored_and_a_chain_that_keeps_changing():
    fake = FakeBSC()
    p, store, hub = processor(fake, keep_blocks=1)
    p.ingest(head(fake))
    fake.mine()
    p.ingest(head(fake))
    store.prune_blocks(0)
    fake.reorg(2, [{}, {}, {}])
    p.ingest(head(fake))
    assert p.counters["reorgs"] == 1 and store.get("cursor") == fake.head
    with pytest.raises(RuntimeError, match="没有返回区块"):
        p._process(fake.head + 5, 0.0)
    store.put_block(fake.head + 1, "0x" + "00" * 32, "0x" + "00" * 32, 0, 0)
    fake.mine()
    fake.blocks[fake.head]["parentHash"] = "0x" + "99" * 32
    with pytest.raises(RuntimeError, match="父哈希连续对不上"):
        p._process(fake.head, 0.0, depth=3)


def test_matching_ignores_odd_transactions_and_nft_transfers():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    store.add_monitor(name="w", kind="address", target=WALLET)
    store.add_monitor(name="t", kind="token_transfer", target=TOKEN)
    p.ingest(head(fake))
    b = fake.mine(logs=[{"address": TOKEN, "topics": [TRANSFER_TOPIC, pad(WALLET), pad(OTHER), "0x" + "0" * 64], "data": "0x"}])
    b["transactions"].append("0xjust-a-hash")
    p.ingest(head(fake))
    assert store.events() == []
    assert _transfer({"data": "0x"}, [TRANSFER_TOPIC, pad(WALLET), pad(OTHER)])["value_raw"] == "0"


def test_event_store_filters():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    m = store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 1)])
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 2)])
    p.ingest(head(fake))
    evs = store.events(monitor_id=m["id"], kind="token_transfer")
    first = evs[-1]
    assert len(store.events(from_block=first["block"] + 1, to_block=first["block"] + 1)) == 1
    assert len(store.events(after_id=first["id"])) == 1
    assert len(store.events(tx_hash=first["tx_hash"].upper().replace("0X", "0x"))) == 1
    assert store.update_monitor(m["id"], target=OTHER.upper().replace("0X", "0x"), topics=["0x" + "AB" * 32])["target"] == OTHER
    assert store.update_monitor(m["id"]) == store.monitor(m["id"])


# ------------------------------------------------------------------ service

def test_service_monitor_validation_and_lifecycle():
    fake = FakeBSC()
    chain = load_chain(ROOT / "config" / "chain.example.json")
    def refuse(url):  # the example config names a real node; keep this test off the network
        raise OSError("connection refused")

    svc = ChainService(Store(), chain, rpc=pool(fake), connect=refuse, poll_s=0.05)
    for kw, msg in [({"kind": "contract", "target": POOL, "topics": ["0x12"]}, "topic"), ({"kind": "address", "target": WALLET, "min_value": "-1"}, "非负"),
                    ({"kind": "address", "target": WALLET, "min_value": "abc"}, "非负"), ({"kind": "x", "target": WALLET}, "类型"),
                    ({"kind": "address", "target": WALLET, "trigger": {"workspace_id": "w"}}, "没有工作台")]:
        with pytest.raises(ValueError, match=msg):
            svc.add_monitor(name="", **kw)
    m = svc.add_monitor(name="", kind="contract", target=POOL)
    assert m["name"].startswith("合约事件")
    m = svc.update_monitor(m["id"], name="  ", min_value=5, enabled=None)
    assert m["name"].startswith("合约事件") and m["min_value"] == "5"
    with pytest.raises(ValueError, match="不存在"):
        svc.update_monitor("mon_nope", name="x")
    svc.wf = object()  # only checked for presence before the "on" value
    with pytest.raises(ValueError, match="pending 或 confirmed"):
        svc.add_monitor(name="", kind="address", target=WALLET, trigger={"workspace_id": "w", "on": "later"})
    svc.wf = None
    svc._trigger({"id": 1}, {"trigger": {"workspace_id": "w"}}, "confirmed")  # no workforce: nothing to start
    svc.start()
    svc.start()  # already running
    try:
        wait_for(lambda: svc.listener.status()["transport"] == "http-poll" and svc.events.get("cursor") == fake.head)
    finally:
        svc.stop()
    assert svc.status()["listener"]["transport"] == "stopped"


# ------------------------------------------------------------------ listener transport

class FakeSocket:
    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    async def recv(self):
        if not self.replies:
            await asyncio.sleep(3600)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return json.dumps(r)


def connector(*sessions):
    queue = list(sessions)

    @asynccontextmanager
    async def connect(url):
        yield queue.pop(0) if queue else FakeSocket([{"id": 1, "error": {"message": "gone"}}])

    return connect


def test_listener_skips_foreign_messages_and_survives_subscription_errors():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    fake.mine()
    good = FakeSocket([{"id": 1, "result": "0xsub"}, {"method": "eth_subscription", "params": {"subscription": "0xother", "result": {}}},
                       {"id": 99, "result": True}, {"method": "eth_subscription", "params": {"subscription": "0xsub", "result": head(fake)}},
                       ConnectionError("dropped")])
    lst = ChainListener(p, p.rpc, ["ws://x"], backoff_s=(0.01, 0.02), poll_s=0.02, poll_before_retry_s=0.1,
                        connect=connector(good, FakeSocket([{"id": 1, "error": {"message": "no subscriptions"}}])))
    lst.start()
    try:
        wait_for(lambda: store.get("cursor") == fake.head and any("订阅失败" in (m.get("last_error") or "") for m in hub.buffer if m["type"] == "status"))
    finally:
        lst.stop()


def test_polling_survives_a_node_error():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    fake.down.add("http://a")
    lst = ChainListener(p, p.rpc, [], poll_s=0.02)
    lst.start()
    try:
        wait_for(lambda: lst.status()["last_error"] and not lst.status()["connected"])
        fake.down.clear()
        p.rpc.endpoints[0].down_until = 0
        wait_for(lambda: lst.status()["connected"] and store.get("cursor") == fake.head)
    finally:
        lst.stop()
    assert threading.active_count() >= 1


def test_token_balance_reads_any_bep20_balance():
    def sel(sig):
        return "0x" + keccak(text=sig)[:4].hex()

    answers = {sel("name()"): encode(["string"], ["Tether USD"]), sel("symbol()"): encode(["string"], ["USDT"]),
               sel("decimals()"): encode(["uint8"], [18]), sel("totalSupply()"): encode(["uint256"], [10**24]),
               sel("balanceOf(address)"): encode(["uint256"], [25 * 10**17])}

    def call(c, tag):
        return "0x" + answers.get(c["data"][:10], b"").hex() if c["to"].lower() == TOKEN.lower() else "0x"

    ctx = ctx_of(eth_call=call, eth_blockNumber="0x64")
    out = run_tool("token_balance", ctx, {"address": WALLET, "token": TOKEN})
    assert out == {"address": WALLET.lower(), "token": TOKEN.lower(), "symbol": "USDT", "decimals": 18, "balance": "2.5",
                   "block": 100, "network": "bsc-testnet"}
    with pytest.raises(SkillError, match="不是标准 BEP-20"):
        run_tool("token_balance", ctx, {"address": WALLET, "token": WALLET})
    del answers[sel("balanceOf(address)")]
    with pytest.raises(SkillError, match="balanceOf 调用失败"):
        run_tool("token_balance", ctx, {"address": WALLET, "token": TOKEN})
