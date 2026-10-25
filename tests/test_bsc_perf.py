"""Latency of the BSC data layer's own code, measured against a local fake node (no network in the path).

These numbers cover what KAGOU controls: cache lookups, processing one block, and delivery from the hub to a
browser WebSocket. They are not end-to-end numbers on a real BSC endpoint; RPC round-trips to a real node
add their own latency on top. Each test writes its measurements to KAGOU_PERF_OUT when set.
"""

from __future__ import annotations

import json
import os
import statistics
import time

from fastapi.testclient import TestClient

from agent.bsc.rpc import RpcPool
from agent.bsc.rpc import _pct as pct
from test_bsc import H, OTHER, POOL, SWAP_TOPIC, TOKEN, TOKEN_AUTH, WALLET, FakeBSC, gateway, head, pad, processor, transfer  # noqa: F401


def report(name: str, samples: list[float], target_ms: float) -> dict:
    row = {"name": name, "n": len(samples), "p50_ms": round(pct(samples, 50), 3), "p95_ms": round(pct(samples, 95), 3),
           "max_ms": round(max(samples), 3), "mean_ms": round(statistics.fmean(samples), 3), "target_p95_ms": target_ms}
    out = os.environ.get("KAGOU_PERF_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    print(row)
    return row


def test_cache_hit_p95_under_50ms():
    fake = FakeBSC()
    p = RpcPool(["http://a"], post=fake, sleep=lambda s: None)
    p.call("eth_chainId", [])
    samples = []
    for _ in range(2000):
        t = time.perf_counter()
        p.call("eth_chainId", [])
        samples.append((time.perf_counter() - t) * 1000)
    assert report("rpc cache hit", samples, 50)["p95_ms"] < 50


def test_block_processing_p95_under_50ms():
    """One block with 200 transactions and 60 logs, five monitors of every kind, fake RPC answering in-process."""
    fake = FakeBSC()
    p, store, hub = processor(fake, confirmations=3)
    store.add_monitor(name="w1", kind="address", target=WALLET)
    store.add_monitor(name="w2", kind="address", target=OTHER)
    store.add_monitor(name="t", kind="token_transfer", target=TOKEN, min_value="10")
    store.add_monitor(name="c", kind="contract", target=POOL, topics=[SWAP_TOPIC])
    store.add_monitor(name="c2", kind="contract", target=POOL)
    p.ingest(head(fake))
    samples = []
    for i in range(60):
        txs = [{"from": WALLET if j % 50 == 0 else "0x" + f"{j:040x}", "to": OTHER if j % 70 == 0 else "0x" + f"{j + 1:040x}", "value": j}
               for j in range(200)]
        logs = [transfer(TOKEN, OTHER, WALLET, j) for j in range(30)] + [
            {"address": POOL, "topics": [SWAP_TOPIC, pad(OTHER)], "data": "0x" + "00" * 64} for _ in range(30)]
        fake.mine(txs=txs, logs=logs)
        t = time.perf_counter()
        p.ingest(head(fake), received=t)
        samples.append((time.perf_counter() - t) * 1000)
    assert p.counters["events"] > 0
    assert report("process one block (200 tx, 60 logs, 5 monitors)", samples[5:], 50)["p95_ms"] < 50


def test_event_to_browser_p95_under_150ms(gateway):
    """Hub publish (listener thread) -> FastAPI WebSocket -> client receive."""
    c, svc, fake = gateway
    samples = []
    with c.websocket_connect("/api/chain/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN_AUTH, "topics": ["event"]})
        assert ws.receive_json()["type"] == "hello"
        for i in range(200):
            t = time.perf_counter()
            svc.hub.publish("event", event={"id": i}, sent=t)
            msg = ws.receive_json()
            samples.append((time.perf_counter() - msg["sent"]) * 1000)
    assert report("hub -> browser websocket", samples, 150)["p95_ms"] < 150
