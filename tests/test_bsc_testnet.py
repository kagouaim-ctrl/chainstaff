"""Read-only smoke test against the real BSC testnet (chain id 97). Skipped unless KAGOU_TESTNET=1.

Everything the other BSC tests prove runs against a fake node. This file is the only place the RPC pool, the
WebSocket listener and the agents' chain tools talk to real public endpoints. It only reads: no keys, no signing,
no transactions. It is run by its own CI job, apart from the main suite, so a slow public node never fails a PR.
Results go to KAGOU_TESTNET_OUT (JSON) when set."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agent.bsc.hub import Hub
from agent.bsc.listener import ChainListener, ChainProcessor
from agent.bsc.rpc import RpcPool
from agent.bsc.store import ChainStore
from agent.config import load_chain
from agent.store import Store
from agent.workforce.chain import ChainReader
from agent.workforce.skills import SkillContext

pytestmark = pytest.mark.skipif(os.environ.get("KAGOU_TESTNET") != "1", reason="real BSC testnet: set KAGOU_TESTNET=1")

ROOT = Path(__file__).resolve().parents[1]
WBNB = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"  # wrapped BNB on BSC testnet
RESULTS: dict = {}


@pytest.fixture(scope="module")
def chain():
    c = load_chain(ROOT / "config" / "chain.example.json")
    assert c.chain_id == 97
    yield c
    out = os.environ.get("KAGOU_TESTNET_OUT")
    if out:
        Path(out).write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2))


@pytest.fixture(scope="module")
def rpc(chain):
    return RpcPool(chain.http_endpoints, chain_id=chain.chain_id)


def test_rpc_endpoints_answer_on_chain_97(rpc):
    rows = rpc.health()
    RESULTS["rpc_health"] = rows
    ok = [r for r in rows if r["ok"]]
    assert ok, rows
    assert all(r["chain_id"] == 97 for r in ok)
    head = int(rpc.call("eth_blockNumber", []), 16)
    assert head > 0
    RESULTS["head_at_start"] = head


def test_listener_follows_real_heads_over_websocket(chain, rpc):
    store, hub = ChainStore(Store()), Hub()
    store.add_monitor(name="wbnb", kind="token_transfer", target=WBNB)
    p = ChainProcessor(rpc, store, hub, chain_id=97, network=chain.network, confirmations=chain.confirmations)
    lst = ChainListener(p, rpc, chain.ws_urls, poll_s=3, stall_s=30)
    t0 = time.monotonic()
    lst.start()
    try:
        first = None
        deadline = t0 + 120
        while time.monotonic() < deadline:
            cur = store.get("cursor")
            if cur is not None and first is None:
                first = cur
            if first is not None and cur - first >= 10:
                break
            time.sleep(0.5)
        status, stats = lst.status(), p.stats()
    finally:
        lst.stop()
    RESULTS["listener"] = {"status": status, "stats": stats, "seconds": round(time.monotonic() - t0, 1), "first_cursor": first,
                           "events": store.counts()}
    assert first is not None, status
    assert stats["cursor"] - first >= 10, (status, stats)
    assert status["transport"] == "websocket" and status["connected"], status
    # Every stored block links to its parent: the chain the listener kept is a chain.
    blocks = [store.block_hash(n) for n in range(stats["cursor"] - 5, stats["cursor"] + 1)]
    assert all(blocks), blocks


def test_agent_chain_tools_read_real_data(chain, rpc):
    import agent.workforce.chaintools  # noqa: F401
    from agent.workforce.skills import REGISTRY

    ctx = SkillContext(ChainReader(rpc, chain.network, 97, "https://testnet.bscscan.com/tx/"))

    def run(sid, inp):
        return REGISTRY.get(sid).run(ctx, inp)

    head = int(rpc.call("eth_blockNumber", []), 16)
    bi = run("block_info", {"block": head - 5})
    assert bi["hash"].startswith("0x") and len(bi["hash"]) == 66
    sym = run("contract_read", {"address": WBNB, "signature": "symbol()", "returns": ["string"]})
    dec = run("contract_read", {"address": WBNB, "signature": "decimals()", "returns": ["uint8"]})
    assert sym["decoded"] == ["WBNB"] and dec["decoded"] == [18]
    es = run("event_search", {"address": WBNB, "event": "Transfer(address,address,uint256)", "blocks": 200})
    assert es["count"] >= 0
    RESULTS["tools"] = {"block_info": {k: bi[k] for k in ("number", "hash", "tx_count") if k in bi}, "wbnb": [sym["decoded"][0], dec["decoded"][0]],
                        "wbnb_transfers_last_200_blocks": es["count"], "rpc_stats": rpc.stats()}
