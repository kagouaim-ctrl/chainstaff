"""Agent squads: the team a mission goes to, checked before the mission exists, and run together."""

from __future__ import annotations

import pytest

from agent.execution import RpcSender
from agent.store import Store
from agent.workforce import Workforce, WorkforceError
from agent.workforce import db as D
from agent.workforce import missions as M
from agent.workforce.anchor import Anchor
from test_workforce import EOA, TOKEN, WALLET, FakeWallet, fake, reader, wf  # noqa: F401  (fixtures)


@pytest.fixture
def anchored(fake, reader):
    w = Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)}, anchor=Anchor(RpcSender(FakeWallet(), reader.rpc, 97), reader, WALLET))
    yield w
    w.stop()


def agents(wf):
    mgr = wf.create_agent({"name": "Lead", "role": "Manager", "is_manager": True}, "owner")
    chain = wf.create_agent({"name": "Chain", "role": "Analyst", "skills": ["wallet_analysis", "tx_history"], "permissions": ["chain:read", "net:etherscan"]}, "owner")
    code = wf.create_agent({"name": "Code", "role": "Contract", "skills": ["contract_analysis", "token_info", "contract_events"], "permissions": ["chain:read"]}, "owner")
    rep = wf.create_agent({"name": "Writer", "role": "Reporter", "skills": ["data_report"], "permissions": ["report:write"]}, "owner")
    return mgr, chain, code, rep


def events(wf, root):
    return [m["event"] for m in wf.db.messages(root_id=root, limit=500)]


# ---------------------------------------------------------------- the reported bug

def test_workspace_made_before_its_agents_gets_a_working_squad(wf):
    """The reported failure: a workspace created first, agents created later, mission → "工作区里没有在岗的员工".

    Creating an agent never put it in a workspace, the roster form only listed non-managers as members, and
    the manager was never counted as someone who can work. The mission was created anyway and failed in planning.
    """
    ws = wf.create_workspace({"name": "Research"}, "owner")
    mgr, chain, code, rep = agents(wf)
    with pytest.raises(WorkforceError, match="还没有 Squad"):
        wf.submit_goal(ws["id"], f"Analyze {TOKEN}", "owner")
    assert not wf.db.tasks(kind="goal")[1]  # nothing half-created
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "BSC desk", "manager_id": mgr["id"], "members": [chain["id"]]}, "owner")
    wf.squad_add_member(sq["id"], code["id"], "owner")
    wf.squad_add_member(sq["id"], rep["id"], "owner")
    g = wf.submit_goal(ws["id"], f"Analyze {TOKEN}", "owner", squad_id=sq["id"])
    wf.run_until_idle()
    g = wf.db.task(g["id"])
    assert g["status"] == D.REVIEW and g["squad_id"] == sq["id"]
    assert {s["assigned_agent"] for s in wf.db.subtasks(g["id"])} == {chain["id"], code["id"], rep["id"]}
    # the workspace roster follows its squads, so older endpoints see the same team
    assert set(wf.db.workspace(ws["id"])["members"]) == {chain["id"], code["id"], rep["id"]}


def test_a_manager_with_skills_can_work_alone(wf):
    solo = wf.create_agent({"name": "Solo", "is_manager": True, "skills": ["wallet_analysis"], "permissions": ["chain:read"]}, "owner")
    ws = wf.create_workspace({"name": "Solo"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "One", "manager_id": solo["id"]}, "owner")
    g = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    wf.run_until_idle()
    assert wf.db.task(g["id"])["status"] == D.REVIEW
    assert [s["assigned_agent"] for s in wf.db.subtasks(g["id"])] == [solo["id"]]
    # unless the squad says the manager only manages
    wf.update_squad(sq["id"], {"permissions": ["anchor_results"]}, "owner")
    with pytest.raises(WorkforceError, match="除了主管还没有成员"):
        wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])


# ---------------------------------------------------------------- preflight

def test_preflight_names_the_reason_and_the_fix(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    p = wf.preflight(ws["id"], text=f"check {EOA}")
    assert not p["ok"] and p["checks"][0]["fix"] == "create_squad"
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S"}, "owner")

    def errs(**kw):
        p = wf.preflight(ws["id"], squad_id=sq["id"], **{"text": f"check {EOA}", **kw})
        return {c["key"]: (c["text"], c["fix"]) for c in p["checks"] if c["level"] == "error"}, p

    e, p = errs()
    assert e["manager"][1] == "assign_manager" and e["executors"][1] == "add_agent" and not p["ready"]
    wf.set_squad_manager(sq["id"], mgr["id"], "owner")
    wf.squad_add_member(sq["id"], chain["id"], "owner")
    e, p = errs()
    assert not e and p["ok"] and p["ready"] and p["executors"] == [chain["id"]] and p["planner"] == "rules"
    wf.set_agent_status(chain["id"], "paused", "owner")
    e, _ = errs()
    assert "Chain（paused） 不在岗" in e["executors"][0]
    wf.set_agent_status(chain["id"], "active", "owner")
    wf.skill_action("wallet_analysis", "disable", "owner")
    wf.skill_action("tx_history", "disable", "owner")
    e, _ = errs()
    assert "已停用" in e["executors"][0]
    wf.skill_action("wallet_analysis", "enable", "owner")
    e, _ = errs(text="write a tweet")
    assert e["planner"][1] == "configure_manager"
    e, _ = errs(budget_usd="-2")
    assert "负数" in e["budget"][0]
    e, _ = errs(manager_id=code["id"])
    assert "不在 Squad" in e["manager"][0]
    wf.set_squad_status(sq["id"], "paused", "owner")
    e, _ = errs()
    assert e["squad"][1] == "resume_squad"
    with pytest.raises(WorkforceError, match="已暂停"):
        wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    other = wf.create_workspace({"name": "other"}, "owner")
    assert wf.preflight(other["id"], squad_id=sq["id"])["checks"][0]["fix"] == "configure_squad"
    assert wf.preflight("ws_nope")["checks"][0]["key"] == "workspace"
    assert not wf.db.tasks(kind="goal")[1]


def test_attachments_reach_the_planner(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"]]}, "owner")
    g = wf.submit_goal(ws["id"], "check the wallets in the file", "owner", squad_id=sq["id"],
                       attachments=[{"name": "wallets.csv", "text": f"address\n{EOA}\n"}])
    assert "wallets.csv" in g["description"] and EOA in g["description"]
    wf.run_until_idle()
    assert {s["input"]["address"] for s in wf.db.subtasks(g["id"])} == {EOA}
    for bad, msg in [([{"name": "", "text": "x"}], "文件名"), ([{"name": "a", "text": "x" * 20000}], "太长"), ([{"name": "a"}] * 6, "最多 5")]:
        with pytest.raises(WorkforceError, match=msg):
            wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"], attachments=bad)


# ---------------------------------------------------------------- squad management

def test_squad_crud_membership_and_manager(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    with pytest.raises(WorkforceError, match="名称"):
        wf.create_squad({"workspace_id": ws["id"], "name": " "}, "owner")
    with pytest.raises(WorkforceError, match="没有工作区"):
        wf.create_squad({"workspace_id": "ws_x", "name": "x"}, "owner")
    with pytest.raises(WorkforceError, match="Squad 权限"):
        wf.create_squad({"workspace_id": ws["id"], "name": "x", "permissions": ["root"]}, "owner")
    with pytest.raises(WorkforceError, match="0–50"):
        wf.create_squad({"workspace_id": ws["id"], "name": "x", "max_parallel": 99}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "description": "d", "manager_id": mgr["id"], "members": [chain["id"], mgr["id"]]}, "owner")
    assert sq["members"] == [chain["id"]] and sq["permissions"] == ["manager_executes", "anchor_results"]
    with pytest.raises(WorkforceError, match="已经在"):
        wf.squad_add_member(sq["id"], chain["id"], "owner")
    with pytest.raises(WorkforceError, match="主管"):
        wf.squad_remove_member(sq["id"], mgr["id"], "owner")
    with pytest.raises(WorkforceError, match="不在 Squad"):
        wf.squad_remove_member(sq["id"], code["id"], "owner")
    wf.squad_add_member(sq["id"], code["id"], "owner")
    # an agent can be in more than one squad
    sq2 = wf.create_squad({"workspace_id": ws["id"], "name": "S2", "manager_id": code["id"], "members": [chain["id"]]}, "owner")
    view = {a["id"]: a for a in wf.agent_views()}
    assert {s["id"] for s in view[chain["id"]]["squads"]} == {sq["id"], sq2["id"]}
    assert next(s for s in view[code["id"]]["squads"] if s["id"] == sq2["id"])["role"] == "manager"
    # promoting a member: it leaves the member list, the old manager stays on as a member
    sq = wf.set_squad_manager(sq["id"], code["id"], "owner")
    assert sq["manager_id"] == code["id"] and set(sq["members"]) == {chain["id"], mgr["id"]}
    sq = wf.squad_remove_member(sq["id"], mgr["id"], "owner")
    assert sq["members"] == [chain["id"]]
    sq = wf.update_squad(sq["id"], {"name": "Renamed", "max_parallel": 2, "members": [chain["id"], rep["id"]]}, "owner")
    assert sq["name"] == "Renamed" and sq["max_parallel"] == 2 and sq["members"] == [chain["id"], rep["id"]]
    arch = wf.create_agent({"name": "Old"}, "owner")
    wf.set_agent_status(arch["id"], "archived", "owner")
    with pytest.raises(WorkforceError, match="已归档"):
        wf.squad_add_member(sq["id"], arch["id"], "owner")
    # deleting an agent takes it out of every squad
    tmp = wf.create_agent({"name": "Tmp"}, "owner")
    wf.squad_add_member(sq2["id"], tmp["id"], "owner")
    wf.delete_agent(tmp["id"], "owner")
    assert tmp["id"] not in wf.db.squad(sq2["id"])["members"]
    views = {v["id"]: v for v in wf.squad_views(ws["id"])}
    assert views[sq["id"]]["ready"] and "wallet_analysis" in views[sq["id"]]["skills"] and views[sq["id"]]["missions"]["active"] == 0
    # delete: hard when unused, soft when it has history
    wf.delete_squad(sq2["id"], "owner")
    assert wf.db.squad(sq2["id"]) is None
    g = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    with pytest.raises(WorkforceError, match="没结束的 Mission"):
        wf.delete_squad(sq["id"], "owner")
    wf.run_until_idle()
    wf.cancel(g["id"], "owner") if wf.db.task(g["id"])["status"] != D.REVIEW else wf.decide(wf.db.approvals(status="pending")[0]["id"], True, "owner")
    wf.delete_squad(sq["id"], "owner")
    assert wf.db.squad(sq["id"])["status"] == D.SQUAD_DELETED and not wf.squad_views(ws["id"])
    with pytest.raises(WorkforceError, match="没有 Squad"):
        wf.squad_add_member(sq["id"], chain["id"], "owner")


def test_pausing_a_squad_holds_its_missions_and_resume_continues(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"], code["id"], rep["id"]]}, "owner")
    g = wf.submit_goal(ws["id"], f"Analyze {TOKEN}", "owner", squad_id=sq["id"])
    gm = wf.db.inbox(mgr["id"])[0]
    wf.db.set_message_status(gm["id"], (D.QUEUED,), D.DELIVERED)
    wf.set_squad_status(sq["id"], "paused", "owner")
    wf._plan(wf.db.agent(mgr["id"]), gm)  # planned while paused: nothing is handed out
    assert all(s["status"] == D.PENDING for s in wf.db.subtasks(g["id"]))
    wf.run_until_idle()
    assert "squad.holding" in events(wf, g["id"]) and wf.db.task(g["id"])["status"] == D.WAITING
    wf.set_squad_status(sq["id"], "active", "owner")
    wf.run_until_idle()
    assert wf.db.task(g["id"])["status"] == D.REVIEW
    with pytest.raises(WorkforceError, match="active / paused"):
        wf.set_squad_status(sq["id"], "deleted", "owner")


# ---------------------------------------------------------------- collaboration

def test_three_agent_squad_end_to_end_with_handoffs_review_and_chain(anchored, fake):
    """The acceptance flow: three agents, a squad, a manager, a mission, handoffs, approval, BSC testnet, restart."""
    wf = anchored
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "Ops"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "BSC desk", "description": "contract due diligence"}, "owner")
    wf.set_squad_manager(sq["id"], mgr["id"], "owner")
    for a in (chain, code, rep):
        wf.squad_add_member(sq["id"], a["id"], "owner")
    g = wf.submit_goal(ws["id"], f"Analyze this BSC contract {TOKEN}", "owner", squad_id=sq["id"], priority=1, budget_usd="2")
    wf.run_until_idle()
    ev = events(wf, g["id"])
    for e in ("agent.started", "plan.created", "task.assign", "agent.working", "handoff", "task.result", "goal.review", "approval.requested"):
        assert e in ev, e
    subs = wf.db.subtasks(g["id"])
    workers = {s["assigned_agent"] for s in subs}
    assert workers == {chain["id"], code["id"], rep["id"]}
    report = next(s for s in subs if s["skill"] == "data_report")
    handoffs = [m for m in wf.db.messages(root_id=g["id"], limit=500) if m["event"] == "handoff" and m["recipient"] == rep["id"]]
    assert len(handoffs) >= 3 and {h["sender"] for h in handoffs} >= {chain["id"], code["id"]}
    assert all(h["status"] == D.PROCESSED for h in handoffs) and report["status"] == D.COMPLETED
    ap = wf.db.approvals(status="pending")[0]
    wf.decide(ap["id"], True, "owner", anchor=True)
    m = M.mission(wf.db, wf.db.task(g["id"]))
    assert m["squad_id"] == sq["id"] and m["proof"]["state"] == M.ONCHAIN_PENDING
    fake.mine()
    wf.run_until_idle()
    assert M.mission(wf.db, wf.db.task(g["id"]))["proof"]["state"] == M.ONCHAIN_CONFIRMED
    view = next(v for v in wf.squad_views() if v["id"] == sq["id"])
    assert view["missions"]["completed"] == 1 and view["success_rate"] == 1.0
    # a restart (a new process on the same database) sees the same squad, mission and proof
    again = Workforce(wf.store, reader=wf.reader)
    assert again.db.squad(sq["id"])["members"] == [chain["id"], code["id"], rep["id"]]
    assert M.mission(again.db, again.db.task(g["id"]))["proof"]["state"] == M.ONCHAIN_CONFIRMED
    again.stop()


def test_squad_without_anchor_permission_completes_off_chain_only(anchored):
    wf = anchored
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"]], "permissions": ["manager_executes"]}, "owner")
    assert any(c["key"] == "anchor" for c in wf.preflight(ws["id"], squad_id=sq["id"], text=f"check {EOA}")["checks"])
    g = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    wf.run_until_idle()
    ap = wf.db.approvals(status="pending")[0]
    with pytest.raises(WorkforceError, match="anchor_results"):
        wf.decide(ap["id"], True, "owner", anchor=True)
    wf.decide(ap["id"], True, "owner")
    assert wf.db.task(g["id"])["status"] == D.COMPLETED
    with pytest.raises(WorkforceError, match="anchor_results"):
        wf.anchor_task(g["id"], "owner")


def test_agent_concurrency_cap_queues_work_instead_of_overloading(wf):
    mgr = wf.create_agent({"name": "Lead", "is_manager": True}, "owner")
    one = wf.create_agent({"name": "One", "skills": ["wallet_analysis"], "permissions": ["chain:read"], "max_concurrency": 1}, "owner")
    with pytest.raises(WorkforceError, match="1–20"):
        wf.update_agent(one["id"], {"max_concurrency": 0}, "owner")
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [one["id"]]}, "owner")
    other = "0x" + "9" * 40
    g = wf.submit_goal(ws["id"], f"check {EOA} and {other}", "owner", squad_id=sq["id"])
    gm = wf.db.inbox(mgr["id"])[0]
    wf.db.set_message_status(gm["id"], (D.QUEUED,), D.DELIVERED)
    wf._plan(wf.db.agent(mgr["id"]), gm)
    st = sorted(s["status"] for s in wf.db.subtasks(g["id"]))
    assert st == [D.ASSIGNED, D.PENDING]  # the second waits for the first
    queued = [m for m in wf.db.messages(root_id=g["id"], limit=100) if m["event"] == "task.queued"]
    assert queued and "并发上限" in queued[0]["text"]
    assert next(a for a in wf.agent_views() if a["id"] == one["id"])["running"] == 1
    wf.run_until_idle()
    assert all(s["status"] == D.COMPLETED for s in wf.db.subtasks(g["id"]))
    # the squad can cap its own parallelism too
    wf.update_squad(sq["id"], {"max_parallel": 1}, "owner")
    wf.update_agent(one["id"], {"max_concurrency": 5}, "owner")
    g2 = wf.submit_goal(ws["id"], f"check {EOA} and {other}", "owner", squad_id=sq["id"])
    wf.run_until_idle()
    assert any("同时最多执行 1 个任务" in m["text"] for m in wf.db.messages(root_id=g2["id"], limit=100) if m["event"] == "task.queued")
    assert wf.db.task(g2["id"])["status"] == D.REVIEW


def test_manager_reassigns_when_an_agent_drops_out(wf):
    mgr, chain, code, rep = agents(wf)
    backup = wf.create_agent({"name": "Backup", "skills": ["wallet_analysis"], "permissions": ["chain:read"]}, "owner")
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"], backup["id"], rep["id"]]}, "owner")
    g = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    gm = wf.db.inbox(mgr["id"])[0]
    wf.db.set_message_status(gm["id"], (D.QUEUED,), D.DELIVERED)
    wf.set_agent_status(chain["id"], "paused", "owner")
    wf.set_agent_status(backup["id"], "paused", "owner")
    wf.set_agent_status(chain["id"], "active", "owner")
    wf._plan(wf.db.agent(mgr["id"]), gm)
    w = next(s for s in wf.db.subtasks(g["id"]) if s["skill"] == "wallet_analysis")
    assert w["assigned_agent"] == chain["id"]
    # chain goes off duty before it picks the task up; backup comes back
    wf.db.transition(w["id"], (D.ASSIGNED,), D.PENDING)
    wf.set_agent_status(chain["id"], "paused", "owner")
    wf.set_agent_status(backup["id"], "active", "owner")
    wf.run_until_idle()
    w = wf.db.task(w["id"])
    assert w["status"] == D.COMPLETED and w["assigned_agent"] == backup["id"]
    moved = [m for m in wf.db.messages(root_id=g["id"], limit=200) if m["event"] == "task.reassigned"]
    assert moved and "改派给 Backup" in moved[0]["text"] and moved[0]["data"] == {"from": chain["id"], "to": backup["id"]}
    assert wf.db.task(g["id"])["status"] == D.REVIEW


def test_permission_denied_at_execution_goes_to_a_colleague(wf):
    mgr, chain, code, rep = agents(wf)
    backup = wf.create_agent({"name": "Backup", "skills": ["wallet_analysis"], "permissions": ["chain:read"]}, "owner")
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"], backup["id"]]}, "owner")
    g = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=sq["id"])
    gm = wf.db.inbox(mgr["id"])[0]
    wf.db.set_message_status(gm["id"], (D.QUEUED,), D.DELIVERED)
    wf._plan(wf.db.agent(mgr["id"]), gm)
    first = wf.db.subtasks(g["id"])[0]
    loser = wf.db.agent(first["assigned_agent"])
    wf.db.update_agent(loser["id"], permissions=[])  # revoked behind the dispatcher's back
    m = next(x for x in wf.db.inbox(loser["id"]) if x["event"] == "task.assign")
    wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
    wf._execute(loser["id"], m)
    t = wf.db.task(first["id"])
    assert t["status"] in (D.PENDING, D.ASSIGNED) and t["assigned_agent"] != loser["id"]
    assert any(m["event"] == "task.reassigned" for m in wf.db.messages(task_id=first["id"], limit=50))
    wf.run_until_idle()
    assert wf.db.task(first["id"])["status"] == D.COMPLETED


def test_squad_peer_messages_permission(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    sq = wf.create_squad({"workspace_id": ws["id"], "name": "S", "manager_id": mgr["id"], "members": [chain["id"], code["id"]]}, "owner")
    with pytest.raises(WorkforceError, match="message:peers"):
        wf.send(chain["id"], code["id"], "hi")
    assert wf.send(mgr["id"], chain["id"], "from the manager")
    wf.update_squad(sq["id"], {"permissions": ["peer_messages"]}, "owner")
    assert wf.send(chain["id"], code["id"], "hi")


def test_squad_missions_and_api_filters(wf):
    mgr, chain, code, rep = agents(wf)
    ws = wf.create_workspace({"name": "W"}, "owner")
    a = wf.create_squad({"workspace_id": ws["id"], "name": "A", "manager_id": mgr["id"], "members": [chain["id"]]}, "owner")
    b = wf.create_squad({"workspace_id": ws["id"], "name": "B", "manager_id": code["id"], "members": [chain["id"]]}, "owner")
    ga = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=a["id"])
    gb = wf.submit_goal(ws["id"], f"check {EOA}", "owner", squad_id=b["id"], manager_id=chain["id"])
    assert wf.db.task(gb["id"])["assigned_agent"] == chain["id"]  # a member can be picked as this mission's manager
    assert [m["id"] for m in M.missions(wf.db, squad_id=a["id"])] == [ga["id"]]
    wf.run_until_idle()


# ---------------------------------------------------------------- migration

def test_workspaces_from_before_squads_are_migrated(reader):
    store = Store()
    old = Workforce(store, reader=reader)
    mgr, chain, code, rep = agents(old)
    ws = old.db.add_workspace("Legacy", "", mgr["id"], [chain["id"], rep["id"]])  # written the old way: no squad
    goal = old.db.add_task(workspace_id=ws["id"], kind="goal", title="old", status=D.COMPLETED, assigned_agent=mgr["id"])
    old.stop()
    new = Workforce(store, reader=reader)
    squads = new.db.squads(ws["id"])
    assert len(squads) == 1 and squads[0]["manager_id"] == mgr["id"] and squads[0]["members"] == [chain["id"], rep["id"]]
    assert new.db.task(goal["id"])["squad_id"] == squads[0]["id"]
    g = new.submit_goal(ws["id"], f"check {EOA}", "owner")  # no squad named: the workspace's squad takes it
    assert g["squad_id"] == squads[0]["id"]
    new.run_until_idle()
    # the old roster endpoint edits that squad
    new.update_workspace(ws["id"], {"members": [chain["id"]]}, "owner")
    assert new.db.squad(squads[0]["id"])["members"] == [chain["id"]]
    assert len(Workforce(store, reader=reader).db.squads(ws["id"])) == 1  # migration runs once
    new.stop()
