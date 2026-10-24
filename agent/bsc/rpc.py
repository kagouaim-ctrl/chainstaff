"""BSC JSON-RPC over several HTTP endpoints: timeouts, retries, failover, rate limits, health and a small cache.

Drop-in for RpcClient (same call() and helpers), so the workforce reader, the anchor and the old finance
code all get failover without changing. Reads only, apart from the one write the anchor makes after a
human approval: eth_sendRawTransaction is sent once to one endpoint and never retried or cached.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.execution.rpc import RpcClient

NO_RETRY = frozenset({"eth_sendRawTransaction"})
NO_CACHE = frozenset({"eth_sendRawTransaction", "eth_estimateGas", "eth_getTransactionCount", "eth_subscribe", "eth_unsubscribe"})
# JSON-RPC errors that mean "this node is busy or limited", worth trying another node for.
THROTTLE_CODES = frozenset({-32005, -32029, 429})
THROTTLE_WORDS = ("rate limit", "limit exceeded", "too many requests", "exceeded the quota", "capacity")


class RpcError(RuntimeError):
    """The node answered with a JSON-RPC error (reverted call, bad params). Retrying elsewhere would not help."""

    def __init__(self, method: str, error: Any) -> None:
        super().__init__(f"RPC {method} failed: {error}")
        self.error = error


class RpcUnavailable(ConnectionError):
    """No endpoint answered within the retries. A ConnectionError, so callers retry it like any network failure
    (and ChainReader.eth_call does not mistake it for a reverted call)."""


class RateLimiter:
    """Token bucket: `rate` requests per second, bursts up to `burst`."""

    def __init__(self, rate: float, burst: int | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate
        self.burst = burst or max(1, int(rate))
        self.tokens = float(self.burst)
        self.clock = clock
        self.at = clock()
        self.lock = threading.Lock()

    def wait_time(self) -> float:
        """Take a token; return how long the caller must wait first (0 if one was free)."""
        with self.lock:
            now = self.clock()
            self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
            self.at = now
            self.tokens -= 1
            return 0.0 if self.tokens >= 0 else -self.tokens / self.rate


@dataclass
class Endpoint:
    url: str
    limiter: RateLimiter
    down_until: float = 0.0
    failures: int = 0
    calls: int = 0
    errors: int = 0
    latency_ms: float | None = None  # moving average of successful calls
    last_error: str | None = None
    last_ok: float | None = None
    samples: list[float] = field(default_factory=list)

    def record(self, ms: float) -> None:
        self.latency_ms = ms if self.latency_ms is None else self.latency_ms * 0.8 + ms * 0.2
        self.samples.append(ms)
        del self.samples[:-200]

    def public(self, now: float) -> dict:
        ok = now >= self.down_until
        return {"url": _redact(self.url), "healthy": ok, "cooldown_s": 0 if ok else round(self.down_until - now, 1), "calls": self.calls,
                "errors": self.errors, "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
                "p95_ms": round(_pct(self.samples, 95), 1) if self.samples else None, "last_error": self.last_error}


class TTLCache:
    def __init__(self, capacity: int = 5000, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = capacity
        self.clock = clock
        self.data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> tuple[bool, Any]:
        with self.lock:
            v = self.data.get(key)
            if v is None or v[0] < self.clock():
                if v is not None:
                    del self.data[key]
                self.misses += 1
                return False, None
            self.data.move_to_end(key)
            self.hits += 1
            return True, v[1]

    def put(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        with self.lock:
            self.data[key] = (self.clock() + ttl, value)
            self.data.move_to_end(key)
            while len(self.data) > self.capacity:
                self.data.popitem(last=False)


def cache_ttl(method: str, params: list, result: Any) -> float:
    """How long an answer can be reused. Only what a reorg cannot change is kept for long (data addressed by
    block hash); anything addressed by block number or "latest" lives under a block time or is not cached,
    so the listener never re-reads a block from before a reorg."""
    if method in NO_CACHE or result is None:
        return 0
    if method == "eth_chainId":
        return 3600
    if method == "eth_getBlockByHash":
        return 300
    if method == "eth_getLogs":
        return 300 if params and isinstance(params[0], dict) and params[0].get("blockHash") else 0
    if method in ("eth_getTransactionByHash", "eth_getTransactionReceipt"):
        return 3  # a reorg can move a transaction to another block
    if method == "eth_getBlockByNumber":
        return 0.5 if params and params[0] in ("latest", "pending", "safe", "finalized") else 0
    if method in ("eth_blockNumber", "eth_gasPrice"):
        return 0.5
    if method in ("eth_getCode", "eth_getStorageAt", "eth_call", "eth_getBalance"):
        return 1.0
    return 0


class RpcPool(RpcClient):
    def __init__(self, urls: list[str] | tuple[str, ...], *, chain_id: int | None = None, timeout_s: float = 8.0, retries: int = 2,
                 rate_per_s: float = 25.0, cooldown_s: float = 15.0, post: Callable[[str, dict, float], Any] | None = None,
                 cache: TTLCache | None = None, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        urls = [u for u in dict.fromkeys(urls) if u]
        if not urls:
            raise ValueError("至少需要一个 RPC 地址")
        if post is None:  # pragma: no cover - real network
            import httpx

            client = httpx.Client(timeout=timeout_s)
            post = lambda u, body, t: client.post(u, json=body, timeout=t).json()  # noqa: E731
        super().__init__(urls[0], post=lambda u, body: post(u, body, timeout_s))
        self._post = post
        self.endpoints = [Endpoint(u, RateLimiter(rate_per_s, clock=clock)) for u in urls]
        self.chain_id_expected = chain_id
        self.timeout_s = timeout_s
        self.retries = retries
        self.cooldown_s = cooldown_s
        self.cache = cache if cache is not None else TTLCache(clock=clock)
        self.clock = clock
        self.sleep = sleep
        self._rr = 0
        self._lock = threading.Lock()
        self._ids = 0

    # ---- calls ----

    def call(self, method: str, params: list) -> Any:
        key = method + json.dumps(params, sort_keys=True, default=str)
        if method not in NO_CACHE:
            hit, val = self.cache.get(key)
            if hit:
                return val
        tries = 1 if method in NO_RETRY else self.retries + 1
        last: Exception | None = None
        for attempt in range(tries):
            ep = self._pick(exclude_down=attempt < len(self.endpoints))
            wait = ep.limiter.wait_time()
            if wait > 0:
                self.sleep(min(wait, 2.0))
            try:
                result = self._send(ep, method, params)
            except RpcError:
                raise
            except Exception as e:  # timeout, connection, throttled, malformed answer: try the next endpoint
                last = e
                self._fail(ep, e)
                if attempt + 1 < tries:
                    self.sleep(min(0.25 * 2 ** attempt, 2.0))
                continue
            ttl = cache_ttl(method, params, result)
            if ttl:
                self.cache.put(key, result, ttl)
            return result
        raise RpcUnavailable(f"RPC {method}: 所有节点都没有响应（{type(last).__name__}: {str(last)[:160]}）")

    def _send(self, ep: Endpoint, method: str, params: list) -> Any:
        with self._lock:
            self._ids += 1
            rid = self._ids
        t0 = time.perf_counter()
        ep.calls += 1
        res = self._post(ep.url, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, self.timeout_s)
        if not isinstance(res, dict):
            raise ValueError(f"非 JSON-RPC 响应：{str(res)[:80]}")
        if "error" in res:
            err = res["error"]
            code = err.get("code") if isinstance(err, dict) else None
            text = json.dumps(err).lower()
            if code in THROTTLE_CODES or any(w in text for w in THROTTLE_WORDS):
                raise ConnectionError(f"节点限流：{str(err)[:160]}")
            ep.record((time.perf_counter() - t0) * 1000)  # the node is up, the request was wrong
            raise RpcError(method, err)
        if "result" not in res:
            raise ValueError("响应里没有 result")
        ep.record((time.perf_counter() - t0) * 1000)
        ep.failures, ep.last_ok = 0, self.clock()
        return res["result"]

    def _pick(self, exclude_down: bool) -> Endpoint:
        now = self.clock()
        with self._lock:
            up = [e for e in self.endpoints if now >= e.down_until] if exclude_down else []
            pool = up or self.endpoints  # all down: try anyway rather than fail without asking
            # Prefer the fastest known endpoint, rotating among endpoints with no measurement yet.
            measured = sorted((e for e in pool if e.latency_ms is not None), key=lambda e: e.latency_ms)
            fresh = [e for e in pool if e.latency_ms is None]
            if fresh and (not measured or self._rr % 4 == 0):
                ep = fresh[self._rr % len(fresh)]
            else:
                ep = (measured or pool)[0]
            self._rr += 1
            return ep

    def _fail(self, ep: Endpoint, e: Exception) -> None:
        with self._lock:
            ep.errors += 1
            ep.failures += 1
            ep.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            if ep.failures >= 2 or isinstance(e, ConnectionError):
                ep.down_until = self.clock() + self.cooldown_s * min(4, ep.failures)

    # ---- health ----

    def health(self) -> list[dict]:
        """Ask every endpoint directly for its chain id and head; marks the ones that fail or are on the wrong chain."""
        out = []
        for ep in self.endpoints:
            row = {"url": _redact(ep.url)}
            t0 = time.perf_counter()
            try:
                cid = int(self._send(ep, "eth_chainId", []), 16)
                head = int(self._send(ep, "eth_blockNumber", []), 16)
                row.update(ok=True, chain_id=cid, block=head, latency_ms=round((time.perf_counter() - t0) * 1000, 1))
                if self.chain_id_expected is not None and cid != self.chain_id_expected:
                    row.update(ok=False, error=f"链 ID 是 {cid}，配置的是 {self.chain_id_expected}")
                    ep.down_until = self.clock() + 3600
            except Exception as e:
                self._fail(ep, e)
                row.update(ok=False, error=f"{type(e).__name__}: {str(e)[:160]}")
            out.append(row)
        return out

    def stats(self) -> dict:
        now = self.clock()
        return {"endpoints": [e.public(now) for e in self.endpoints], "cache": {"hits": self.cache.hits, "misses": self.cache.misses,
                                                                                "size": len(self.cache.data)}}


def _redact(url: str) -> str:
    """Endpoint URLs often carry an API key in the path or query; never show it."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    path = p.path if len(p.path) <= 12 else p.path[:6] + "…"
    return f"{p.scheme}://{p.hostname or ''}{':' + str(p.port) if p.port else ''}{path}{'?…' if p.query else ''}"


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(round(q / 100 * (len(s) - 1)))))
    return s[k]
