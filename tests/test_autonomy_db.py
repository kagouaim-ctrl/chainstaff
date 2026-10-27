"""The autonomy tables: query filters the console uses, notification dedupe, holder ordering, and adding columns
to a database written by an older build."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from autonomy_kit import Kit, Script

from agent.autonomy import constants as C
from agent.autonomy.db import AutonomyDB, now_dt
from agent.store import Store


def test_activity_filters(tmp_path):
    k = Kit(tmp_path, provider=Script())
    try:
        db, log = k.at.db, k.at.activity.log
        ws = k.workspace()
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x")
        a = log("probe.one", "one", agent_id="agt_a", task_id=t["id"])
        b = log("probe.two", "two", target_agent_id="agt_a", needs_human=True, severity="high")
        log("probe.three", "three", severity="nonsense")
        assert [r["id"] for r in db.activity(before=b + 1, types=["probe.*"])] == [b, a]
        assert {r["id"] for r in db.activity(agent_id="agt_a")} == {a, b}
        assert [r["id"] for r in db.activity(root_id=t["root_id"], types=["probe.one"])] == [a]
        assert [r["title"] for r in db.activity(needs_human=True, types=["probe.*"])] == ["two"]
        assert db.activity(types=["probe.three"])[0]["severity"] == "info"
        assert db.activity(since=now_dt() - timedelta(minutes=1), until=now_dt() + timedelta(minutes=1), types=["probe.*"], newest_first=False)[0]["id"] == a
    finally:
        k.close()


def test_notifications_artifacts_holders_and_leases(tmp_path):
    k = Kit(tmp_path, provider=Script())
    try:
        db = k.at.db
        assert db.add_notification(title="once", dedupe_key="k1") is not None
        assert db.add_notification(title="again", dedupe_key="k1") is None
        assert db.mark_read([]) == 0 and db.unread_count() == 1
        ws = k.workspace()
        t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective="x", fingerprint="fp-1")
        art = db.add_artifact(task_id=t["id"], workspace_id=ws["id"], kind="patch", title="p")
        for kw in ({"task_id": t["id"]}, {"root_id": t["root_id"]}, {"workspace_id": ws["id"]}, {"fingerprint": "fp-1"},
                   {"since": now_dt() - timedelta(minutes=1)}):
            assert [x["id"] for x in db.artifacts(**kw)] == [art["id"]], kw
        assert db.artifacts(fingerprint="other") == []
        db.update_artifact(art["id"])  # only touches updated_at
        assert db.set_holder("wch_1", "0xa", "5", 10) is None
        db.set_holder("wch_1", "0xb", "100", 10)
        db.set_holder("wch_1", "0xc", "0", 10)
        assert db.set_holder("wch_1", "0xa", "7", 11)["balance_raw"] == "5"
        assert [h["address"] for h in db.holders("wch_1")] == ["0xb", "0xa"]
        assert db.renew_leases("wkr-x", [], 60) == 0
        assert db._update("at_tasks", "id", t["id"], {}) == 0
        assert db.transition(t["id"], [db.task(t["id"])["status"]], C.T_RUNNING)
        assert db.task(t["id"])["started_at"]
    finally:
        k.close()


def test_old_databases_get_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    s = Store(str(path))
    AutonomyDB(s)
    s._db.execute("ALTER TABLE wf_agents DROP COLUMN responsibility")
    s._db._conn.close()
    s = Store(str(path))
    try:
        AutonomyDB(s)
        cols = {r["name"] for r in s._db.execute("PRAGMA table_info(wf_agents)").fetchall()}
        assert "responsibility" in cols
    finally:
        s._db._conn.close()
    assert sqlite3.connect(str(path)).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
