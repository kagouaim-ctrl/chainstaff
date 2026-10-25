"""The cloud worker: heartbeat, leases, crash recovery, per-agent concurrency, the loop surviving errors, and the
supervisor's commands. A second Worker object over the same database stands in for a second process."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from autonomy_kit import Kit, Script, turn

from agent.autonomy import constants as C
from agent.autonomy.db import iso, now_dt
from agent.autonomy.supervise import commands
from agent.autonomy.worker import Worker


@pytest.fixture
def kit(tmp_path):
    k = Kit(tmp_path, provider=Script())
    yield k
    k.close()


def _agent(kit, ws, name="Solo", **kw):
    return kit.agent(None, name, ws, responsibility="你负责处理交给你的任务。", **kw)


def test_start_heartbeat_and_stop(kit):
    ws = kit.workspace()
    a = _agent(kit, ws)
    w = Worker(kit.at, worker_id="wkr-2", concurrency=2)
    w.start(interval_s=0.05)
    try:
        t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
        end = time.monotonic() + 10
        while kit.at.db.task(t["id"])["status"] != C.T_COMPLETED and time.monotonic() < end:
            time.sleep(0.05)
        assert kit.at.db.task(t["id"])["status"] == C.T_COMPLETED
        row = next(x for x in kit.at.db.workers() if x["id"] == "wkr-2")
        assert row["status"] == "running" and row["concurrency"] == 2
    finally:
        w.stop()
    assert next(x for x in kit.at.db.workers() if x["id"] == "wkr-2")["status"] == "stopped"
    types = [a["type"] for a in kit.at.db.activity(limit=200)]
    assert "worker.started" in types and "worker.stopped" in types


def test_the_loop_survives_errors(kit):
    w = Worker(kit.at, worker_id="wkr-3")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        w._stop.set()

    w._forever(flaky, 0.01)
    assert calls["n"] == 2 and w.stats["errors"] == 1
    assert kit.at.db.activity(types=["worker.error"])[0]["severity"] == "medium"
    w.pool.shutdown()


def test_crashed_worker_tasks_are_recovered_then_failed(kit):
    ws = kit.workspace()
    a = _agent(kit, ws)
    t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
    db = kit.at.db
    for attempt in (1, 2):
        assert db.claim(t["id"], "wkr-dead", 60)
        db.add_run(task_id=t["id"], agent_id=a["id"], workspace_id=ws["id"], worker_id="wkr-dead", attempt=attempt)
        db.update_task(t["id"], lease_until=iso(now_dt() - timedelta(seconds=1)))
        assert kit.worker.recover() == 1
    task = db.task(t["id"])
    assert task["status"] == C.T_FAILED and "中断 2 次" in task["error"]
    assert all(r["status"] == C.R_INTERRUPTED for r in db.runs(task_id=t["id"]))
    assert db.activity(types=["worker.recovered"]) and db.activity(types=["task.failed"])[0]["needs_human"]
    # A task this worker is running itself is never "recovered" from under it.
    t2, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="y", to_agent_id=a["id"])
    db.claim(t2["id"], kit.worker.id, 60)
    db.update_task(t2["id"], lease_until=iso(now_dt() - timedelta(seconds=1)))
    kit.worker.running[t2["id"]] = kit.worker.pool.submit(lambda: None)
    assert kit.worker.recover() == 0
    kit.worker.running.clear()


def test_dispatch_respects_agent_and_worker_concurrency(kit):
    ws = kit.workspace()
    a = _agent(kit, ws, "One")
    b = _agent(kit, ws, "Two", max_concurrency=2)
    paused = _agent(kit, ws, "Paused")
    for i in range(3):
        kit.at.wakeup.create_task(workspace_id=ws["id"], objective=f"a{i}", to_agent_id=a["id"])
        kit.at.wakeup.create_task(workspace_id=ws["id"], objective=f"b{i}", to_agent_id=b["id"])
    kit.at.wakeup.create_task(workspace_id=ws["id"], objective="p", to_agent_id=paused["id"])
    kit.at.set_agent_status(paused["id"], "pause", "user")
    kit.at.wakeup.create_task(workspace_id=ws["id"], objective="nobody", required=["github:draft_pr"])
    gate = []
    kit.at.runner.run = lambda task, wid: gate.append(task["id"]) or C.R_COMPLETED  # claim only; nothing runs
    w = Worker(kit.at, worker_id="wkr-4", concurrency=10)
    assert w.dispatch() == 3  # one for One, two for Two, none for Paused
    assert kit.at.db.running_count(a["id"]) == 1 and kit.at.db.running_count(b["id"]) == 2
    w.pool.shutdown(wait=True)
    small = Worker(kit.at, worker_id="wkr-5", concurrency=1)
    small.running["x"] = small.pool.submit(time.sleep, 0.2)
    assert small.dispatch() == 0
    small.pool.shutdown(wait=True)
    # Another worker already took it: claim fails, nothing is double-run.
    third = _agent(kit, ws, "Three")
    kit.at.wakeup.create_task(workspace_id=ws["id"], objective="c", to_agent_id=third["id"])
    kit.at.db.claim = lambda *a: False
    assert Worker(kit.at, worker_id="wkr-6").dispatch() == 0


def test_tick_does_the_housekeeping(kit, monkeypatch):
    ws = kit.workspace()
    a = _agent(kit, ws)
    pruned = []
    monkeypatch.setattr(kit.at.sandbox, "prune", lambda keep: pruned.append(keep) or 0)
    t, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="x", to_agent_id=a["id"])
    kit.worker.tick()
    assert pruned and t["id"] in pruned[0]
    kit.drain()
    assert kit.at.db.task(t["id"])["status"] == C.T_COMPLETED and kit.worker.stats["runs"] == 1


def test_drain_gives_up(kit):
    ws = kit.workspace()
    a = _agent(kit, ws)
    kit.at.wakeup.create_task(workspace_id=ws["id"], objective="stuck", to_agent_id=a["id"])
    kit.at.set_agent_status(a["id"], "pause", "user")
    with pytest.raises(TimeoutError):
        kit.worker.drain(0.3)


def test_two_workers_never_run_the_same_task(kit):
    ws = kit.workspace()
    a = _agent(kit, ws, max_concurrency=4)
    kit.provider.turns = [turn(text=f"done {i}") for i in range(6)]
    ids = [kit.at.wakeup.create_task(workspace_id=ws["id"], objective=f"t{i}", to_agent_id=a["id"])[0]["id"] for i in range(6)]
    w2 = Worker(kit.at, worker_id="wkr-7", concurrency=2)
    for _ in range(20):
        kit.worker.dispatch()
        w2.dispatch()
        time.sleep(0.02)
    kit.drain()
    w2.drain()
    w2.pool.shutdown()
    assert all(kit.at.db.task(i)["status"] == C.T_COMPLETED for i in ids)
    assert all(len(kit.at.db.runs(task_id=i)) == 1 for i in ids)


def test_supervisor_commands():
    cmds = commands({"PORT": "9000"})
    assert cmds["api"][1:4] == ["-m", "uvicorn", "backend.app:app"] and "9000" in cmds["api"]
    assert cmds["worker"][1:] == ["-m", "agent.autonomy.worker"]
    assert "worker" not in commands({"KAGOU_WORKER": "0"}) and "8000" in commands({})["api"]
