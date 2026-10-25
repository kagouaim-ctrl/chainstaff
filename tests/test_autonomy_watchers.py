"""Watchers, events and missions: real triggers turned into work.

App error logs, CI results, GitHub webhooks, API health checks and BSC chain events (pending → confirmed, reorged)
all become the same normalized event, deduplicated on arrival and routed by trigger rules to missions."""

from __future__ import annotations

from datetime import timedelta

import pytest

from autonomy_kit import Kit

from agent.autonomy import constants as C
from agent.autonomy.db import iso, now_dt
from agent.autonomy.events import EventError, _objective, classify_chain
from agent.autonomy.missions import MissionError, clean_triggers, trigger_matches
from agent.autonomy.wakeup import TaskError
from agent.autonomy.watchers import WatcherError, _ts, check_url, public


class FakeChain:
    """The parts of agent.bsc.ChainService a bsc_events watcher uses."""

    def __init__(self, fail: bool = False) -> None:
        self.monitors: dict[int, dict] = {}
        self.fail = fail
        self.events = self
        self.deleted: list[int] = []

    def add_monitor(self, **kw):
        if self.fail:
            raise ValueError("bad monitor")
        mid = len(self.monitors) + 1
        self.monitors[mid] = {"id": mid, **kw, "enabled": True}
        return self.monitors[mid]

    def update_monitor(self, mid, **kw):
        if mid not in self.monitors:
            raise ValueError("no monitor")
        self.monitors[mid].update(kw)

    def delete_monitor(self, mid):
        self.deleted.append(mid)

    def status(self):
        return {"listener": True}


class Reader:
    def __init__(self, balances: dict[str, int], fail: str | None = None) -> None:
        self.rpc = self
        self.balances = balances
        self.fail = fail

    def call(self, method, params):
        addr = "0x" + params[0]["data"][-40:]
        if addr == self.fail:
            raise ConnectionError("rpc down")
        return hex(self.balances.get(addr, 0)) if addr in self.balances else "0x"


@pytest.fixture
def kit(tmp_path):
    k = Kit(tmp_path)
    yield k
    k.close()


def test_app_log_watcher_levels_tokens_and_secrets(kit):
    ws = kit.workspace()
    w, token = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "app_logs", "config": {"min_level": "warning", "service": "api"}}, "user")
    assert token.startswith("kgw_") and w["has_secret"] and "secret_hash" not in w
    raw = kit.at.db.watcher(w["id"])
    assert kit.at.watchers.token_ok(raw, token) and not kit.at.watchers.token_ok(raw, "wrong") and not kit.at.watchers.token_ok(raw, None)
    assert kit.at.watchers.ingest_log(raw, {"level": "info", "message": "hi"})["accepted"] is False
    r = kit.at.watchers.ingest_log(raw, {"level": "warning", "message": "slow request 1234ms", "timestamp": "2026-10-25T10:00:00Z", "context": {"id": 1}})
    ev = kit.at.db.event(r["event_id"])
    assert ev["type"] == "app.warning" and ev["severity"] == "medium" and ev["occurred_at"].startswith("2026-10-25T10:00:00")
    r2 = kit.at.watchers.ingest_log(raw, {"level": "warning", "message": "slow request 98ms"})
    assert r2["deduplicated"] and r2["occurrences"] == 2  # numbers are folded into the fingerprint
    for bad in ("text", {"level": "error", "message": " "}):
        with pytest.raises(WatcherError):
            kit.at.watchers.ingest_log(raw, bad)
    w2, t2 = kit.at.watchers.rotate(w["id"], "user")
    assert t2 != token and not kit.at.watchers.token_ok(kit.at.db.watcher(w["id"]), token)
    assert kit.at.watchers.update(w["id"], {"name": "", "config": {"service": "web"}}, "user")["config"]["service"] == "web"
    assert kit.at.watchers.get(w["id"])["name"] == w["name"]


def test_ci_watcher(kit):
    ws = kit.workspace()
    w, token = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "ci_failure", "config": {"pipeline": "unit"}}, "user")
    raw = kit.at.db.watcher(w["id"])
    r = kit.at.watchers.ingest_ci(raw, {"status": "failed", "branch": "main", "commit": "abc", "failed_tests": ["t1"]})
    again = kit.at.watchers.ingest_ci(raw, {"status": "failed", "branch": "main", "commit": "abc", "failed_tests": ["t1"]})
    assert again["deduplicated"] and again["event_id"] == r["event_id"]
    ev = kit.at.db.event(r["event_id"])
    assert ev["type"] == "ci.failed" and ev["severity"] == "high" and "unit 失败（main）" in ev["title"]
    ok = kit.at.watchers.ingest_ci(raw, {"conclusion": "success"})
    assert kit.at.db.event(ok["event_id"])["type"] == "ci.passed"
    for bad in ("x", {"status": "maybe"}):
        with pytest.raises(WatcherError):
            kit.at.watchers.ingest_ci(raw, bad)


def test_github_webhook_events(kit):
    ws = kit.workspace()
    w, secret = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "github_webhook", "config": {"repo": "o/r"}}, "user")
    raw = kit.at.db.watcher(w["id"])
    assert raw["webhook_secret"] == secret
    gh = kit.at.watchers.ingest_github
    assert gh(raw, "ping", "d0", {})["ping"]
    repo = {"repository": {"full_name": "o/r"}}
    r = gh(raw, "workflow_run", "d1", {**repo, "action": "completed", "workflow_run": {"name": "CI", "conclusion": "failure", "head_branch": "main"}})
    assert kit.at.db.event(r["event_id"])["type"] == "ci.failed"
    assert gh(raw, "workflow_run", "d1", {**repo, "action": "completed", "workflow_run": {}})["deduplicated"]
    assert gh(raw, "workflow_run", "d2", {**repo, "action": "requested"})["accepted"] is False
    r = gh(raw, "check_run", "d3", {**repo, "action": "completed", "check_run": {"name": "lint", "conclusion": "success"}})
    assert kit.at.db.event(r["event_id"])["type"] == "ci.passed"
    assert gh(raw, "check_run", "d4", {**repo, "action": "created"})["accepted"] is False
    r = gh(raw, "pull_request", "d5", {**repo, "action": "opened", "pull_request": {"number": 7, "title": "x"}})
    assert kit.at.db.event(r["event_id"])["type"] == "github.pull_request.opened"
    r = gh(raw, "push", "d6", {**repo, "ref": "refs/heads/main", "commits": [{"message": "m"}]})
    assert "1 commits" in kit.at.db.event(r["event_id"])["title"]
    r = gh(raw, "issues", "d7", {**repo, "action": "opened", "issue": {"number": 3, "title": "bug", "body": "it breaks"}})
    ev = kit.at.db.event(r["event_id"])
    assert ev["type"] == "github.issue.opened" and ev["severity"] == "medium"
    # A merged PR that KAGOU opened points back at the task that produced it.
    art = kit.at.db.add_artifact(task_id="job_x", workspace_id=ws["id"], agent_id="a", kind="patch", title="t", meta={"pr_url": "https://github.com/o/r/pull/9"})
    r = gh(raw, "pull_request", "d8", {**repo, "action": "closed", "pull_request": {"number": 9, "merged": True, "html_url": "https://github.com/o/r/pull/9",
                                                                                     "merged_by": {"login": "di"}}})
    ev = kit.at.db.event(r["event_id"])
    assert ev["type"] == "pr.merged" and ev["payload"]["origin_task_id"] == "job_x" and kit.at.db.artifact(art["id"])["status"] == "merged"
    r = gh(raw, "pull_request", None, {**repo, "action": "closed", "pull_request": {"number": 10, "merged": True}})
    assert kit.at.db.event(r["event_id"])["payload"]["origin_task_id"] is None
    kit.at.watchers.update(w["id"], {"config": {"events": ["push"]}}, "user")
    assert gh(kit.at.db.watcher(w["id"]), "issues", "d9", repo)["accepted"] is False
    w2, s2 = kit.at.watchers.rotate(w["id"], "user")
    assert kit.at.db.watcher(w["id"])["webhook_secret"] == s2 != secret


def test_api_health_down_once_and_recovered_once(tmp_path):
    state = {"code": 200, "boom": False}

    def get(url, timeout):
        if state["boom"]:
            raise ConnectionError("refused")
        return state["code"], 5.0, "body"

    k = Kit(tmp_path, http_get=get, env={"KAGOU_ALLOW_PRIVATE_HTTP": "1"})
    try:
        ws = k.workspace()
        k.agent("bug_hunter", "Hunter", ws)
        w, secret = k.at.watchers.create({"workspace_id": ws["id"], "kind": "api_health", "config": {"url": "http://127.0.0.1:9/health", "interval_s": 1,
                                                                                                         "fail_after": 2, "severity": "critical"}}, "user")
        assert secret is None

        def tick():
            k.at.db.update_watcher(w["id"], next_check_at=iso(now_dt() - timedelta(seconds=1)))
            return k.at.watchers.poll_health()

        assert tick() == 1 and k.at.db.watcher(w["id"])["state"]["health"] == "up"
        assert k.at.watchers.poll_health() == 0  # not due yet
        state["code"] = 503
        tick()
        assert not k.at.db.events(type_="health.down")  # one failure is not an outage
        state["boom"] = True
        tick()
        tick()
        [down] = k.at.db.events(type_="health.down")
        assert down["severity"] == "critical" and "连续 2 次" in down["title"]
        state["boom"] = False
        state["code"] = 200
        tick()
        tick()
        assert len(k.at.db.events(type_="health.recovered")) == 1
        k.at.events.process_due()
        kinds = sorted(t["kind"] for t in k.at.db.tasks())
        assert kinds == ["investigate", "verify"]
        assert k.at.watchers.healthy(k.at.db.watcher(w["id"]), True)[0]
        assert k.at.watchers.healthy(k.at.db.watcher(w["id"]), False) == (False, "没有在线的 Worker 执行检查")
        k.at.db.update_watcher(w["id"], last_heartbeat_at=iso(now_dt() - timedelta(hours=1)))
        assert k.at.watchers.healthy(k.at.db.watcher(w["id"]), True) == (False, "检查没有按时执行")
        paused = k.at.watchers.set_status(w["id"], False, "user")
        assert k.at.watchers.healthy(k.at.db.watcher(w["id"]), True) == (False, "已暂停") and paused["status"] == "paused"
        assert k.at.watchers.set_status(w["id"], True, "user")["status"] == "active"
        with pytest.raises(WatcherError, match="没有密钥"):
            k.at.watchers.rotate(w["id"], "user")
        with pytest.raises(WatcherError, match="检查间隔"):
            k.at.watchers.update(w["id"], {"config": {"interval_s": -5}}, "user")
    finally:
        k.close()


def test_watcher_config_validation(kit):
    ws = kit.workspace()
    c = lambda kind, cfg: kit.at.watchers.create({"workspace_id": ws["id"], "kind": kind, "config": cfg}, "user")  # noqa: E731
    with pytest.raises(WatcherError, match="工作区"):
        kit.at.watchers.create({"workspace_id": "ws_x", "kind": "app_logs"}, "user")
    with pytest.raises(WatcherError, match="类型"):
        c("smoke", {})
    with pytest.raises(WatcherError, match="对象"):
        c("app_logs", ["x"])
    with pytest.raises(WatcherError, match="http"):
        c("api_health", {"url": "ftp://x"})
    with pytest.raises(WatcherError, match="内网"):
        c("api_health", {"url": "http://localhost:8000/"})
    with pytest.raises(WatcherError, match="检查间隔"):
        c("api_health", {"url": "http://93.184.216.34/", "interval_s": 5})
    with pytest.raises(WatcherError, match="地址格式"):
        c("bsc_events", {"address": "0x12"})
    with pytest.raises(WatcherError, match="monitor"):
        c("bsc_events", {"address": "0x" + "a" * 40, "monitor": "everything"})
    with pytest.raises(WatcherError, match="整数"):
        c("bsc_events", {"address": "0x" + "a" * 40, "large_transfer_raw": "1e18"})
    with pytest.raises(WatcherError, match="没有 Watcher"):
        kit.at.watchers.get("wch_x")
    w, _ = c("ci_failure", {})
    assert w["config"] == {"pipeline": ""} and w["name"] == "CI 失败"
    with pytest.raises(WatcherError, match="解析不到"):
        check_url("http://no-such-host.invalid/", False)
    assert check_url("http://10.0.0.1/", True) == "http://10.0.0.1/"
    assert _ts(None) is None and _ts("not a date") is None
    assert public({"secret_hash": "h", "x": 1}) == {"x": 1, "has_secret": True}
    kit.at.watchers.delete(w["id"], "user")
    assert kit.at.db.watcher(w["id"]) is None


def test_bsc_watcher_without_a_listener_says_so(kit):
    ws = kit.workspace()
    w, secret = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "bsc_events", "config": {"address": "0x" + "A" * 40}}, "user")
    assert w["status"] == "error" and "BSC 监听" in w["last_error"] and secret is None
    assert kit.at.watchers.healthy(kit.at.db.watcher(w["id"]), True)[0] is False
    kit.at.watchers.chain = FakeChain(fail=True)
    assert kit.at.watchers.set_status(w["id"], True, "user")["last_error"] == "bad monitor"
    # A transfer whose watcher is gone, or that names no counterparty, records no flow.
    for ev in ({"source": "bsc_events", "type": "bsc.transfer", "payload": {"from": "0x1"}, "watcher_id": w["id"]},
               {"source": "bsc_events", "type": "bsc.transfer", "payload": {"from": "0x1", "to": "0x2"}, "watcher_id": None}):
        kit.at.events._enrich(ev)
    assert kit.at.db.holder_count(w["id"]) == 0
    bsc = {"status": "active", "kind": "bsc_events", "config": {}}
    assert kit.at.watchers.healthy({**bsc, "last_heartbeat_at": None}, True) == (False, "BSC 监听没有新区块（监听没运行或节点断开）")
    assert kit.at.watchers.healthy({**bsc, "last_heartbeat_at": iso(now_dt())}, True) == (True, "跟随新区块")


def _chain_ev(i, block=100, frm="0x" + "1" * 40, to="0x" + "2" * 40, value=5, kind="transfer"):
    return {"id": i, "kind": kind, "block": block, "block_hash": f"0xh{block}", "tx_hash": f"0xt{i}", "log_index": 0, "address": "0x" + "a" * 40,
            "data": {"from": frm, "to": to, "value_raw": str(value), "token": "0x" + "a" * 40}}


def test_bsc_events_confirmations_reorgs_and_holders(kit):
    ws = kit.workspace()
    chain = FakeChain()
    kit.at.watchers.chain = chain
    tw = kit.agent("token_watcher", "Token Watcher", ws)
    w, _ = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "bsc_events", "config": {"address": "0x" + "A" * 40, "symbol": "KGA",
                                                                                               "large_transfer_raw": "1000", "alert_new_holders": True}}, "user")
    assert w["status"] == "active" and chain.monitors[1]["trigger"] == {"watcher_id": w["id"]} and chain.monitors[1]["target"] == "0x" + "a" * 40
    mon = {"trigger": {"watcher_id": w["id"]}}
    ev = kit.at.events
    ev.reader = Reader({"0x" + "2" * 40: 1500, "0x" + "1" * 40: 0})
    big = _chain_ev(1, value=1500)
    ev.chain_event(big, mon, "pending")
    [e] = kit.at.db.events()
    assert e["processing_status"] == C.E_PENDING_CONFIRMATION and e["type"] == "bsc.large_transfer" and e["severity"] == "high"
    assert ev.process_due() == 0  # pending events do not start work
    ev.chain_event(big, mon, "pending")  # the same event again: nothing new
    ev.chain_event(big, mon, "confirmed")
    e = kit.at.db.event(e["id"])
    assert e["chain_status"] == "confirmed" and e["processing_status"] == C.E_RECEIVED
    ev.chain_event(big, mon, "confirmed")
    assert ev.process_due() == 1
    e = kit.at.db.event(e["id"])
    assert e["payload"]["holders"] == {"new_holder": "0x" + "2" * 40, "to_balance_raw": "1500", "holder_exited": "0x" + "1" * 40, "from_balance_raw": "0"}
    [t] = kit.at.db.tasks()
    assert t["to_agent_id"] == tw["id"] and t["kind"] == "analyze" and "不代表有不法行为" in t["objective"]
    assert kit.at.db.flows([w["id"]])[0]["total_raw"] == "1500" and kit.at.db.holder_count(w["id"]) == 1
    # A reorg removes the event; work it started that has not run yet is cancelled.
    ev.chain_event(big, mon, "reorged")
    assert kit.at.db.event(e["id"])["processing_status"] == C.E_REORGED and kit.at.db.task(t["id"])["status"] == C.T_CANCELLED
    ev.chain_event(_chain_ev(99), mon, "reorged")  # never seen: nothing to undo
    # A small transfer is recorded, without an activity row (chain noise), and enriched quietly.
    ev.reader = Reader({}, fail="0x" + "2" * 40)
    ev.chain_event(_chain_ev(2, block=101, value=3), mon, "confirmed")
    small = kit.at.db.event_by_key("chain:2")
    assert small["type"] == "bsc.transfer" and small["severity"] == "info"
    ev.process_due()
    assert kit.at.db.flows([w["id"]], "0x" + "2" * 40)
    ev.chain_event({**_chain_ev(3, kind="log"), "data": {"topics": ["0xddf252ad1234"]}}, mon, "confirmed")
    assert kit.at.db.event_by_key("chain:3")["type"] == "bsc.contract_event"
    ev.chain_event(_chain_ev(4), {"trigger": {}}, "confirmed")  # not a watcher's monitor
    kit.at.db.update_watcher(w["id"], last_heartbeat_at=None)
    kit.at.watchers.chain_heartbeat(102)
    assert kit.at.db.watcher(w["id"])["state"]["last_block"] == 102 and kit.at.watchers.healthy(kit.at.db.watcher(w["id"]), True)[0]
    # Pausing, updating and deleting reach the listener's monitor.
    kit.at.watchers.set_status(w["id"], False, "user")
    assert chain.monitors[1]["enabled"] is False
    ev.chain_event(_chain_ev(5), mon, "confirmed")
    assert kit.at.db.event_by_key("chain:5") is None
    kit.at.watchers.update(w["id"], {"config": {"min_value_raw": "10"}}, "user")
    assert chain.monitors[1]["min_value"] == "10"
    chain.monitors.clear()
    with pytest.raises(WatcherError):
        kit.at.watchers.update(w["id"], {"config": {"min_value_raw": "11"}}, "user")
    kit.at.watchers.set_status(w["id"], True, "user")  # a monitor the listener no longer knows: ignored
    kit.at.watchers.delete(w["id"], "user")
    assert chain.deleted == [1]
    assert classify_chain({"kind": "log", "address": "0x" + "b" * 40, "data": {"event": "Upgraded"}}, {"contract_event_severity": "high"})[1] == "high"


def test_event_intake_and_processing(tmp_path):
    k = Kit(tmp_path, env={"KAGOU_MAX_EVENT_RUNS_PER_HOUR": "1"})
    try:
        ws = k.workspace()
        k.agent("bug_hunter", "Hunter", ws)
        ev = k.at.events
        with pytest.raises(EventError, match="工作区"):
            ev.ingest(workspace_id="ws_x", source="manual", type_="a")
        with pytest.raises(EventError, match="severity"):
            ev.ingest(workspace_id=ws["id"], source="manual", type_="a", severity="loud")
        with pytest.raises(EventError, match="太大"):
            ev.ingest(workspace_id=ws["id"], source="manual", type_="a", payload="x" * 70000)
        e1, _ = ev.ingest(workspace_id=ws["id"], source="manual", type_="ci.failed", severity="high", title="one", payload={"a": 1})
        e2, _ = ev.ingest(workspace_id=ws["id"], source="manual", type_="ci.failed", severity="high", title="two")
        ev.process_due()
        assert len(k.at.db.tasks()) == 1  # the mission's hourly cap stopped the second
        assert any("一小时内已经触发" in a["title"] for a in k.at.db.activity(types=["mission.triggered"]))
        # Processing errors are retried with backoff, then given up on.
        e3, _ = ev.ingest(workspace_id=ws["id"], source="manual", type_="x", title="three")
        boom = ev._route
        ev._route = lambda e: (_ for _ in ()).throw(RuntimeError("router broke"))
        assert ev.process(k.at.db.event(e3["id"])) == 1
        e3 = k.at.db.event(e3["id"])
        assert e3["processing_status"] == C.E_FAILED and "router broke" in e3["error"] and e3["next_attempt_at"]
        k.at.db.update_event(e3["id"], attempts=5)
        ev.process(k.at.db.event(e3["id"]))
        assert k.at.db.event(e3["id"])["processing_status"] == C.E_IGNORED
        ev._route = boom
        assert ev.process(k.at.db.event(e1["id"])) == 0  # already processed: another worker took it
        assert "1 个事件处理失败" not in " ".join(k.at.runtime_health()["problems"])
    finally:
        k.close()


def test_verify_events(kit):
    ws = kit.workspace()
    kit.trio(ws)
    ev = kit.at.events
    e, _ = ev.ingest(workspace_id=ws["id"], source="system", type_="patch.applied", severity="medium", payload={"origin_task_id": "job_missing"})
    ev.process_due()
    assert not kit.at.db.tasks()
    root, _ = kit.at.wakeup.create_task(workspace_id=ws["id"], objective="orig", to_agent_id=kit.at.db.agents()[0]["id"])
    kit.at.set_agent_status(root["to_agent_id"], "pause", "user")
    ev.ingest(workspace_id=ws["id"], source="github_webhook", type_="pr.merged", severity="medium", title="#9", payload={"origin_task_id": root["id"]})
    ev.process_due()
    assert kit.at.db.activity(types=["task.blocked"])[0]["needs_human"]


def test_objectives_by_event_type():
    m = {"name": "M"}
    mk = lambda typ, **p: {"type": typ, "title": "T", "payload": p}  # noqa: E731
    assert "应用报错（svc）" in _objective(mk("app.error", service="svc", message="boom"), m)
    assert _objective(mk("ci.failed"), m).startswith("CI 失败")
    assert _objective(mk("health.down"), m).startswith("API 健康检查失败")
    assert _objective(mk("health.recovered"), m).startswith("API 已恢复")
    assert _objective(mk("bsc.transfer"), m).startswith("链上事件")
    assert _objective(mk("github.push"), m) == "T（Mission：M）"


def test_missions_crud_schedules_and_triggers(kit):
    ws = kit.workspace()
    a1, a2, a3 = kit.trio(ws)
    ms = kit.at.missions
    base = {"workspace_id": ws["id"], "name": "Nightly", "goal": "check everything", "lead_agent_id": a1["id"]}
    for bad, msg in (({**base, "workspace_id": "x"}, "工作区"), ({**base, "goal": ""}, "名称和目标"), ({**base, "type": "forever"}, "类型"),
                     ({**base, "lead_agent_id": "agt_x"}, "没有 Agent"), ({**base, "lead_agent_id": None}, "至少要有"),
                     ({**base, "type": "scheduled"}, "cron"), ({**base, "schedule_cron": "nope"}, ""), ({**base, "type": "event_driven"}, "触发条件"),
                     ({**base, "budget_usd": "x"}, "预算格式"), ({**base, "budget_usd": "-1"}, "负数")):
        with pytest.raises(MissionError, match=msg or None):
            ms.create(bad, "user")
    other = kit.at.create_workspace({"name": "Other"}, "user")
    far = kit.agent(None, "Far", other, responsibility="r")
    with pytest.raises(MissionError, match="没有这个工作区的权限"):
        ms.create({**base, "lead_agent_id": far["id"]}, "user")
    m = ms.create({**base, "type": "scheduled", "schedule_cron": "* * * * *", "agent_ids": [a2["id"]], "permissions": ["code:read", 3]}, "user")
    assert m["agent_ids"] == [a2["id"], a1["id"]] and m["permissions"] == ["code:read"] and m["next_run_at"]
    kit.at.db.update_mission(m["id"], next_run_at=iso(now_dt() - timedelta(minutes=1)))
    assert ms.due_schedules() == 1 and m["id"] == kit.at.db.tasks(mission_id=m["id"])[0]["mission_id"]
    assert ms.due_schedules() == 0
    kit.at.db.update_mission(m["id"], next_run_at=iso(now_dt() - timedelta(minutes=1)), schedule_cron="garbage")
    assert ms.due_schedules() == 0
    m = ms.update(m["id"], {"name": "Nightly 2", "schedule_cron": "0 4 * * *", "budget_usd": "2", "permissions": None, "agent_ids": [a3["id"]]}, "user")
    assert m["name"] == "Nightly 2" and m["budget_usd"] == "2" and m["permissions"] == [] and a3["id"] in m["agent_ids"]
    for bad in ({"name": " "}, {"schedule_cron": "bad"}):
        with pytest.raises(MissionError):
            ms.update(m["id"], bad, "user")
    m = ms.update(m["id"], {"triggers": [{"source": "app_logs", "types": ["app.error"], "min_severity": "high", "task_kind": "investigate"}], "schedule_cron": ""}, "user")
    assert m["triggers"][0]["task_kind"] == "investigate" and m["next_run_at"] is None
    resp = kit.at.db.missions(responsibility_of=a1["id"])[0]
    with pytest.raises(MissionError, match="长期职责"):
        ms.update(resp["id"], {"triggers": []}, "user")
    t = ms.run_now(m["id"], "user")
    assert t["mission_id"] == m["id"] and kit.at.db.mission(m["id"])["run_count"] >= 2
    kit.at.db.update_mission(m["id"], budget_usd="0.0001", cost_usd="1")
    with pytest.raises(MissionError, match="没有创建任务"):
        ms.run_now(m["id"], "user")
    ms.set_status(m["id"], "pause", "user")
    with pytest.raises(MissionError, match="立即执行"):
        ms.run_now(m["id"], "user")
    with pytest.raises(MissionError, match="pause / resume / stop"):
        ms.set_status(m["id"], "explode", "user")
    stopped = ms.set_status(m["id"], "stop", "user")
    assert stopped["status"] == C.M_STOPPED and kit.at.db.task(t["id"])["status"] == C.T_CANCELLED
    with pytest.raises(MissionError, match="不能resume"):
        ms.set_status(m["id"], "resume", "user")
    with pytest.raises(MissionError, match="没有 Mission"):
        ms.get("msn_x")


def test_mission_start_errors_are_logged(kit):
    ws = kit.workspace()
    a1, a2, a3 = kit.trio(ws)
    m = kit.at.missions.create({"workspace_id": ws["id"], "name": "Fix", "goal": "fix", "type": "continuous", "lead_agent_id": a2["id"]}, "user")
    assert kit.at.missions._start(m, "user", objective="x", kind="fix", reason="r") is None  # a2 cannot fix: blocked
    kit.at.db.update_mission(m["id"], lead_agent_id="agt_gone")
    def broken(**kw):
        raise TaskError("nope")

    kit.at.wakeup.create_task = broken
    assert kit.at.missions._start(kit.at.db.mission(m["id"]), "user", objective="x", kind="general", reason="r") is None
    assert kit.at.db.activity(types=["system.error"])


def test_trigger_rules(kit):
    ws = kit.workspace()
    w, _ = kit.at.watchers.create({"workspace_id": ws["id"], "kind": "app_logs"}, "user")
    for bad, msg in (("x", "列表"), (["x"], "对象"), ([{"watcher_id": "wch_x"}], "Watcher"), ([{"source": "carrier_pigeon"}], "来源"),
                     ([{"types": "app.error"}], "types"), ([{"min_severity": "loud"}], "min_severity"), ([{"task_kind": "dance"}], "task_kind")):
        with pytest.raises(MissionError, match=msg):
            clean_triggers(kit.at.db, ws["id"], bad)
    [tr] = clean_triggers(kit.at.db, ws["id"], [{"watcher_id": w["id"], "source": "app_logs", "types": ["app.*"]}])
    ev = {"watcher_id": w["id"], "source": "app_logs", "type": "app.error", "severity": "high"}
    assert trigger_matches(tr, ev)
    assert not trigger_matches(tr, {**ev, "watcher_id": "other"}) and not trigger_matches(tr, {**ev, "source": "ci_failure"})
    assert not trigger_matches(tr, {**ev, "type": "ci.failed"}) and not trigger_matches(tr, {**ev, "severity": "low"})
    assert clean_triggers(kit.at.db, ws["id"], None) == []


def test_responsibility_missions_follow_the_agent(kit):
    ws = kit.workspace()
    ws2 = kit.at.create_workspace({"name": "Second"}, "user")
    a = kit.agent("bug_hunter", "Hunter", ws)
    [m] = kit.at.db.missions(responsibility_of=a["id"])
    assert m["type"] == "continuous" and m["triggers"][0]["types"][0] == "app.error"
    kit.at.update_agent(a["id"], {"workspace_ids": [ws2["id"]]}, "user")
    assert kit.at.db.mission(m["id"])["status"] == C.M_STOPPED
    kit.at.update_agent(a["id"], {"workspace_ids": [ws["id"], ws2["id"]], "wake_on": None}, "user")
    assert kit.at.db.mission(m["id"])["status"] == C.M_ACTIVE and "bsc.*" in kit.at.db.mission(m["id"])["triggers"][0]["types"]
    kit.at.set_agent_status(a["id"], "pause", "user")
    assert {x["status"] for x in kit.at.db.missions(responsibility_of=a["id"])} == {C.M_PAUSED}
    kit.at.set_agent_status(a["id"], "resume", "user")
    kit.at.update_agent(a["id"], {"work_mode": "on_demand"}, "user")
    assert {x["status"] for x in kit.at.db.missions(responsibility_of=a["id"])} == {C.M_STOPPED}
