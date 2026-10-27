"""The BSC realtime data layer as one object: RPC pool, event store, processor, listener, hub, and the
monitors that start missions. Built from the chain settings and attached to the workforce, so agents'
chain tools and the console both read from it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from agent.config import ChainConfig
from agent.rules.models import ADDRESS_RE

from .hub import Hub
from .listener import ChainListener, ChainProcessor
from .rpc import RpcPool
from .store import CONFIRMED, MONITOR_KINDS, PENDING, ChainStore

DEFAULT_GOAL = "链上监控「{monitor}」发现新事件：{kind}，交易 {tx_hash}（区块 {block}），{from} → {to}，数量 {value}。请分析这笔交易和相关地址，给出结论。"
TRIGGER_BY = "chain-monitor"


class ChainService:
    def __init__(self, store, chain: ChainConfig, wf=None, *, rpc=None, connect: Callable | None = None,
                 poll_s: float = 3.0, stall_s: float = 30.0) -> None:
        self.chain = chain
        self.wf = wf
        self.rpc = rpc or (wf.reader.rpc if wf is not None and wf.reader is not None else None) or RpcPool(chain.http_endpoints, chain_id=chain.chain_id)
        self.events = ChainStore(store)
        self.hub = Hub()
        self.processor = ChainProcessor(self.rpc, self.events, self.hub, chain_id=chain.chain_id, network=chain.network,
                                        confirmations=chain.confirmations, on_event=self._trigger)
        self.listener = ChainListener(self.processor, self.rpc, chain.ws_urls, poll_s=poll_s, stall_s=stall_s, connect=connect)
        self.autonomy = None  # agent.autonomy.service.Autonomy: bsc_events watchers' monitors route there
        if wf is not None:
            wf.events = self.events

    def attach_autonomy(self, at) -> None:
        self.autonomy = at
        at.watchers.chain = self
        self.processor.on_block = at.watchers.chain_heartbeat

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()

    # ---- monitors ----

    def add_monitor(self, *, name: str, kind: str, target: str, topics: list[str] | None = None, min_value: Any = "0",
                    trigger: dict | None = None, enabled: bool = True) -> dict:
        self._check(kind, target, topics, min_value, trigger)
        return self.events.add_monitor(name=(name or "").strip()[:80] or f"{MONITOR_KINDS[kind]} {target[:10]}", kind=kind, target=target,
                                       topics=topics, min_value=str(int(min_value or 0)), trigger=trigger, enabled=enabled)

    def update_monitor(self, mid: str, **f: Any) -> dict:
        cur = self.events.monitor(mid)
        if not cur:
            raise ValueError("监控不存在")
        merged = {**cur, **{k: v for k, v in f.items() if v is not None}}
        self._check(merged["kind"], merged["target"], merged["topics"], merged["min_value"], merged["trigger"])
        if "min_value" in f and f["min_value"] is not None:
            f["min_value"] = str(int(f["min_value"]))
        if "name" in f and f["name"] is not None:
            f["name"] = f["name"].strip()[:80] or cur["name"]
        return self.events.update_monitor(mid, **{k: v for k, v in f.items() if v is not None})

    def _check(self, kind: str, target: str, topics, min_value, trigger: dict | None) -> None:
        if kind not in MONITOR_KINDS:
            raise ValueError(f"监控类型只能是 {', '.join(MONITOR_KINDS)}")
        if not ADDRESS_RE.fullmatch(str(target or "").strip()):
            raise ValueError(f"地址格式不对：{target}")
        for t in topics or []:
            if not (isinstance(t, str) and len(t) == 66 and t.startswith("0x")):
                raise ValueError(f"事件 topic 应该是 0x 开头的 32 字节哈希：{t}")
        try:
            if int(min_value or 0) < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("最小数量应该是非负整数（链上原始单位）") from None
        if trigger and trigger.get("watcher_id"):
            return  # owned by an autonomy watcher, which checked its own workspace
        if trigger:
            if self.wf is None:
                raise ValueError("没有工作台，不能设置自动任务")
            if trigger.get("on", CONFIRMED) not in (PENDING, CONFIRMED):
                raise ValueError("自动任务只能在 pending 或 confirmed 时触发")
            self.wf.workspace_or_404(trigger.get("workspace_id") or "")

    # ---- monitor -> mission ----

    def _trigger(self, ev: dict, mon: dict, phase: str) -> None:
        trig = mon.get("trigger") or {}
        if trig.get("watcher_id"):
            if self.autonomy is not None:
                self.autonomy.events.chain_event(ev, mon, phase)
            return
        if self.wf is None or not trig.get("workspace_id") or trig.get("on", CONFIRMED) != phase:
            return
        d = ev["data"]
        fields = defaultdict(lambda: "—", monitor=mon["name"], kind=ev["kind"], tx_hash=ev["tx_hash"], block=ev["block"],
                             address=ev["address"], token=d.get("token", ""), value=d.get("value_bnb") or d.get("value_raw") or "—",
                             event=d.get("event", ""), **{"from": d.get("from") or "—", "to": d.get("to") or "—"})
        text = (trig.get("goal") or DEFAULT_GOAL).format_map(fields)
        # One mission per event, even if the same event is seen again after a restart or a reconnect.
        task = self.wf.submit_goal(trig["workspace_id"], text, TRIGGER_BY, idempotency_key=f"chain-event:{ev['id']}",
                                   budget_usd=trig.get("budget_usd", "0.5"), squad_id=trig.get("squad_id"))
        self.events.set_event_task(ev["id"], task["id"])
        self.hub.publish("trigger", event_id=ev["id"], monitor_id=mon["id"], task_id=task["id"], replayed=bool(task.get("replayed")))

    # ---- status ----

    def status(self) -> dict:
        head = self.events.head()
        return {"enabled": True, "network": self.chain.network, "chain_id": self.chain.chain_id, "listener": self.listener.status(),
                "processor": self.processor.stats(), "rpc": self.rpc.stats() if hasattr(self.rpc, "stats") else None,
                "hub": self.hub.stats(), "events": self.events.counts(), "head": head, "monitors": len(self.events.monitors()),
                "read_only": True}
