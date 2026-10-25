"""In-process fan-out from the listener thread to every connected browser.

Each message gets a sequence number and stays in a short ring buffer, so a browser that reconnects with
`since=<last seq>` gets what it missed instead of a gap. A slow browser never blocks the listener: its
queue is bounded and, once full, it is told to resync and dropped.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any


class Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int, topics: set[str] | None) -> None:
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.topics = topics
        self.overflowed = False

    def wants(self, msg: dict) -> bool:
        return self.topics is None or msg["type"] in self.topics or msg["type"] == "resync"

    def _put(self, msg: dict) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.overflowed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync", "seq": msg["seq"], "reason": "too slow"})


class Hub:
    def __init__(self, buffer: int = 2000, queue_size: int = 1000) -> None:
        self.seq = 0
        self.buffer: deque[dict] = deque(maxlen=buffer)
        self.subs: set[Subscriber] = set()
        self.queue_size = queue_size
        self.lock = threading.Lock()
        self.published = 0

    def publish(self, type_: str, **payload: Any) -> dict:
        """Safe to call from any thread."""
        with self.lock:
            self.seq += 1
            msg = {"type": type_, "seq": self.seq, "ts_ms": time.time() * 1000, **payload}
            self.buffer.append(msg)
            subs = list(self.subs)
            self.published += 1
        for s in subs:
            if s.wants(msg):
                try:
                    s.loop.call_soon_threadsafe(s._put, msg)
                except RuntimeError:  # its event loop is gone
                    self.unsubscribe(s)
        return msg

    def subscribe(self, loop: asyncio.AbstractEventLoop, since: int | None = None, topics: set[str] | None = None) -> tuple[Subscriber, list[dict]]:
        """Register a browser; returns it and the buffered messages after `since` (all of them if the gap is too old)."""
        s = Subscriber(loop, self.queue_size, topics)
        with self.lock:
            self.subs.add(s)
            backlog = [m for m in self.buffer if since is not None and m["seq"] > since and s.wants(m)]
            gap = since is not None and self.buffer and since < self.buffer[0]["seq"] - 1
        if gap:
            backlog.insert(0, {"type": "resync", "seq": since, "reason": "gap"})
        return s, backlog

    def unsubscribe(self, s: Subscriber) -> None:
        with self.lock:
            self.subs.discard(s)

    def stats(self) -> dict:
        return {"seq": self.seq, "subscribers": len(self.subs), "buffered": len(self.buffer)}
