"""BSC realtime data layer: RPC pool, processor (backfill, dedupe, reorgs, confirmations, monitors, triggers),
WebSocket listener against a local fake node, the console gateway, and the new read-only chain tools.

Everything runs against an in-process fake BSC node. None of it proves the listener works against a real
BSC endpoint; that needs network access to one (see docs/KAGOU_INTEGRATION_REPORT.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest
import websockets
from eth_abi import encode
from eth_utils import keccak
from fastapi.testclient import TestClient

from agent.bsc.hub import Hub
from agent.bsc.listener import ChainListener, ChainProcessor
from agent.bsc.rpc import RateLimiter, RpcError, RpcPool, RpcUnavailable, TTLCache, _redact, cache_ttl
from agent.bsc.service import ChainService
from agent.bsc.store import CONFIRMED, PENDING, REORGED, ChainStore
from agent.config import load_chain
from agent.store import Store
from agent.workforce import Workforce
from agent.workforce.chain import TRANSFER_TOPIC, ChainReader
from agent.workforce.skills import REGISTRY, SkillContext, SkillError, validate

ROOT = Path(__file__).resolve().parent.parent
WALLET = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
TOKEN = "0x" + "cc" * 20
POOL = "0x" + "dd" * 20
SWAP_TOPIC = "0x" + keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()


def pad(a: str) -> str:
    return "0x" + a[2:].lower().rjust(64, "0")


def h32(*parts) -> str:
    return "0x" + hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


class FakeBSC:
    """A small BSC node: canonical blocks with transactions and logs, forks on demand, JSON-RPC over post()."""

    def __init__(self, chain_id: int = 97, start: int = 100) -> None:
        self.chain_id = chain_id
        self.blocks: dict[int, dict] = {}
        self.by_hash: dict[str, dict] = {}
        self.fork = 0
        self.calls: list[str] = []
        self.down: set[str] = set()  # urls that refuse connections
        self.lock = threading.Lock()
        for n in range(start - 3, start + 1):
            self.mine(n=n)

    @property
    def head(self) -> int:
        return max(self.blocks)

    def mine(self, txs=(), logs=(), n: int | None = None) -> dict:
        with self.lock:
            n = self.head + 1 if n is None else n
            parent = self.blocks[n - 1]["hash"] if n - 1 in self.blocks else "0x" + "00" * 32
            h = h32("block", n, self.fork, parent)
            txs = [{"hash": t.get("hash") or h32("tx", h, i), "from": t["from"], "to": t.get("to"), "value": hex(t.get("value", 0)),
                    "input": t.get("input", "0x"), "blockHash": h, "blockNumber": hex(n)} for i, t in enumerate(txs)]
            out_logs = []
            for i, lg in enumerate(logs):
                out_logs.append({**lg, "blockHash": h, "blockNumber": hex(n), "logIndex": hex(i),
                                 "transactionHash": lg.get("transactionHash") or h32("ltx", h, i)})
            b = {"number": hex(n), "hash": h, "parentHash": parent, "timestamp": hex(1_760_000_000 + n * 3), "gasUsed": "0x10",
                 "gasLimit": "0x100", "transactions": txs, "logs": out_logs, "miner": OTHER}
            self.blocks[n] = b
            self.by_hash[h] = b
            return b

    def reorg(self, depth: int, new_blocks: list[dict]) -> None:
        """Drop the top `depth` blocks and mine `new_blocks` (kwargs for mine) in their place."""
        with self.lock:
            self.fork += 1
            for n in range(self.head - depth + 1, self.head + 1):
                del self.blocks[n]
        for kw in new_blocks:
            self.mine(**kw)

    def __call__(self, url: str, body: dict, timeout: float | None = None) -> dict:
        if url in self.down:
            raise ConnectionError(f"{url} refused")
        m, p = body["method"], body["params"]
        self.calls.append(m)
        return {"jsonrpc": "2.0", "id": body["id"], "result": getattr(self, m)(*p)}

    def _view(self, b: dict | None, full: bool) -> dict | None:
        if b is None:
            return None
        return {**{k: v for k, v in b.items() if k != "logs"}, "transactions": b["transactions"] if full else [t["hash"] for t in b["transactions"]]}

    def eth_chainId(self):
        return hex(self.chain_id)

    def eth_blockNumber(self):
        return hex(self.head)

    def eth_getBlockByNumber(self, tag, full):
        n = self.head if tag == "latest" else int(tag, 16)
        return self._view(self.blocks.get(n), full)

    def eth_getBlockByHash(self, h, full):
        b = self.by_hash.get(h)
        return self._view(b if b and self.blocks.get(int(b["number"], 16)) is b else b, full)

    def eth_getLogs(self, flt):
        if "blockHash" in flt:
            b = self.by_hash.get(flt["blockHash"])
            blocks = [b] if b else []
        else:
            lo, hi = int(flt["fromBlock"], 16), int(flt["toBlock"], 16)
            blocks = [self.blocks[n] for n in range(lo, hi + 1) if n in self.blocks]
        addrs = flt.get("address")
        addrs = {a.lower() for a in ([addrs] if isinstance(addrs, str) else addrs or [])}
        out = []
        for b in blocks:
            for lg in b["logs"]:
                if addrs and lg["address"].lower() not in addrs:
                    continue
                if not _topics_match(flt.get("topics") or [], lg["topics"]):
                    continue
                out.append(lg)
        return out

    def eth_getTransactionByHash(self, h):
        for b in self.blocks.values():
            for t in b["transactions"]:
                if t["hash"] == h:
                    return {**t, "nonce": "0x5", "gasPrice": hex(10**9)}
        return None

    def eth_getTransactionReceipt(self, h):
        for b in self.blocks.values():
            for t in b["transactions"]:
                if t["hash"] == h:
                    logs = [lg for lg in b["logs"] if lg["transactionHash"] == h]
                    return {"status": "0x1", "gasUsed": hex(21000), "effectiveGasPrice": hex(10**9), "blockNumber": b["number"], "logs": logs,
                            "contractAddress": None}
        return None

    def eth_call(self, call, tag):
        data = call["data"]
        if data.startswith("0x" + keccak(text="balanceOf(address)")[:4].hex()):
            return "0x" + encode(["uint256"], [12345]).hex()
        if data.startswith("0x" + keccak(text="getReserves()")[:4].hex()):
            return "0x" + encode(["uint112", "uint112", "uint32"], [10, 20, 30]).hex()
        raise_revert()


def raise_revert():
    raise AssertionError("unexpected eth_call")


def _topics_match(want: list, got: list) -> bool:
    for i, w in enumerate(want):
        if w is None:
            continue
        if i >= len(got):
            return False
        options = w if isinstance(w, list) else [w]
        if got[i].lower() not in {o.lower() for o in options}:
            return False
    return True


def transfer(token: str, frm: str, to: str, value: int, tx: str | None = None) -> dict:
    lg = {"address": token, "topics": [TRANSFER_TOPIC, pad(frm), pad(to)], "data": "0x" + hex(value)[2:].rjust(64, "0")}
    if tx:
        lg["transactionHash"] = tx
    return lg


def pool(fake: FakeBSC, urls=("http://a",), **kw) -> RpcPool:
    return RpcPool(list(urls), chain_id=97, post=fake, sleep=lambda s: None, **kw)


def processor(fake: FakeBSC, confirmations: int = 2, **kw):
    store = ChainStore(Store())
    hub = Hub()
    p = ChainProcessor(pool(fake), store, hub, chain_id=97, confirmations=confirmations, **kw)
    return p, store, hub


def head(fake: FakeBSC) -> dict:
    b = fake.blocks[fake.head]
    return {"number": b["number"], "hash": b["hash"], "parentHash": b["parentHash"]}


def types(hub: Hub) -> list[str]:
    return [m["type"] for m in hub.buffer]


# ------------------------------------------------------------------ RPC pool

def test_pool_fails_over_and_cools_down_a_dead_endpoint():
    fake = FakeBSC()
    fake.down.add("http://a")
    p = pool(fake, ("http://a", "http://b"))
    assert int(p.call("eth_blockNumber", []), 16) == fake.head
    s = {e["url"]: e for e in p.stats()["endpoints"]}
    assert not s["http://a"]["healthy"] and s["http://a"]["errors"] == 1 and s["http://b"]["healthy"]
    fake.calls.clear()
    p.cache.data.clear()
    p.call("eth_blockNumber", [])
    assert p.endpoints[0].calls == 1  # the dead one was skipped while cooling down


def test_pool_does_not_retry_a_node_error_or_a_raw_transaction():
    fake = FakeBSC()
    p = pool(fake, ("http://a", "http://b"))
    fake.eth_call = lambda c, t: (_ for _ in ()).throw(AssertionError)  # never reached
    p._post = lambda u, body, t: {"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}}
    with pytest.raises(RpcError, match="reverted"):
        p.call("eth_call", [{}, "latest"])
    assert sum(e.calls for e in p.endpoints) == 1
    sent = []
    p._post = lambda u, body, t: sent.append(u) or (_ for _ in ()).throw(TimeoutError("slow"))
    with pytest.raises(RpcUnavailable):
        p.call("eth_sendRawTransaction", ["0x00"])
    assert len(sent) == 1  # a write is never sent twice
    assert isinstance(RpcUnavailable("x"), ConnectionError)


def test_pool_moves_on_from_a_rate_limited_node():
    fake = FakeBSC()
    p = pool(fake, ("http://a", "http://b"))
    real = p._post
    p._post = lambda u, body, t: {"id": 1, "error": {"code": -32005, "message": "limit exceeded"}} if u == "http://a" else real(u, body, t)
    assert p.call("eth_chainId", []) == hex(97)
    assert p.endpoints[0].errors == 1 and "限流" in p.endpoints[0].last_error


def test_pool_gives_up_after_retries():
    fake = FakeBSC()
    fake.down |= {"http://a", "http://b"}
    p = pool(fake, ("http://a", "http://b"), retries=3)
    with pytest.raises(RpcUnavailable, match="所有节点"):
        p.call("eth_blockNumber", [])
    assert sum(e.errors for e in p.endpoints) == 4


def test_pool_cache_keeps_only_reorg_safe_answers_for_long():
    now = [0.0]
    fake = FakeBSC()
    p = RpcPool(["http://a"], post=fake, sleep=lambda s: None, clock=lambda: now[0])
    p.call("eth_chainId", [])
    p.call("eth_chainId", [])
    assert fake.calls.count("eth_chainId") == 1
    p.call("eth_getBlockByNumber", [hex(99), False])
    p.call("eth_getBlockByNumber", [hex(99), False])
    assert fake.calls.count("eth_getBlockByNumber") == 2  # by number: a reorg can change it
    p.call("eth_blockNumber", [])
    now[0] += 1
    p.call("eth_blockNumber", [])
    assert fake.calls.count("eth_blockNumber") == 2
    assert cache_ttl("eth_getLogs", [{"blockHash": "0x1"}], []) == 300
    assert cache_ttl("eth_getLogs", [{"fromBlock": "0x1", "toBlock": "0x2"}], []) == 0
    assert cache_ttl("eth_sendRawTransaction", ["0x"], "0x1") == 0
    c = TTLCache(capacity=2)
    for k in "abc":
        c.put(k, k, 10)
    assert list(c.data) == ["b", "c"]


def test_pool_health_check_flags_the_wrong_chain():
    a, b = FakeBSC(chain_id=97), FakeBSC(chain_id=56)
    p = RpcPool(["http://a", "http://b"], chain_id=97, post=lambda u, body, t: (a if u == "http://a" else b)(u, body), sleep=lambda s: None)
    rows = p.health()
    assert rows[0]["ok"] and rows[0]["chain_id"] == 97
    assert not rows[1]["ok"] and "56" in rows[1]["error"]
    assert not p.stats()["endpoints"][1]["healthy"]


def test_rate_limiter_and_url_redaction():
    now = [0.0]
    lim = RateLimiter(2, burst=2, clock=lambda: now[0])
    assert [lim.wait_time() for _ in range(3)] == [0.0, 0.0, 0.5]
    now[0] += 1.5
    assert lim.wait_time() == 0.0
    assert _redact("https://bsc.example.com/v1/SECRETKEY12345678?key=abc") == "https://bsc.example.com/v1/SE…?…"
    assert "SECRET" not in json.dumps(RpcPool(["https://x.io/k/SECRETSECRETSECRET"], post=lambda *a: {}).stats())


# ------------------------------------------------------------------ processor

def test_monitors_match_native_and_token_transfers_and_contract_logs():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    mw = store.add_monitor(name="treasury", kind="address", target=WALLET)
    mt = store.add_monitor(name="big USDT", kind="token_transfer", target=TOKEN, min_value="1000")
    mc = store.add_monitor(name="pool swaps", kind="contract", target=POOL, topics=[SWAP_TOPIC])
    p.ingest(head(fake))
    fake.mine(txs=[{"from": WALLET, "to": OTHER, "value": 5 * 10**17}, {"from": OTHER, "to": POOL, "value": 0}],
              logs=[transfer(TOKEN, OTHER, WALLET, 7), transfer(TOKEN, OTHER, POOL, 5000),
                    {"address": POOL, "topics": [SWAP_TOPIC, pad(OTHER)], "data": "0x01"},
                    {"address": POOL, "topics": ["0x" + "11" * 32], "data": "0x"}])
    p.ingest(head(fake))
    evs = store.events(limit=50)
    by = {(e["monitor_id"], e["kind"]) for e in evs}
    assert (mw["id"], "native_transfer") in by and (mw["id"], "token_transfer") in by
    assert [e["data"]["value_raw"] for e in evs if e["monitor_id"] == mt["id"]] == ["5000"]  # the 7 is under min_value
    assert [e["data"]["event"] for e in evs if e["monitor_id"] == mc["id"]] == [SWAP_TOPIC[:10]]  # the other topic is filtered
    native = next(e for e in evs if e["kind"] == "native_transfer")
    assert native["data"]["direction"] == "out" and native["data"]["value_bnb"] == "0.5" and native["status"] == PENDING
    inbound = next(e for e in evs if e["monitor_id"] == mw["id"] and e["kind"] == "token_transfer")
    assert inbound["data"]["direction"] == "in" and inbound["data"]["token"] == TOKEN
    assert types(hub).count("event") == 4 and types(hub).count("block") == 2


def test_duplicate_heads_and_replayed_blocks_store_each_event_once():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, WALLET, OTHER, 1)])
    p.ingest(head(fake))
    p.ingest(head(fake))
    assert p.counters["duplicates"] == 1 and len(store.events()) == 1
    # A restart that forgot its cursor re-reads the block: the store still refuses the duplicate.
    store.set("cursor", fake.head - 1)
    store.drop_blocks_above(fake.head - 1)
    p.ingest(head(fake))
    assert len(store.events()) == 1
    # A head older than anything kept is stale, not a reorg.
    store.prune_blocks(0)
    p.ingest({"number": hex(fake.head - 3), "hash": "0x" + "ee" * 32})
    assert p.counters["reorgs"] == 0


def test_missed_blocks_are_backfilled_in_order():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    start = fake.head
    for i in range(4):
        fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, i + 1)])
    p.ingest(head(fake))  # one head arrives after four blocks were produced
    assert p.counters["backfilled"] == 3 and store.get("cursor") == start + 4
    assert sorted(e["block"] for e in store.events()) == [start + 1, start + 2, start + 3, start + 4]
    assert [m["number"] for m in hub.buffer if m["type"] == "block"] == [start, start + 1, start + 2, start + 3, start + 4]


def test_a_gap_larger_than_the_backfill_window_is_reported():
    fake = FakeBSC()
    p, store, hub = processor(fake, max_backfill=3)
    p.ingest(head(fake))
    for _ in range(10):
        fake.mine()
    p.ingest(head(fake))
    gap = next(m for m in hub.buffer if m["type"] == "gap")
    assert p.counters["gaps"] == 1 and "跳过了 6 个区块" in gap["text"] and store.get("cursor") == fake.head


def test_reorg_marks_orphaned_events_and_reads_the_new_branch():
    fake = FakeBSC()
    p, store, hub = processor(fake, confirmations=5)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    base = fake.head
    fake.mine()
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 1, tx="0x" + "01" * 32)])
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 2, tx="0x" + "02" * 32)])
    p.ingest(head(fake))
    assert len(store.events(status=PENDING)) == 2
    # Blocks base+2 and base+3 are replaced; tx 02 moves into the new base+3, tx 01 disappears; base+4 is new.
    fake.reorg(2, [{}, {"logs": [transfer(TOKEN, OTHER, WALLET, 2, tx="0x" + "02" * 32)]}, {}])
    p.ingest(head(fake))
    assert p.counters["reorgs"] == 1
    reorged = store.events(status=REORGED)
    assert sorted(e["data"]["value_raw"] for e in reorged) == ["1", "2"]
    live = store.events(status=PENDING)
    assert [(e["block"], e["data"]["value_raw"]) for e in live] == [(base + 3, "2")]
    assert store.block_hash(base + 3) == fake.blocks[base + 3]["hash"] and store.get("cursor") == base + 4
    r = next(m for m in hub.buffer if m["type"] == "reorg")
    assert r["common_ancestor"] == base + 1 and r["events"] == 2
    assert types(hub).count("event.reorged") == 2


def test_reorg_announced_by_a_replacement_head_at_the_same_height():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 9)])
    p.ingest(head(fake))
    fake.reorg(1, [{}])
    p.ingest(head(fake))  # same number, different hash
    assert p.counters["reorgs"] == 1 and len(store.events(status=REORGED)) == 1
    assert store.block_hash(fake.head) == fake.blocks[fake.head]["hash"]


def test_events_confirm_after_n_blocks_and_reorged_ones_never_do():
    fake = FakeBSC()
    p, store, hub = processor(fake, confirmations=2)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 1)])
    p.ingest(head(fake))
    fake.mine()
    p.ingest(head(fake))
    assert store.events(status=CONFIRMED) == []
    fake.mine()
    p.ingest(head(fake))
    assert len(store.events(status=CONFIRMED)) == 1 and types(hub).count("event.confirmed") == 1


def test_monitor_trigger_starts_one_mission_per_confirmed_event():
    from test_workforce import team

    fake = FakeBSC()
    st = Store()
    wf = Workforce(st, reader=ChainReader(pool(fake), "bsc-testnet", 97))
    t = team(wf)
    chain = load_chain(ROOT / "config" / "chain.example.json")
    svc = ChainService(st, chain, wf)
    assert svc.rpc is wf.reader.rpc  # one pool for agents' tools and the listener
    svc.processor.confirmations = 1
    assert wf.events is svc.events
    mon = svc.add_monitor(name="treasury", kind="address", target=WALLET,
                          trigger={"workspace_id": t.ws["id"], "goal": "分析 {tx_hash}：{from} → {to} 数量 {value}"})
    with pytest.raises(ValueError, match="地址格式"):
        svc.add_monitor(name="x", kind="address", target="0x12")
    with pytest.raises(Exception, match="工作区|workspace|不存在"):
        svc.add_monitor(name="x", kind="address", target=WALLET, trigger={"workspace_id": "ws_nope"})
    svc.processor.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 77, tx="0x" + "07" * 32)])
    svc.processor.ingest(head(fake))
    assert wf.db.tasks(workspace_id=t.ws["id"])[0] == []  # pending: not yet
    fake.mine()
    svc.processor.ingest(head(fake))
    goals = [x for x in wf.db.tasks(workspace_id=t.ws["id"])[0] if x["kind"] == "goal"]
    assert len(goals) == 1 and "0x0707" in goals[0]["title"] and "数量 77" in goals[0]["description"]
    ev = svc.events.events(monitor_id=mon["id"])[0]
    assert ev["task_id"] == goals[0]["id"]
    svc._trigger({**ev, "status": CONFIRMED}, svc.events.monitor(mon["id"]), CONFIRMED)  # seen again after a restart
    assert len([x for x in wf.db.tasks(workspace_id=t.ws["id"])[0] if x["kind"] == "goal"]) == 1
    assert any(m["type"] == "trigger" and m["replayed"] for m in svc.hub.buffer)
    wf.stop()


def test_a_failing_trigger_does_not_stop_the_listener():
    fake = FakeBSC()
    boom = lambda ev, mon, phase: (_ for _ in ()).throw(RuntimeError("workspace gone"))  # noqa: E731
    p, store, hub = processor(fake, confirmations=0, on_event=boom)
    store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 1)])
    p.ingest(head(fake))
    fake.mine()
    p.ingest(head(fake))
    assert store.get("cursor") == fake.head and "trigger.error" in types(hub)


# ------------------------------------------------------------------ WebSocket listener against a local node

class FakeNode:
    """A WebSocket JSON-RPC endpoint that supports eth_subscribe newHeads, pushes heads on demand, and can drop
    every connection or go silent."""

    def __init__(self, fake: FakeBSC) -> None:
        self.fake = fake
        self.conns: set = set()
        self.subscribes = 0
        self.silent = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.port = 0
        self.ready = threading.Event()
        self.stop_ev: asyncio.Event | None = None

    async def handler(self, ws):
        self.conns.add(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["method"] == "eth_subscribe":
                    self.subscribes += 1
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": "0xsub"}))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.conns.discard(ws)

    def run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            self.stop_ev = asyncio.Event()
            async with websockets.serve(self.handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self.ready.set()
                await self.stop_ev.wait()

        threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
        assert self.ready.wait(5)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def push_head(self):
        if self.silent:
            return
        b = head(self.fake)
        msg = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {"subscription": "0xsub", "result": b}})

        async def send():
            for c in list(self.conns):
                await c.send(msg)

        asyncio.run_coroutine_threadsafe(send(), self.loop).result(5)

    def drop_all(self):
        async def close():
            for c in list(self.conns):
                await c.close()

        asyncio.run_coroutine_threadsafe(close(), self.loop).result(5)

    def close(self):
        self.loop.call_soon_threadsafe(self.stop_ev.set)


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("condition not reached")


@pytest.fixture
def node():
    fake = FakeBSC()
    n = FakeNode(fake)
    n.run()
    yield n
    n.close()


def test_websocket_listener_follows_heads_reconnects_and_backfills(node):
    fake = node.fake
    p, store, hub = processor(fake)
    store.add_monitor(name="w", kind="address", target=WALLET)
    lst = ChainListener(p, p.rpc, [node.url], backoff_s=(0.05, 0.2), stall_s=5)
    lst.start()
    try:
        wait_for(lambda: lst.status()["connected"] and store.get("cursor") == fake.head)
        assert lst.status()["transport"] == "websocket"
        fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 1)])
        node.push_head()
        wait_for(lambda: store.get("cursor") == fake.head)
        # The node drops the connection; three blocks are produced while the listener is away.
        node.drop_all()
        for i in range(3):
            fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 10 + i)])
        wait_for(lambda: node.subscribes >= 2 and store.get("cursor") == fake.head)
        assert sorted(int(e["data"]["value_raw"]) for e in store.events()) == [1, 10, 11, 12]
        assert lst.status()["reconnects"] >= 1
    finally:
        lst.stop()
    assert lst.status()["transport"] == "stopped"


def test_websocket_listener_reconnects_when_the_node_goes_silent(node):
    fake = node.fake
    p, store, hub = processor(fake)
    lst = ChainListener(p, p.rpc, [node.url], backoff_s=(0.05, 0.2), stall_s=0.3, poll_s=0.05, poll_before_retry_s=0.5)
    lst.start()
    try:
        # No heads at all: after the stall timeout it re-subscribes; after two silent sessions it polls over HTTP,
        # still following the chain, then goes back to the WebSocket.
        wait_for(lambda: node.subscribes >= 2 and lst.status()["transport"] == "http-poll")
        assert any("没有新区块" in (m.get("last_error") or "") for m in hub.buffer if m["type"] == "status")
        fake.mine()
        wait_for(lambda: store.get("cursor") == fake.head)
        wait_for(lambda: node.subscribes >= 3)
    finally:
        lst.stop()


def test_listener_falls_back_to_http_polling_when_no_websocket_works():
    fake = FakeBSC()
    p, store, hub = processor(fake)
    calls = []

    def refuse(url):
        calls.append(url)
        raise OSError("connection refused")

    lst = ChainListener(p, p.rpc, ["ws://dead-1", "ws://dead-2"], backoff_s=(0.01, 0.02), poll_s=0.05, connect=refuse)
    lst.start()
    try:
        wait_for(lambda: lst.status()["transport"] == "http-poll" and store.get("cursor") == fake.head)
        fake.mine()
        wait_for(lambda: store.get("cursor") == fake.head)
        assert calls[:4] == ["ws://dead-1", "ws://dead-2", "ws://dead-1", "ws://dead-2"]
    finally:
        lst.stop()


# ------------------------------------------------------------------ console gateway

TOKEN_AUTH = "t" * 24


@pytest.fixture
def gateway(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    chain = load_chain(ROOT / "config" / "chain.example.json")
    fake = FakeBSC()
    wf = Workforce(emp.store)
    svc = ChainService(emp.store, chain, wf, rpc=pool(fake))
    c = TestClient(create_app(emp, TOKEN_AUTH, chain, workforce=wf, chain_service=svc))
    yield c, svc, fake
    wf.stop()


H = {"Authorization": "Bearer " + TOKEN_AUTH}


def test_gateway_rest_monitors_events_status_and_health(gateway):
    c, svc, fake = gateway
    assert c.get("/api/chain/status").status_code == 401
    st = c.get("/api/chain/status", headers=H).json()
    assert st["enabled"] and st["chain_id"] == 97 and st["read_only"] and st["listener"]["transport"] == "stopped"
    m = c.post("/api/chain/monitors", headers=H, json={"name": "treasury", "kind": "address", "target": WALLET}).json()
    assert m["kind"] == "address" and m["enabled"]
    assert c.post("/api/chain/monitors", headers=H, json={"kind": "nope", "target": WALLET}).status_code == 400
    assert c.post("/api/chain/monitors", headers=H, json={"kind": "contract", "target": POOL, "topics": ["0x12"]}).status_code == 400
    assert c.patch(f"/api/chain/monitors/{m['id']}", headers=H, json={"enabled": False}).json()["enabled"] is False
    assert c.patch("/api/chain/monitors/mon_nope", headers=H, json={"enabled": False}).status_code == 404
    c.patch(f"/api/chain/monitors/{m['id']}", headers=H, json={"enabled": True})
    svc.processor.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 3)])
    svc.processor.ingest(head(fake))
    evs = c.get("/api/chain/events", headers=H, params={"address": WALLET}).json()["events"]
    assert len(evs) == 1 and evs[0]["data"]["value_raw"] == "3"
    hc = c.post("/api/chain/health-check", headers=H).json()
    assert hc["ok"] and hc["endpoints"][0]["chain_id"] == 97
    assert c.delete(f"/api/chain/monitors/{m['id']}", headers=H).json() == {"ok": True}
    assert c.delete(f"/api/chain/monitors/{m['id']}", headers=H).status_code == 404


def test_gateway_status_without_a_chain(tmp_path):
    from agent.runtime import build_employee
    from backend.app import create_app

    p = tmp_path / "policy.json"
    p.write_text((ROOT / "config" / "policy.example.json").read_text())
    emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp_path / "l.db")})
    c = TestClient(create_app(emp, TOKEN_AUTH, None))
    assert c.get("/api/chain/status", headers=H).json()["enabled"] is False
    assert c.get("/api/chain/monitors", headers=H).status_code == 409
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN_AUTH})
        assert ws.receive_json()["enabled"] is False


def test_gateway_websocket_requires_the_token_first(gateway):
    from starlette.websockets import WebSocketDisconnect

    c, svc, fake = gateway
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": "wrong" * 5})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_text("not json")
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_gateway_websocket_streams_live_and_resumes_from_a_sequence(gateway):
    c, svc, fake = gateway
    svc.add_monitor(name="w", kind="address", target=WALLET)
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN_AUTH})
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["status"]["chain_id"] == 97
        svc.processor.ingest(head(fake))
        fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 5)])
        svc.processor.ingest(head(fake))
        got = [ws.receive_json() for _ in range(3)]
        assert [g["type"] for g in got] == ["block", "block", "event"]
        assert got[2]["event"]["data"]["value_raw"] == "5" and got[2]["monitor"]["name"] == "w"
        last = got[-1]["seq"]
    fake.mine()
    svc.processor.ingest(head(fake))  # produced while the browser was away
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN_AUTH, "since": last, "topics": ["block"]})
        assert ws.receive_json()["type"] == "hello"
        missed = ws.receive_json()
        assert missed["type"] == "block" and missed["number"] == fake.head and missed["seq"] > last


def test_hub_tells_a_slow_browser_to_resync():
    hub = Hub(buffer=10, queue_size=2)

    async def main():
        sub, _ = hub.subscribe(asyncio.get_running_loop())
        for i in range(5):
            hub.publish("block", number=i)
        await asyncio.sleep(0.05)
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        return sub, items

    sub, items = asyncio.run(main())
    assert sub.overflowed and items[-1]["type"] == "resync"
    loop = asyncio.new_event_loop()
    _, backlog = hub.subscribe(loop, since=2)
    assert [m["seq"] for m in backlog] == [3, 4, 5]
    small = Hub(buffer=3)
    for i in range(5):
        small.publish("block", number=i)
    _, backlog = small.subscribe(loop, since=1)  # seq 2 already left the buffer
    assert backlog[0] == {"type": "resync", "seq": 1, "reason": "gap"} and [m["seq"] for m in backlog[1:]] == [3, 4, 5]
    loop.close()


# ------------------------------------------------------------------ read-only chain tools

def tool_ctx(fake: FakeBSC, events=None) -> SkillContext:
    return SkillContext(ChainReader(pool(fake), "bsc-testnet", 97, "https://testnet.bscscan.com/tx/"), events=events)


def run_tool(sid: str, ctx: SkillContext, inp: dict) -> dict:
    s = REGISTRY.get(sid)
    validate(s.input_schema, inp)
    return s.run(ctx, inp)


def test_chain_tools_are_registered_read_only_agent_tools():
    import agent.workforce.chaintools  # noqa: F401

    for sid in ("block_info", "tx_lookup", "contract_read", "event_search", "wallet_activity", "chain_events"):
        s = REGISTRY.get(sid)
        assert s and s.read_only and s.agent_tool and s.permissions == ("chain:read",), sid


def test_block_info_and_tx_lookup():
    fake = FakeBSC()
    b = fake.mine(txs=[{"from": WALLET, "to": OTHER, "value": 10**18, "input": "0xa9059cbb" + "00" * 64}],
                  logs=[transfer(TOKEN, WALLET, OTHER, 4)])
    fake.blocks[fake.head]["logs"][0]["transactionHash"] = b["transactions"][0]["hash"]
    fake.mine()
    ctx = tool_ctx(fake)
    bi = run_tool("block_info", ctx, {"block": int(b["number"], 16)})
    assert bi["hash"] == b["hash"] and bi["tx_count"] == 1 and bi["explorer"].endswith(f"/block/{int(b['number'], 16)}")
    tx = run_tool("tx_lookup", ctx, {"hash": b["transactions"][0]["hash"]})
    assert tx["status"] == "success" and tx["value_bnb"] == "1" and tx["method"] == "0xa9059cbb" and tx["confirmations"] == 2
    assert tx["fee_bnb"] == "0.000021" and tx["events"][0]["event"] == "Transfer"
    with pytest.raises(SkillError, match="找不到交易"):
        run_tool("tx_lookup", ctx, {"hash": "0x" + "ab" * 32})


def test_contract_read_encodes_args_and_decodes_returns():
    fake = FakeBSC()
    ctx = tool_ctx(fake)
    out = run_tool("contract_read", ctx, {"address": TOKEN, "signature": "balanceOf(address)", "args": [WALLET], "returns": ["uint256"]})
    assert out["decoded"] == [12345] and out["function"] == "balanceOf(address)"
    out = run_tool("contract_read", ctx, {"address": POOL, "signature": "getReserves()", "returns": ["uint112", "uint112", "uint32"]})
    assert out["decoded"] == [10, 20, 30]
    for bad, msg in [({"signature": "balanceOf"}, "签名格式"), ({"signature": "f(address)", "args": []}, "需要 1 个参数"),
                     ({"signature": "f(tuple)"}, "不支持的 ABI 类型"), ({"signature": "f(address)", "args": ["0x12"]}, "地址参数")]:
        with pytest.raises(SkillError, match=msg):
            run_tool("contract_read", ctx, {"address": TOKEN, **bad})


def test_event_search_and_wallet_activity():
    fake = FakeBSC(start=6000)
    for i in range(3):
        fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, i + 1), transfer(TOKEN, WALLET, POOL, 100)])
    ctx = tool_ctx(fake)
    es = run_tool("event_search", ctx, {"address": TOKEN, "event": "Transfer(address,address,uint256)", "blocks": 10})
    assert es["count"] == 6 and es["by_event"] == {"Transfer": 6}
    with pytest.raises(SkillError, match="最多查 5000"):
        run_tool("event_search", ctx, {"address": TOKEN, "from_block": 0})
    store = ChainStore(Store())
    wa = run_tool("wallet_activity", SkillContext(ctx.reader, events=store), {"address": WALLET, "blocks": 10})
    assert wa["transfers"] == 6 and wa["incoming"] == 3 and wa["outgoing"] == 3 and wa["monitored_events"] == []
    assert wa["top_counterparties"][0]["count"] == 3


def test_chain_events_tool_reads_the_listener_store():
    fake = FakeBSC()
    with pytest.raises(SkillError, match="没有运行"):
        run_tool("chain_events", tool_ctx(fake), {})
    p, store, hub = processor(fake)
    m = store.add_monitor(name="w", kind="address", target=WALLET)
    p.ingest(head(fake))
    fake.mine(logs=[transfer(TOKEN, OTHER, WALLET, 2)])
    p.ingest(head(fake))
    out = run_tool("chain_events", tool_ctx(fake, store), {"monitor_id": m["id"]})
    assert out["count"] == 1 and out["events"][0]["data"]["to"] == WALLET


def test_build_workforce_uses_the_failover_pool(tmp_path):
    from agent.workforce import build_workforce

    chain = load_chain(ROOT / "config" / "chain.example.json")
    wf = build_workforce(Store(), chain, env={})
    try:
        assert isinstance(wf.reader.rpc, RpcPool) and wf.reader.rpc.chain_id_expected == 97
        assert [e.url for e in wf.reader.rpc.endpoints] == list(chain.http_endpoints)
    finally:
        wf.stop()
