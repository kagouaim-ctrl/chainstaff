"""The cloud worker: the process that keeps agents working after every browser is closed.

    python -m agent.autonomy.worker            (or the supervisor, which also runs the API server and restarts both)

Each tick (about once a second) it:
    heartbeat        writes its row in at_workers; the console shows Offline when no worker has written recently
    leases           renews the lease on each task it runs; a task whose lease ran out belonged to a worker that died:
                     it goes back to the queue with its checkpoint (or fails after max attempts)
    events           routes received events to missions (retries with backoff, missed ones stay received until routed)
    schedules        starts scheduled missions whose cron came due
    deadlines        fails tasks past their deadline
    queue            claims queued tasks, respecting the global and the per-agent concurrency, and runs them in threads

API health checks run on their own thread so a slow endpoint never holds the queue. No model is called unless a
task is running; an idle worker costs nothing.
"""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

from . import constants as C
from .db import iso, now_dt

if TYPE_CHECKING:  # pragma: no cover
    from .service import Autonomy

VERSION = "autonomy-1"


class Worker:
    def __init__(self, svc: "Autonomy", *, worker_id: str | None = None, concurrency: int | None = None) -> None:
        self.svc = svc
        self.db = svc.db
        self.id = worker_id or f"wkr-{socket.gethostname()[:20]}-{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency or svc.limits.worker_concurrency
        self.pool = ThreadPoolExecutor(self.concurrency, thread_name_prefix="agent")
        self.running: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # -inf, not 0: time.monotonic() can be small on a freshly booted machine, and both must run on the first tick.
        self._last_beat = float("-inf")
        self._last_prune = float("-inf")
        self.stats = {"ticks": 0, "runs": 0, "recovered": 0, "events": 0, "errors": 0}
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ lifecycle

    def start(self, interval_s: float = 1.0) -> None:
        self.db.heartbeat(self.id, host=socket.gethostname()[:64], pid=os.getpid(), version=VERSION, concurrency=self.concurrency, running=[], stats=self.stats)
        self.svc.activity.log("worker.started", f"Worker {self.id} 启动（并发 {self.concurrency}）", actor=self.id,
                              data={"pid": os.getpid(), "sandbox": self.svc.sandbox.info(), "models": [p["provider"] for p in self.svc.model_status()["providers"]]})
        self.recover()
        for name, fn, every in (("worker-loop", self.tick, interval_s), ("worker-health", self.svc.watchers.poll_health, 2.0)):
            t = threading.Thread(target=self._forever, args=(fn, every), name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def _forever(self, fn, every: float) -> None:
        while not self._stop.is_set():
            try:
                fn()
            except Exception as e:  # the loop must survive anything; the error is in Activity
                self.stats["errors"] += 1
                try:
                    self.svc.activity.log("worker.error", f"Worker 循环出错：{type(e).__name__}: {str(e)[:300]}", severity="medium", actor=self.id)
                except Exception:  # pragma: no cover - the database itself is gone
                    pass
            self._stop.wait(every)

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self.pool.shutdown(wait=wait, cancel_futures=True)
        # Tasks still running keep their lease until it expires; the next worker resumes them from the checkpoint.
        self.db.stop_worker(self.id)
        self.svc.activity.log("worker.stopped", f"Worker {self.id} 停止", actor=self.id, data=self.stats)

    # ------------------------------------------------------------------ one tick

    def tick(self) -> None:
        self.stats["ticks"] += 1
        self._reap()
        mono = time.monotonic()
        if mono - self._last_beat >= self.svc.limits.heartbeat_s:
            self._last_beat = mono
            with self._lock:
                ids = list(self.running)
            self.db.renew_leases(self.id, ids, self.svc.limits.lease_s)
            self.db.heartbeat(self.id, running=ids, stats=self.stats)
        self.recover()
        self.stats["events"] += self.svc.events.process_due()
        self.svc.missions.due_schedules()
        self.svc.wakeup.enforce_deadlines()
        self.dispatch()
        if mono - self._last_prune > 3600:
            self._last_prune = mono
            with self._lock:
                keep = set(self.running)
            keep |= {t["id"] for t in self.db.tasks(status=list(C.TASK_OPEN), limit=1000)}
            self.svc.sandbox.prune(keep)

    def _reap(self) -> None:
        with self._lock:
            for tid, f in list(self.running.items()):
                if f.done():
                    del self.running[tid]

    def recover(self) -> int:
        """Tasks whose worker stopped renewing the lease (crash, OOM, redeploy) go back to the queue."""
        n = 0
        for t in self.db.expired_leases():
            with self._lock:
                if t["id"] in self.running:
                    continue
            dead = t["lease_owner"]
            for r in self.db.runs(task_id=t["id"], status=[C.R_RUNNING], limit=5):
                self.db.update_run(r["id"], status=C.R_INTERRUPTED, finished_at=iso(now_dt()), error=f"Worker {dead} 没有续租（崩溃或重启）")
            if t["attempts"] < t["max_attempts"]:
                if self.db.transition(t["id"], (C.T_RUNNING,), C.T_QUEUED, lease_owner=None, lease_until=None):
                    n += 1
                    self.svc.activity.log("worker.recovered", f"恢复中断的任务：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                                          task_id=t["id"], agent_id=t["to_agent_id"], actor=self.id, severity="low",
                                          reason=f"Worker {dead} 的租约在 {t['lease_until']} 过期", result="放回队列" + ("，从检查点继续" if t.get("checkpoint") else ""))
            elif self.db.transition(t["id"], (C.T_RUNNING,), C.T_FAILED, error=f"Worker 中断 {t['attempts']} 次，不再重试", lease_owner=None, lease_until=None,
                                    finished_at=iso(now_dt())):
                n += 1
                self.svc.activity.log("task.failed", f"任务多次中断后失败：{t['objective'][:120]}", workspace_id=t["workspace_id"], mission_id=t["mission_id"],
                                      task_id=t["id"], agent_id=t["to_agent_id"], actor=self.id, severity="medium", needs_human=True)
                self.svc.activity.notify(f"任务失败：{t['objective'][:80]}", "执行这个任务的 Worker 多次中断。", severity="medium", agent_id=t["to_agent_id"],
                                         workspace_id=t["workspace_id"], task_id=t["id"], dedupe_key=f"interrupted:{t['id']}")
                self.svc.wakeup.finished(self.db.task(t["id"]))
        self.stats["recovered"] += n
        return n

    def dispatch(self) -> int:
        """Claim what fits: free worker slots, and each agent under its own concurrency."""
        started = 0
        with self._lock:
            free = self.concurrency - len(self.running)
        if free <= 0:
            return 0
        busy: dict[str, int] = {}
        for t in self.db.next_queued(50):
            if started >= free:
                break
            aid = t["to_agent_id"]
            a = self.db.agent(aid) if aid else None
            if a is None or a["status"] != "active":
                continue  # paused agents keep their queue
            if aid not in busy:
                busy[aid] = self.db.running_count(aid)
            if busy[aid] >= max(1, int(a.get("max_concurrency") or 1)):
                continue
            if not self.db.claim(t["id"], self.id, self.svc.limits.lease_s):
                continue  # another worker took it
            busy[aid] += 1
            started += 1
            self.stats["runs"] += 1
            claimed = self.db.task(t["id"])
            with self._lock:
                self.running[t["id"]] = self.pool.submit(self.svc.runner.run, claimed, self.id)
        return started

    def drain(self, timeout_s: float = 60.0) -> None:
        """Tests and the end-to-end check: tick until no task is queued or running here."""
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            self.tick()
            with self._lock:
                futs = list(self.running.values())
            for f in futs:
                f.result(timeout=max(0.1, end - time.monotonic()))
            self._reap()
            if not self.running and not self.db.next_queued(1) and not self.db.due_events(1):
                return
        raise TimeoutError("worker did not drain in time")


def build(env: dict | None = None):  # pragma: no cover - process wiring, exercised by the e2e check
    """The Autonomy service over the database the API server uses."""
    from agent.runtime import build_front_desk
    from agent.workforce import build_workforce

    from .service import Autonomy

    env = dict(os.environ if env is None else env)
    desk, chain = build_front_desk(env)
    wf = build_workforce(desk.finance.store, chain, env)
    return Autonomy(wf, env=env)


def main() -> None:  # pragma: no cover - process entry point
    svc = build()
    w = Worker(svc)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    w.start()
    print(f"[worker] {w.id} running, concurrency {w.concurrency}, sandbox {svc.sandbox.info()}", flush=True)
    stop.wait()
    w.stop(wait=False)


if __name__ == "__main__":  # pragma: no cover
    main()
