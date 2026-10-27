"""BSC realtime pipeline: new heads in, matched events out.

    WS newHeads ─┐                         ┌─> chain_blocks / chain_events (SQLite)
                 ├─> ChainProcessor.ingest ┤
    HTTP poll  ──┘   (backfill, reorg,     └─> Hub ─> /api/chain/ws ─> browser
                      match, confirm)

ChainProcessor is synchronous and does all the chain logic, so tests drive it block by block.
ChainListener owns the transport: a WebSocket subscription to newHeads with reconnects, endpoint
rotation and a stall watchdog, and HTTP polling when no WebSocket endpoint works.

Read-only: the only RPC methods used are eth_blockNumber, eth_getBlockByNumber/ByHash and eth_getLogs.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from decimal import Decimal
from typing import Any, Callable

from agent.workforce.chain import KNOWN_TOPICS, TRANSFER_TOPIC

from .hub import Hub
from .rpc import _pct, _redact
from .store import CONFIRMED, PENDING, REORGED, ChainStore


def _int(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v or 0)


def _pad(addr: str) -> str:
    return "0x" + addr.lower()[2:].rjust(64, "0")


class ChainProcessor:
    def __init__(self, rpc, store: ChainStore, hub: Hub, *, chain_id: int, network: str = "", confirmations: int = 3,
                 max_backfill: int = 200, keep_blocks: int = 512, on_event: Callable[[dict, dict, str], None] | None = None) -> None:
        self.rpc = rpc
        self.store = store
        self.hub = hub
        self.chain_id = chain_id
        self.network = network
        self.confirmations = max(0, confirmations)
        self.max_backfill = max_backfill
        self.keep_blocks = keep_blocks
        self.on_event = on_event
        self.on_block: Callable[[int], None] | None = None  # called after each stored block (autonomy watcher heartbeats)
        self.lock = threading.RLock()  # one block at a time, in order
        self.block_ms: deque[float] = deque(maxlen=1000)  # head received -> block fully processed
        self.event_ms: deque[float] = deque(maxlen=1000)  # head received -> event published
        self.counters = {"blocks": 0, "duplicates": 0, "backfilled": 0, "reorgs": 0, "reorged_events": 0, "gaps": 0, "events": 0}
        self.last_reorg: dict | None = None

    # ---- entry points ----

    def ingest(self, header: dict, received: float | None = None) -> None:
        """A new head (from newHeads or a poll). Fills any gap before it, and handles a head that replaces one we stored."""
        received = received if received is not None else time.perf_counter()
        n, h = _int(header["number"]), str(header["hash"]).lower()
        with self.lock:
            last = self.store.get("cursor")
            if self.store.block_hash(n) == h:
                self.counters["duplicates"] += 1
                return
            if last is None:
                self._process(n, received, hash_=h)
                return
            if n <= last:
                if self.store.block_hash(n) is None:  # older than anything we keep: a stale head, not a reorg
                    self.counters["duplicates"] += 1
                    return
                self._reorg(n, received)
                return
            start = last + 1
            if n - start > self.max_backfill:
                self.counters["gaps"] += 1
                self.hub.publish("gap", from_block=start, to_block=n - self.max_backfill - 1,
                                 text=f"离线太久，跳过了 {n - self.max_backfill - start} 个区块（只补最近 {self.max_backfill} 个）")
                start = n - self.max_backfill
            if start > last + 1:
                self.store.set("cursor", start - 1)
            # Walk the cursor up to n; a reorg found on the way rewinds it, and the walk carries on from there.
            while (k := self.store.get("cursor") + 1) <= n:
                if k == n:
                    self._process(n, received, hash_=h)
                    break
                if self._process(k, received):
                    self.counters["backfilled"] += 1

    def ingest_number(self, n: int) -> None:
        """Catch up to block `n` (used by polling and right after a reconnect)."""
        with self.lock:
            last = self.store.get("cursor")
            if last is not None and n <= last:
                return
            blk = self._fetch(n)
            if blk:
                self.ingest(blk)

    # ---- one block ----

    def _fetch(self, n: int | None = None, hash_: str | None = None) -> dict | None:
        full = any(m["kind"] == "address" for m in self.store.monitors(enabled_only=True))
        if hash_:
            return self.rpc.call("eth_getBlockByHash", [hash_, full])
        return self.rpc.call("eth_getBlockByNumber", [hex(n), full])

    def _process(self, n: int, received: float, hash_: str | None = None, depth: int = 0) -> bool:
        """Store block n, match its events, confirm older ones. Returns False if it found a reorg instead."""
        blk = self._fetch(n, hash_) if hash_ else self._fetch(n)
        if not blk:
            raise RuntimeError(f"节点没有返回区块 {n}")
        h, parent = blk["hash"].lower(), blk["parentHash"].lower()
        prev = self.store.block_hash(n - 1)
        if prev is not None and prev != parent:
            if depth >= 3:
                raise RuntimeError(f"区块 {n} 的父哈希连续对不上，链还在变化，稍后重试")
            self._reorg(n, received, depth + 1)
            return False
        seen_ms = time.time() * 1000
        txs = blk.get("transactions") or []
        self.store.put_block(n, h, parent, _int(blk.get("timestamp")), len(txs))
        new = self._match(n, h, txs, seen_ms)
        self.store.set("cursor", n)
        self.counters["blocks"] += 1
        self.hub.publish("block", number=n, hash=h, timestamp=_int(blk.get("timestamp")), tx_count=len(txs), events=len(new))
        for ev, mon in new:
            self.hub.publish("event", event=ev, monitor=_mon_brief(mon))
            self.event_ms.append((time.perf_counter() - received) * 1000)
            self._notify(ev, mon, PENDING)
        for ev in self.store.confirm_up_to(n - self.confirmations):
            self.hub.publish("event.confirmed", event=ev)
            mon = self.store.monitor(ev["monitor_id"])
            if mon:
                self._notify(ev, mon, CONFIRMED)
        self.store.prune_blocks(self.keep_blocks)
        if self.on_block:
            try:
                self.on_block(n)
            except Exception as e:  # a failed hook never stops the listener
                self.hub.publish("trigger.error", event_id=None, monitor_id=None, error=str(e)[:200])
        self.block_ms.append((time.perf_counter() - received) * 1000)
        return True

    def _reorg(self, n: int, received: float, depth: int = 0) -> None:
        """The node's chain no longer contains blocks we stored. Walk back to the last block both agree on,
        mark everything above it as reorged, then re-read the canonical blocks up to n."""
        old = self.store.get("cursor")
        a = n - 1
        floor = max(0, (old or n) - self.keep_blocks)
        while a >= floor:
            stored = self.store.block_hash(a)
            if stored is None:
                break
            canonical = self.rpc.call("eth_getBlockByNumber", [hex(a), False])
            if canonical and canonical["hash"].lower() == stored:
                break
            a -= 1
        removed = self.store.reorg_above(a)
        self.store.drop_blocks_above(a)
        self.store.set("cursor", a)
        self.counters["reorgs"] += 1
        self.counters["reorged_events"] += len(removed)
        self.last_reorg = {"common_ancestor": a, "old_head": old, "new_head": n, "depth": (old or a) - a, "events": len(removed),
                           "at": time.time()}
        self.hub.publish("reorg", **self.last_reorg, event_ids=[e["id"] for e in removed])
        for ev in removed:
            self.hub.publish("event.reorged", event=ev)
            mon = self.store.monitor(ev["monitor_id"])
            if mon:
                self._notify(ev, mon, REORGED)
        for k in range(a + 1, n + 1):
            if not self._process(k, received, depth=depth):
                return

    # ---- matching ----

    def _match(self, n: int, h: str, txs: list, seen_ms: float) -> list[tuple[dict, dict]]:
        mons = self.store.monitors(enabled_only=True)
        if not mons:
            return []
        out: list[tuple[dict, dict]] = []

        def add(mon: dict, kind: str, tx: str, idx: int, address: str, data: dict) -> None:
            ev = self.store.add_event(monitor_id=mon["id"], kind=kind, block=n, block_hash=h, tx_hash=tx, log_index=idx,
                                      address=address, data=data, seen_ms=seen_ms)
            if ev:
                self.counters["events"] += 1
                out.append((ev, mon))

        by_contract: dict[str, list[dict]] = {}
        for m in mons:
            if m["kind"] in ("contract", "token_transfer"):
                by_contract.setdefault(m["target"], []).append(m)
        if by_contract:
            for log in self.rpc.call("eth_getLogs", [{"blockHash": h, "address": sorted(by_contract)}]) or []:
                addr = str(log.get("address", "")).lower()
                topics = [t.lower() for t in log.get("topics") or []]
                for m in by_contract.get(addr, []):
                    if m["kind"] == "token_transfer":
                        t = _transfer(log, topics)
                        if t and int(t["value_raw"]) >= int(m["min_value"] or 0):
                            add(m, "token_transfer", log["transactionHash"], _int(log.get("logIndex")), addr, {**t, "token": addr})
                    elif not m["topics"] or (topics and topics[0] in m["topics"]):
                        add(m, "log", log["transactionHash"], _int(log.get("logIndex")), addr, _log_data(log, topics))

        watch = {m["target"]: m for m in mons if m["kind"] == "address"}
        if watch:
            pads = [_pad(a) for a in watch]
            seen: set[tuple[str, int]] = set()
            for flt in ([TRANSFER_TOPIC, pads], [TRANSFER_TOPIC, None, pads]):
                for log in self.rpc.call("eth_getLogs", [{"blockHash": h, "topics": flt}]) or []:
                    key = (log["transactionHash"], _int(log.get("logIndex")))
                    topics = [t.lower() for t in log.get("topics") or []]
                    t = _transfer(log, topics)
                    if key in seen or not t:
                        continue
                    seen.add(key)
                    for a in {t["from"], t["to"]} & watch.keys():
                        m = watch[a]
                        if int(t["value_raw"]) >= int(m["min_value"] or 0):
                            add(m, "token_transfer", key[0], key[1], str(log.get("address", "")).lower(),
                                {**t, "token": str(log.get("address", "")).lower(), "direction": "out" if t["from"] == a else "in"})
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                frm, to = str(tx.get("from") or "").lower(), str(tx.get("to") or "").lower()
                value = _int(tx.get("value"))
                for a in {frm, to} & watch.keys():
                    m = watch[a]
                    if value >= int(m["min_value"] or 0) and (value > 0 or frm == a):
                        add(m, "native_transfer", tx["hash"], -1, a, {"from": frm, "to": to or None, "value_raw": str(value),
                                                                      "value_bnb": str(Decimal(value) / Decimal(10**18)),
                                                                      "direction": "out" if frm == a else "in",
                                                                      "input": str(tx.get("input") or "0x")[:10]})
        return out

    def _notify(self, ev: dict, mon: dict, phase: str) -> None:
        if self.on_event:
            try:
                self.on_event(ev, mon, phase)
            except Exception as e:  # a failed trigger never stops the listener
                self.hub.publish("trigger.error", event_id=ev["id"], monitor_id=mon["id"], error=str(e)[:200])

    # ---- status ----

    def stats(self) -> dict:
        return {**self.counters, "cursor": self.store.get("cursor"), "confirmations": self.confirmations, "last_reorg": self.last_reorg,
                "block_ms_p50": _r(_pct(list(self.block_ms), 50)), "block_ms_p95": _r(_pct(list(self.block_ms), 95)),
                "event_ms_p95": _r(_pct(list(self.event_ms), 95)), "samples": len(self.block_ms)}


def _r(x: float) -> float:
    return round(x, 2)


def _transfer(log: dict, topics: list[str]) -> dict | None:
    if len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
        return None  # ERC-721 Transfer has the id as a 4th topic; only fungible transfers here
    data = log.get("data") or "0x"
    return {"from": "0x" + topics[1][-40:], "to": "0x" + topics[2][-40:], "value_raw": str(int(data, 16) if data not in ("0x", "") else 0)}


def _log_data(log: dict, topics: list[str]) -> dict:
    data = str(log.get("data") or "0x")
    return {"event": KNOWN_TOPICS.get(topics[0], topics[0][:10]) if topics else "anonymous", "topics": topics,
            "data": data[:514] + ("…" if len(data) > 514 else "")}


def _mon_brief(m: dict) -> dict:
    return {"id": m["id"], "name": m["name"], "kind": m["kind"], "target": m["target"]}


class ChainListener:
    """Runs the processor off a WebSocket newHeads subscription, falling back to HTTP polling.

    Reconnects with exponential backoff, rotates WebSocket endpoints, and reconnects when no head arrives
    for `stall_s` (a silently dead socket). After every (re)connect it catches up over HTTP, so blocks
    produced while it was disconnected are backfilled rather than lost."""

    def __init__(self, processor: ChainProcessor, rpc, ws_urls: list[str] | tuple[str, ...] = (), *, poll_s: float = 3.0,
                 stall_s: float = 30.0, backoff_s: tuple[float, float] = (1.0, 30.0), poll_before_retry_s: float = 60.0,
                 connect: Callable | None = None) -> None:
        self.p = processor
        self.rpc = rpc
        self.ws_urls = [u for u in ws_urls if u]
        self.poll_s = poll_s
        self.stall_s = stall_s
        self.backoff = backoff_s
        self.poll_before_retry_s = poll_before_retry_s
        self._connect = connect
        self.state = {"transport": "stopped", "connected": False, "endpoint": None, "reconnects": 0, "last_head": None,
                      "last_head_at": None, "last_error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=lambda: asyncio.run(self.run()), name="bsc-listener", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:  # pragma: no cover - the loop already closed
                pass
        if self._thread:
            self._thread.join(timeout)
        self._set(transport="stopped", connected=False)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        failures, i = 0, 0
        while not self._stop.is_set():
            if self.ws_urls:
                url = self.ws_urls[i % len(self.ws_urls)]
                try:
                    await self._ws_session(url)
                    failures = 0
                except Exception as e:  # dropped, refused, stalled, bad answer
                    failures += 1
                    self._set(connected=False, last_error=f"{type(e).__name__}: {str(e)[:160]}")
                    self.p.hub.publish("status", **self.status())
                if self._stop.is_set():
                    break
                i += 1
                self.state["reconnects"] += 1
                if failures >= 2 * len(self.ws_urls):
                    # Every WebSocket endpoint failed twice in a row: poll over HTTP for a while, then try again.
                    await self._poll(until=time.monotonic() + self.poll_before_retry_s)
                    failures = 0
                else:
                    await self._sleep(min(self.backoff[1], self.backoff[0] * 2 ** max(0, failures - 1)))
            else:
                await self._poll(until=None)

    # ---- transports ----

    async def _ws_session(self, url: str) -> None:
        connect = self._connect
        if connect is None:  # pragma: no cover - real network
            import websockets

            connect = lambda u: websockets.connect(u, open_timeout=10, ping_interval=20, ping_timeout=20, max_size=2**22)  # noqa: E731
        async with connect(url) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if "error" in reply or not reply.get("result"):
                raise ConnectionError(f"订阅失败：{reply.get('error')}")
            sub = reply["result"]
            self._set(transport="websocket", connected=True, endpoint=_redact(url), last_error=None)
            self.p.hub.publish("status", **self.status())
            await self._catch_up()
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), self.stall_s)
                except asyncio.TimeoutError:
                    raise ConnectionError(f"{self.stall_s:.0f} 秒没有新区块，重新连接") from None
                received = time.perf_counter()
                msg = json.loads(raw)
                params = msg.get("params") or {}
                if msg.get("method") != "eth_subscription" or params.get("subscription") != sub:
                    continue
                head = params["result"]
                await asyncio.to_thread(self.p.ingest, head, received)
                self._set(last_head=_int(head["number"]), last_head_at=time.time())

    async def _poll(self, until: float | None) -> None:
        self._set(transport="http-poll", connected=True, endpoint=_redact(getattr(self.rpc, "url", "")))
        self.p.hub.publish("status", **self.status())
        while not self._stop.is_set() and (until is None or time.monotonic() < until):
            try:
                await self._catch_up()
                self._set(connected=True, last_error=None)
            except Exception as e:
                self._set(connected=False, last_error=f"{type(e).__name__}: {str(e)[:160]}")
            await self._sleep(self.poll_s)

    async def _catch_up(self) -> None:
        n = int(await asyncio.to_thread(self.rpc.call, "eth_blockNumber", []), 16)
        await asyncio.to_thread(self.p.ingest_number, n)
        cursor = self.p.store.get("cursor")
        if cursor is not None:
            self._set(last_head=cursor, last_head_at=time.time())

    async def _sleep(self, s: float) -> None:
        end = time.monotonic() + s
        while not self._stop.is_set() and time.monotonic() < end:
            await asyncio.sleep(min(0.2, end - time.monotonic()))

    # ---- status ----

    def _set(self, **f: Any) -> None:
        self.state.update(f)

    def status(self) -> dict:
        s = dict(self.state)
        if s["last_head_at"]:
            s["head_age_s"] = round(time.time() - s["last_head_at"], 1)
        return s
