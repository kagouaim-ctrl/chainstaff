"""AI workforce: agents, the bus, the manager, skills on (fake) chain data, approvals, anchoring, schedules, memory."""

from __future__ import annotations

import json
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_utils import keccak

from agent.config import load_chain
from agent.execution.rpc import RpcClient
from agent.store import Store, _iso
from agent.workforce import Workforce, WorkforceError, build_workforce
from agent.workforce import db as D
from agent.workforce.anchor import Anchor, anchor_calldata, decode_calldata
from agent.workforce.chain import EIP1967_IMPL, TRANSFER_TOPIC, ChainReader, _decode_string, push4_selectors
from agent.workforce.cron import Cron
from agent.workforce.planner import Planner, PlanError
from agent.workforce.skills import SKILL_BY_ID, SkillContext, SkillError, validate

ROOT = Path(__file__).resolve().parent.parent
EOA = "0x1111111111111111111111111111111111111111"
TOKEN = "0x2222222222222222222222222222222222222222"
OWNER = "0x3333333333333333333333333333333333333333"
WALLET = "0x4444444444444444444444444444444444444444"
PROXY = "0x5555555555555555555555555555555555555555"
IMPL = "0x6666666666666666666666666666666666666666"
OLD = "0x7777777777777777777777777777777777777777"
NOW = datetime.now(timezone.utc)


def sel(sig: str) -> str:
    return keccak(text=sig)[:4].hex()


def runtime(*sigs: str) -> str:
    # PUSH4 <selector> EQ ... for each function, with a PUSH32 carrying a decoy selector to prove PUSH data is skipped
    body = "".join("63" + sel(s) + "14" for s in sigs)
    return "0x6080" + body + "7f" + sel("blacklist(address)") + "00" * 28 + "00"


class FakeChain:
    """Answers the JSON-RPC methods the workforce uses, like a BSC testnet node would."""

    def __init__(self, chain_id: int = 97) -> None:
        self.chain_id = chain_id
        self.head = 1000
        self.sent: list[str] = []
        self.receipts: dict[str, dict] = {}
        self.fail = set()
        self.calls: list[str] = []

    def __call__(self, url: str, body: dict) -> dict:
        m, p = body["method"], body["params"]
        self.calls.append(m)
        if m in self.fail:
            return {"error": {"code": -32000, "message": f"{m} broken"}}
        return {"result": getattr(self, m)(*p)}

    def eth_chainId(self):
        return hex(self.chain_id)

    def eth_blockNumber(self):
        return hex(self.head)

    def eth_getBlockByNumber(self, tag, full):
        return {"number": hex(self.head), "timestamp": hex(1_760_000_000), "gasUsed": "0x10", "transactions": ["0x1", "0x2"]}

    def eth_gasPrice(self):
        return hex(1_000_000_000)

    def eth_getBalance(self, a, tag):
        return hex(2 * 10**18 if a.lower() == EOA else 10**17)

    def eth_getTransactionCount(self, a, tag):
        return hex(7)

    def eth_getCode(self, a, tag):
        a = a.lower()
        if a == TOKEN:
            return runtime("mint(address,uint256)", "pause()", "transferOwnership(address)", "name()")
        if a == PROXY:
            return runtime("upgradeTo(address)")
        if a == IMPL:
            return runtime("setFee(uint256)")
        return "0x"

    def eth_getStorageAt(self, a, slot, tag):
        if a.lower() == PROXY and slot == EIP1967_IMPL:
            return "0x" + IMPL[2:].rjust(64, "0")
        return "0x" + "0" * 64

    def eth_call(self, call, tag):
        to, data = call["to"].lower(), call["data"]
        s = data[2:10]
        if to == TOKEN:
            if s == sel("name()"):
                return "0x" + encode(["string"], ["Kagou Token"]).hex()
            if s == sel("symbol()"):
                return "0x" + encode(["string"], ["KGU"]).hex()
            if s == sel("decimals()"):
                return hex(18)
            if s == sel("totalSupply()"):
                return hex(10**24)
            if s == sel("owner()"):
                return "0x" + OWNER[2:].rjust(64, "0")
            if s == sel("balanceOf(address)"):
                return hex(5 * 10**18)
        if to == OLD and s == sel("symbol()"):
            return "0x" + b"OLD".ljust(32, b"\0").hex()
        raise RuntimeError("execution reverted")

    def eth_getLogs(self, flt):
        t = lambda a: "0x" + a[2:].rjust(64, "0")  # noqa: E731
        return [{"blockNumber": hex(990), "transactionHash": "0xaa", "logIndex": "0x0", "topics": [TRANSFER_TOPIC, t(EOA), t(OWNER)], "data": hex(10**18)},
                {"blockNumber": hex(995), "transactionHash": "0xbb", "logIndex": "0x1", "topics": ["0x" + "ab" * 32], "data": "0x"}]

    def eth_estimateGas(self, call):
        return hex(22000)

    def eth_sendRawTransaction(self, raw):
        h = "0x" + keccak(text=raw + str(len(self.sent))).hex()
        self.sent.append(h)
        return h

    def eth_getTransactionReceipt(self, h):
        return self.receipts.get(h)

    def eth_getTransactionByHash(self, h):
        if h in self.receipts:
            return {"hash": h, "blockNumber": self.receipts[h]["blockNumber"], "input": self.inputs.get(h, "0x")}
        return None

    inputs: dict = {}

    def mine(self, ok: bool = True, block: int = 1001):
        self.head = block + 2
        for h in self.sent:
            self.receipts.setdefault(h, {"blockNumber": hex(block), "status": "0x1" if ok else "0x0", "gasUsed": hex(21500), "effectiveGasPrice": hex(10**9)})


class FakeWallet:
    address = WALLET

    def __init__(self):
        self.signed = []

    def sign_transaction(self, tx):
        self.signed.append(tx)
        return {"rawTransaction": "0x" + tx["data"][2:]}


class FakeLLM:
    """Stands in for anthropic.Anthropic(): returns queued responses and records requests."""

    def __init__(self, *texts: str, stop: str = "end_turn", usage=(1000, 200)):
        self.texts = list(texts)
        self.stop = stop
        self.usage = usage
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def __call__(self):
        return self

    def create(self, **kw):
        self.requests.append(kw)
        text = self.texts.pop(0) if self.texts else "- 摘要"
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


@pytest.fixture
def fake():
    return FakeChain()


@pytest.fixture
def reader(fake):
    return ChainReader(RpcClient("http://rpc", post=fake), "bsc-testnet", 97, "https://testnet.bscscan.com/tx/")


@pytest.fixture
def wf(reader):
    w = Workforce(Store(), reader=reader, tokens={"USDT": (TOKEN, 18)}, max_workers=3)
    yield w
    w.stop()


def team(wf: Workforce, manager_provider: str = "none", with_tx: bool = True):
    mgr = wf.create_agent({"name": "AI Manager", "role": "Manager", "is_manager": True, "provider": manager_provider}, "owner")
    onchain = wf.create_agent({"name": "On-Chain Analyst", "role": "Analyst", "skills": ["wallet_analysis"] + (["tx_history"] if with_tx else []),
                               "permissions": ["chain:read", "net:etherscan"]}, "owner")
    contract = wf.create_agent({"name": "Contract Agent", "role": "Contract", "skills": ["contract_analysis", "token_info", "contract_events"],
                                "permissions": ["chain:read"]}, "owner")
    report = wf.create_agent({"name": "Report Agent", "role": "Reporter", "skills": ["data_report"], "permissions": ["report:write"]}, "owner")
    ws = wf.create_workspace({"name": "BSC Research", "manager_id": mgr["id"], "members": [onchain["id"], contract["id"], report["id"]]}, "owner")
    return SimpleNamespace(mgr=mgr, onchain=onchain, contract=contract, report=report, ws=ws)


# ---------------------------------------------------------------- agents

def test_agent_lifecycle(wf):
    a = wf.create_agent({"name": "  Risk  ", "skills": ["contract_analysis"], "permissions": ["chain:read"], "budget_usd": "2.5",
                         "provider": "anthropic", "model_config": {"effort": "low", "junk": 1}, "onchain": {"agent_id": "2648", "x": "y"}}, "owner")
    assert a["name"] == "Risk" and a["model"] == wf.planner.model and a["model_config"] == {"effort": "low"} and a["onchain"] == {"agent_id": "2648"}
    assert wf.update_agent(a["id"], {"description": "reads contracts", "skills": []}, "owner")["skills"] == []
    b = wf.duplicate_agent(a["id"], "owner")
    assert b["name"] == "Risk 副本" and b["id"] != a["id"]
    assert wf.set_agent_status(a["id"], "paused", "owner")["status"] == "paused"
    assert wf.set_agent_status(a["id"], "archived", "owner")["status"] == "archived"
    wf.delete_agent(b["id"], "owner")
    assert wf.db.agent(b["id"]) is None
    assert any("创建员工" in e["text"] for e in wf.store.events(("audit",)))
    for bad, msg in [({"name": ""}, "不能为空"), ({"name": "x", "skills": ["nope"]}, "没有这些技能"), ({"name": "x", "permissions": ["root"]}, "没有这些权限"),
                     ({"name": "x", "provider": "openai"}, "提供方"), ({"name": "x", "budget_usd": "-1"}, "负数"), ({"name": "x", "budget_usd": "abc"}, "格式"),
                     ({"name": "x", "model_config": [1]}, "对象")]:
        with pytest.raises(WorkforceError, match=msg):
            wf.create_agent(bad, "owner")
    with pytest.raises(WorkforceError, match="没有员工"):
        wf.update_agent("agt_x", {}, "owner")
    with pytest.raises(WorkforceError, match="状态只能"):
        wf.set_agent_status(a["id"], "fired", "owner")


def test_agent_with_history_cannot_be_deleted_or_archived_while_busy(wf):
    t = team(wf)
    wf.submit_goal(t.ws["id"], f"Analyze {EOA}", "owner")
    with pytest.raises(WorkforceError, match="没结束的任务"):
        wf.set_agent_status(t.mgr["id"], "archived", "owner")
    with pytest.raises(WorkforceError, match="没结束的任务"):
        wf.delete_agent(t.mgr["id"], "owner")
    wf.run_until_idle()
    with pytest.raises(WorkforceError, match="归档"):
        wf.delete_agent(t.onchain["id"], "owner")
    lonely = wf.create_agent({"name": "Lonely"}, "owner")
    wf.update_workspace(t.ws["id"], {"members": [t.onchain["id"], lonely["id"]]}, "owner")
    wf.delete_agent(lonely["id"], "owner")
    assert lonely["id"] not in wf.db.workspace(t.ws["id"])["members"]


def test_workspace_validation(wf):
    worker = wf.create_agent({"name": "w"}, "owner")
    with pytest.raises(WorkforceError, match="名称"):
        wf.create_workspace({"name": " "}, "owner")
    with pytest.raises(WorkforceError, match="AI Manager"):
        wf.create_workspace({"name": "x", "manager_id": worker["id"]}, "owner")
    with pytest.raises(WorkforceError, match="没有员工"):
        wf.create_workspace({"name": "x", "members": ["agt_nope"]}, "owner")
    ws = wf.create_workspace({"name": "x", "members": [worker["id"]]}, "owner")
    with pytest.raises(WorkforceError, match="还没有 AI 主管"):
        wf.submit_goal(ws["id"], "hi", "owner")
    ws = wf.update_workspace(ws["id"], {"name": "", "description": "d", "archived": True}, "owner")
    assert ws["name"] == "x" and ws["archived"]
    with pytest.raises(WorkforceError, match="没有工作区"):
        wf.workspace_or_404("ws_nope")


# ---------------------------------------------------------------- the end-to-end flow

def test_end_to_end_contract_analysis_with_approval_and_anchor(fake, reader):
    store = Store()
    wallet = FakeWallet()
    from agent.execution import RpcSender

    anchor = Anchor(RpcSender(wallet, reader.rpc, 97), reader, WALLET)
    wf = Workforce(store, reader=reader, tokens={"USDT": (TOKEN, 18)}, anchor=anchor)
    t = team(wf)
    goal = wf.submit_goal(t.ws["id"], f"Analyze this BSC contract {TOKEN}", "owner", priority=1)
    assert goal["status"] == D.PENDING and goal["assigned_agent"] == t.mgr["id"]
    wf.run_until_idle()

    goal = wf.db.task(goal["id"])
    subs = {s["skill"]: s for s in wf.db.subtasks(goal["id"])}
    assert goal["status"] == D.REVIEW and goal["planner"] == "rules"
    # manager first asked for a wallet check, learned it's a contract, then delegated the contract work
    assert set(subs) == {"wallet_analysis", "contract_analysis", "token_info", "contract_events", "tx_history", "data_report"}
    assert subs["wallet_analysis"]["result"]["is_contract"] is True
    assert subs["contract_analysis"]["assigned_agent"] == t.contract["id"]
    assert {f["signature"] for f in subs["contract_analysis"]["result"]["privileged_functions"]} == {"mint(address,uint256)", "pause()", "transferOwnership(address)"}
    assert subs["token_info"]["result"]["symbol"] == "KGU"
    assert subs["contract_events"]["result"]["by_event"]["Transfer"] == 1
    # no Etherscan key: the tx history step fails honestly and the report says so
    assert subs["tx_history"]["status"] == D.FAILED and "ETHERSCAN_API_KEY" in subs["tx_history"]["error"]
    rep = subs["data_report"]
    assert rep["status"] == D.COMPLETED and "可增发" in rep["result"]["report_md"] and rep["result"]["incomplete"]
    assert goal["result_hash"] == rep["result"]["sha256"] and goal["steps"] <= goal["max_steps"]
    # real message passing: dispatch, results to the manager, handoffs to the reporter, all processed
    msgs = wf.db.messages(root_id=goal["id"], limit=1000)
    events = [m["event"] for m in msgs]
    for ev in ("task.assign", "task.result", "handoff", "tool.start", "tool.done", "tool.error", "task.completed", "task.failed"):
        assert ev in events, ev
    directed = [m for m in msgs if m["recipient"]]
    assert directed and all(m["status"] == D.PROCESSED for m in directed)
    handoffs = [m for m in directed if m["event"] == "handoff"]
    assert {m["recipient"] for m in handoffs} == {t.report["id"]} and len(handoffs) == 5
    kinds = {m["kind"] for m in wf.db.messages(workspace_id=t.ws["id"], limit=1000)}
    assert {"user", "agent", "task", "tool", "system"} <= kinds

    # human approval, then the hash goes to BSC testnet
    ap = [a for a in wf.db.approvals(status="pending") if a["task_id"] == goal["id"]][0]
    with pytest.raises(WorkforceError, match="驳回时不能上链"):
        wf.decide(ap["id"], False, "owner", anchor=True)
    wf.decide(ap["id"], True, "owner", note="looks right", anchor=True)
    with pytest.raises(WorkforceError, match="已经处理过"):
        wf.decide(ap["id"], True, "owner")
    goal = wf.db.task(goal["id"])
    assert goal["status"] == D.COMPLETED
    rec = wf.db.chain_records(task_id=goal["id"])[0]
    assert rec["status"] == "pending" and rec["tx_hash"] in fake.sent and rec["chain_id"] == 97
    tx = wallet.signed[0]
    assert tx["value"] == 0 and tx["to"].lower() == WALLET and tx["chainId"] == 97
    assert decode_calldata(tx["data"]) == {"result_hash": goal["result_hash"], "task_id": goal["id"]}
    with pytest.raises(WorkforceError, match="已经上链或正在确认"):
        wf.anchor_task(goal["id"], "owner")
    fake.mine()
    wf.run_until_idle()
    rec = wf.db.chain_records(task_id=goal["id"])[0]
    assert rec["status"] == "confirmed" and rec["block"] == 1001 and rec["fee_wei"] == str(21500 * 10**9)
    assert any(m["kind"] == "chain" and m["event"] == "chain.confirmed" for m in wf.db.messages(root_id=goal["id"], limit=1000))
    # workspace memory got the report; the reporter remembers what it did
    assert wf.db.search_memory([("workspace", t.ws["id"])], "可增发")
    assert wf.db.search_memory([("agent", t.report["id"])], "")

    # everything survives a restart: a new engine on the same database sees the same state
    wf.stop()
    wf2 = Workforce(store, reader=reader)
    assert wf2.db.task(goal["id"])["status"] == D.COMPLETED
    assert len(wf2.db.messages(root_id=goal["id"], limit=1000)) == len(wf.db.messages(root_id=goal["id"], limit=1000))
    ov = wf2.overview()
    assert ov["tasks"]["completed"] >= 5 and ov["pending_approvals"] == 0 and ov["agents"]["total"] == 4
    views = {a["id"]: a for a in wf2.agent_views()}
    assert views[t.contract["id"]]["tasks_completed"] == 3 and views[t.contract["id"]]["success_rate"] == 1.0
    assert views[t.onchain["id"]]["live"] == "error"
    wf2.stop()


def test_eoa_goal_uses_tx_history_with_etherscan(reader):
    rows = [{"hash": "0x1", "blockNumber": "10", "from": EOA, "to": OWNER, "value": str(10**18), "isError": "0", "timeStamp": "1760000000", "input": "0xa9059cbb00"},
            {"hash": "0x2", "blockNumber": "11", "from": OWNER, "to": EOA, "value": "0", "isError": "1", "timeStamp": "1760000100", "input": "0x"}]
    calls = []

    def scan(params):
        calls.append(params)
        return {"status": "1", "result": rows}

    wf = Workforce(Store(), reader=reader, etherscan=scan)
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"check wallet {EOA}", "owner")
    wf.run_until_idle()
    subs = {s["skill"]: s for s in wf.db.subtasks(g["id"])}
    assert set(subs) == {"wallet_analysis", "tx_history", "data_report"}
    h = subs["tx_history"]["result"]
    assert h["count"] == 2 and h["outgoing"] == 1 and h["failed"] == 1 and h["top_methods"][0]["selector"] == "0xa9059cbb"
    assert "1 笔失败" in subs["data_report"]["result"]["report_md"]
    assert wf.db.task(g["id"])["status"] == D.REVIEW
    wf.stop()


def test_reject_and_cancel_and_retry(wf, fake):
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")
    wf.run_until_idle()
    ap = wf.db.approvals(status="pending")[0]
    wf.decide(ap["id"], False, "owner", note="not enough")
    assert wf.db.task(g["id"])["status"] == D.FAILED
    with pytest.raises(WorkforceError, match="不能取消"):
        wf.cancel(g["id"], "owner")
    # retry a failed subtask: the goal goes back to waiting and the work runs again
    failed = [s for s in wf.db.subtasks(g["id"]) if s["status"] == D.FAILED][0]
    with pytest.raises(WorkforceError, match="只能重试失败的子任务"):
        wf.retry(g["id"], "owner")
    wf.retry(failed["id"], "owner")
    assert wf.db.task(g["id"])["status"] == D.WAITING
    wf.run_until_idle()
    assert wf.db.task(failed["id"])["status"] == D.FAILED  # still no Etherscan key; same honest failure
    # cancel stops everything still open
    g2 = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    wf.cancel(g2["id"], "owner", "changed my mind")
    g2 = wf.db.task(g2["id"])
    assert g2["status"] == D.CANCELLED and g2["error"] == "changed my mind"
    assert all(m["status"] == D.EXPIRED for m in wf.db.messages(task_id=g2["id"], limit=50) if m["recipient"])
    wf.run_until_idle()
    assert not wf.db.subtasks(g2["id"])
    g3 = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    wf.run_until_idle()
    wf.decide(wf.db.approvals(status="pending")[0]["id"], True, "owner")
    with pytest.raises(WorkforceError, match="已经completed"):
        wf.retry([s for s in wf.db.subtasks(g3["id"]) if s["status"] == D.FAILED][0]["id"], "owner")
    with pytest.raises(WorkforceError, match="没有配置测试网钱包"):
        wf.anchor_task(g3["id"], "owner")
    with pytest.raises(WorkforceError, match="没有任务"):
        wf.task_or_404("tsk_nope")


def test_rule_planner_limits(wf):
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], "write a tweet about us", "owner")
    wf.run_until_idle()
    g = wf.db.task(g["id"])
    assert g["status"] == D.FAILED and "规则规划器只能处理" in g["error"]
    # max steps: plan (1) + wallet (1) = 2, the follow-ups don't fit
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner", max_steps=2)
    wf.run_until_idle()
    g = wf.db.task(g["id"])
    assert g["status"] == D.FAILED and "最大执行步数" in g["error"]
    assert all(s["status"] in D.FINAL for s in wf.db.subtasks(g["id"]))
    for kw, msg in [({"priority": 9}, "优先级"), ({"max_steps": 0}, "最大步数"), ({"timeout_s": 1}, "超时"), ({"budget_usd": "-1"}, "负数")]:
        with pytest.raises(WorkforceError, match=msg):
            wf.submit_goal(t.ws["id"], "x", "owner", **kw)
    with pytest.raises(WorkforceError, match="不能为空"):
        wf.submit_goal(t.ws["id"], "  ", "owner")
    wf.set_agent_status(t.mgr["id"], "paused", "owner")
    with pytest.raises(WorkforceError, match="不在岗"):
        wf.submit_goal(t.ws["id"], "x", "owner")


def test_planner_without_needed_agents(wf):
    mgr = wf.create_agent({"name": "M", "is_manager": True}, "owner")
    only_contract = wf.create_agent({"name": "C", "skills": ["contract_analysis"], "permissions": ["chain:read"]}, "owner")
    ws = wf.create_workspace({"name": "w", "manager_id": mgr["id"], "members": [only_contract["id"]]}, "owner")
    g = wf.submit_goal(ws["id"], f"analyze {TOKEN}", "owner")
    wf.run_until_idle()
    notes = [m["text"] for m in wf.db.messages(root_id=g["id"], limit=100) if m["event"] == "plan.note"]
    assert any("数据报告" in n for n in notes) and any("代币信息" in n for n in notes)
    assert wf.db.task(g["id"])["status"] == D.REVIEW
    nobody = wf.create_workspace({"name": "empty", "manager_id": mgr["id"]}, "owner")
    g = wf.submit_goal(nobody["id"], f"analyze {TOKEN}", "owner")
    wf.run_until_idle()
    assert "没有在岗的员工" in wf.db.task(g["id"])["error"]
    idle = wf.create_agent({"name": "I"}, "owner")
    ws3 = wf.create_workspace({"name": "w3", "manager_id": mgr["id"], "members": [idle["id"]]}, "owner")
    g = wf.submit_goal(ws3["id"], f"analyze {TOKEN}", "owner")
    wf.run_until_idle()
    assert "没有能做链上分析" in wf.db.task(g["id"])["error"]


def test_permission_and_skill_state_are_enforced_at_execution(wf):
    t = team(wf)
    assert wf.can_run(t.contract, "nope").startswith("没有技能")
    wf.skill_action("token_info", "disable", "owner")
    assert "已停用" in wf.can_run(wf.db.agent(t.contract["id"]), "token_info")
    wf.skill_action("token_info", "uninstall", "owner")
    assert "没有安装" in wf.can_run(wf.db.agent(t.contract["id"]), "token_info")
    wf.skill_action("token_info", "install", "owner")
    # permission removed after planning: the worker refuses at run time, no retry
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    m = wf.db.inbox(t.mgr["id"])[0]
    wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
    wf._plan(wf.db.agent(t.mgr["id"]), m)  # manager plans and assigns
    wf.update_agent(t.onchain["id"], {"permissions": []}, "owner")
    wf.run_until_idle()
    w = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "wallet_analysis"][0]
    assert w["status"] == D.FAILED and "权限拒绝" in w["error"] and w["attempts"] == 1


def test_transient_errors_retry_then_fail(reader, fake):
    wf = Workforce(Store(), reader=reader)
    t = team(wf)
    fake.fail.add("eth_getCode")
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    wf.run_until_idle()
    w = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "wallet_analysis"][0]
    assert w["status"] == D.FAILED and w["attempts"] == 2 and "RuntimeError" in w["error"]
    assert any(m["event"] == "task.retry" for m in wf.db.messages(root_id=g["id"], limit=200))
    # the report still ran on what it got and says what is missing
    rep = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "data_report"][0]
    assert rep["status"] == D.COMPLETED and rep["result"]["incomplete"]
    wf.stop()


def test_dependents_of_failed_tasks_are_cancelled(wf):
    mgr = wf.create_agent({"name": "M", "is_manager": True, "provider": "anthropic"}, "owner")
    a = wf.create_agent({"name": "A", "skills": ["token_info", "contract_events"], "permissions": ["chain:read"]}, "owner")
    ws = wf.create_workspace({"name": "w", "manager_id": mgr["id"], "members": [a["id"]]}, "owner")
    plan = {"steps": [{"key": "t", "title": "token", "skill": "token_info", "agent_id": a["id"], "input_json": json.dumps({"address": EOA}), "depends_on": []},
                      {"key": "e", "title": "events", "skill": "contract_events", "agent_id": a["id"], "input_json": json.dumps({"address": EOA}), "depends_on": ["t"]}]}
    wf.llm = FakeLLM(json.dumps(plan))
    wf.planner.llm = wf.llm
    g = wf.submit_goal(ws["id"], f"token {EOA}", "owner")
    wf.run_until_idle()
    subs = {s["skill"]: s for s in wf.db.subtasks(g["id"])}
    assert subs["token_info"]["status"] == D.FAILED and "不是标准 ERC-20" in subs["token_info"]["error"]
    assert subs["contract_events"]["status"] == D.CANCELLED
    assert wf.db.task(g["id"])["status"] == D.FAILED and "都没有完成" in wf.db.task(g["id"])["error"]


# ---------------------------------------------------------------- model planner

def model_team(wf, llm):
    wf.llm = llm
    wf.planner = Planner(llm, (Decimal("5"), Decimal("25")))
    return team(wf, manager_provider="anthropic")


def test_model_planner_runs_steps_in_parallel_and_tracks_cost(wf):
    llm = FakeLLM()
    t = model_team(wf, llm)
    wf.update_agent(t.report["id"], {"provider": "anthropic", "model": "claude-opus-5-5", "model_config": {"max_tokens": 900}}, "owner")
    plan = {"steps": [
        {"key": "c", "title": "合约权限", "skill": "contract_analysis", "agent_id": t.contract["id"], "input_json": json.dumps({"address": TOKEN}), "depends_on": []},
        {"key": "w", "title": "钱包", "skill": "wallet_analysis", "agent_id": t.onchain["id"], "input_json": json.dumps({"address": TOKEN}), "depends_on": []},
        {"key": "r", "title": "报告", "skill": "data_report", "agent_id": t.report["id"], "input_json": "{}", "depends_on": ["c", "w"]}]}
    llm.texts = [json.dumps(plan), "- 合约可增发"]
    g = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner", budget_usd="5")
    wf.run_until_idle()
    g = wf.db.task(g["id"])
    assert g["planner"] == "model" and g["status"] == D.REVIEW
    req = llm.requests[0]
    assert req["model"] == wf.planner.model and req["output_config"]["format"]["type"] == "json_schema"
    assert llm.requests[1]["max_tokens"] == 900 and llm.requests[1]["model"] == "claude-opus-5-5"
    rep = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "data_report"][0]
    assert "摘要（模型生成）" in rep["result"]["report_md"] and rep["result"]["sha256"] == g["result_hash"]
    # 2 calls × (1000 in, 200 out) at $5/$25 per MTok = 2 × $0.01
    assert Decimal(g["cost_usd"]) == Decimal("0.02") and g["tokens"] == 2400
    assert Decimal(wf.db.agent(t.mgr["id"])["cost_usd"]) == Decimal("0.01")
    plan_msg = [m for m in wf.db.messages(root_id=g["id"], limit=100) if m["event"] == "plan.created"][0]
    assert "模型规划" in plan_msg["text"] and len(plan_msg["data"]["steps"]) == 3


@pytest.mark.parametrize("bad, why", [
    ({"steps": []}, "没有给出任何步骤"),
    ({"steps": [{"key": "a", "title": "x", "skill": "token_info", "agent_id": "agt_x", "input_json": "{}", "depends_on": []}]}, "不能执行"),
    ({"steps": [{"key": "a", "title": "x", "skill": "token_info", "agent_id": "CONTRACT", "input_json": "{}", "depends_on": []}]}, "参数不合法"),
    ({"steps": [{"key": "a", "title": "x", "skill": "token_info", "agent_id": "CONTRACT", "input_json": json.dumps({"address": OWNER}), "depends_on": []}]}, "目标里没有的地址"),
    ({"steps": [{"key": "a", "title": "x", "skill": "token_info", "agent_id": "CONTRACT", "input_json": json.dumps({"address": TOKEN}), "depends_on": ["b"]}]}, "依赖了不存在"),
    ({"steps": [{"key": "a", "title": "x", "skill": "token_info", "agent_id": "CONTRACT", "input_json": json.dumps({"address": TOKEN}), "depends_on": []}] * 2}, "key 重复"),
    ({"steps": [{"key": str(i), "title": "x", "skill": "token_info", "agent_id": "CONTRACT", "input_json": "{}", "depends_on": []} for i in range(13)]}, "步骤太多"),
    ("not json", "不是合法 JSON"),
])
def test_bad_model_plans_fall_back_to_rules(wf, bad, why):
    llm = FakeLLM()
    t = model_team(wf, llm)
    llm.texts = [json.dumps(bad).replace("CONTRACT", t.contract["id"]) if isinstance(bad, dict) else bad]
    g = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
    wf.tick()
    note = [m["text"] for m in wf.db.messages(root_id=g["id"], limit=100) if m["event"] == "plan.note"][0]
    assert why in note and "改用规则规划" in note
    assert wf.db.task(g["id"])["planner"] == "rules"


def test_model_refusal_and_truncation(wf):
    for stop, why in (("refusal", "拒绝"), ("max_tokens", "截断")):
        p = Planner(FakeLLM("{}", stop=stop))
        with pytest.raises(PlanError, match=why):
            p._call({"model_config": {}}, "s", "u", None, 10)
    llm = FakeLLM()
    t = model_team(wf, llm)
    wf.update_agent(t.contract["id"], {"skills": []}, "owner")
    wf.update_agent(t.onchain["id"], {"skills": []}, "owner")
    wf.update_agent(t.report["id"], {"skills": []}, "owner")
    g = wf.submit_goal(t.ws["id"], f"Analyze {TOKEN}", "owner")
    wf.run_until_idle()
    err = wf.db.task(g["id"])["error"]
    assert "没有可用的技能" in err and "没有能做链上分析" in err
    assert Planner(None).label() == "rules" and Planner(llm).label() == "model+rules"


def test_budget_exhaustion_asks_for_approval(wf):
    llm = FakeLLM(usage=(100_000, 100_000))
    t = model_team(wf, llm)
    plan = {"steps": [{"key": "w", "title": "钱包", "skill": "wallet_analysis", "agent_id": t.onchain["id"], "input_json": json.dumps({"address": EOA}), "depends_on": []}]}
    llm.texts = [json.dumps(plan)] * 2
    g = wf.submit_goal(t.ws["id"], f"check {EOA}", "owner", budget_usd="0.5")
    wf.run_until_idle()  # planning cost $3 > $0.5 budget
    g = wf.db.task(g["id"])
    ap = wf.db.approvals(status="pending")
    assert g["status"] == D.WAITING and ap and ap[0]["kind"] == "over_budget"
    wf.tick()
    assert len(wf.db.approvals(status="pending")) == 1  # asked once
    wf.decide(ap[0]["id"], True, "owner")
    assert wf.db.task(g["id"])["budget_usd"] == "1.0"  # doubled; still over, so asks again
    wf.run_until_idle()
    ap2 = wf.db.approvals(status="pending")[0]
    wf.decide(ap2["id"], False, "owner")
    g = wf.db.task(g["id"])
    assert g["status"] == D.FAILED and "超出预算" in g["error"]
    # approved budget large enough → work continues to review
    g = wf.submit_goal(t.ws["id"], f"check {EOA}", "owner", budget_usd="2")
    wf.run_until_idle()
    wf.decide(wf.db.approvals(status="pending")[0]["id"], True, "owner")
    wf.run_until_idle()
    assert wf.db.task(g["id"])["status"] == D.REVIEW


# ---------------------------------------------------------------- the bus

def test_send_permissions_and_dedupe(wf):
    t = team(wf)
    outsider = wf.create_agent({"name": "Outsider"}, "owner")
    assert wf.send(t.mgr["id"], t.onchain["id"], "hello", dedupe_key="k1")
    assert wf.send(t.mgr["id"], t.onchain["id"], "hello again", dedupe_key="k1") is None  # duplicate dropped
    assert wf.send(t.onchain["id"], t.mgr["id"], "done")
    with pytest.raises(WorkforceError, match="message:peers"):
        wf.send(t.onchain["id"], t.contract["id"], "psst")
    wf.update_agent(t.onchain["id"], {"permissions": ["chain:read", "message:peers"]}, "owner")
    assert wf.send(t.onchain["id"], t.contract["id"], "psst")
    with pytest.raises(WorkforceError, match="同一个工作区"):
        wf.send(t.onchain["id"], outsider["id"], "hi")
    assert wf.send("user", t.mgr["id"], "from the owner")
    assert wf.send("user", outsider["id"], "no workspace")
    wf.set_agent_status(t.contract["id"], "paused", "owner")
    with pytest.raises(WorkforceError, match="不在岗"):
        wf.send(t.contract["id"], t.mgr["id"], "x")
    wf.run_until_idle()
    assert all(m["status"] == D.PROCESSED for m in wf.db.inbox(t.onchain["id"])) or not wf.db.inbox(t.onchain["id"])
    assert [m["text"] for m in wf.db.inbox(t.contract["id"])] == ["psst"]  # a paused agent keeps its inbox
    nid = wf.note(t.ws["id"], "  human note  ", "owner")
    assert wf.db.message(nid)["kind"] == "user" and wf.db.message(nid)["text"] == "human note"
    with pytest.raises(WorkforceError, match="不能为空"):
        wf.note(t.ws["id"], " ", "owner")


def test_paused_worker_times_out_and_goal_deadline(reader):
    wf = Workforce(Store(), reader=reader, msg_timeout_s=0)
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    m = wf.db.inbox(t.mgr["id"])[0]
    wf.db.set_message_status(m["id"], (D.QUEUED,), D.DELIVERED)
    wf._plan(wf.db.agent(t.mgr["id"]), m)
    wf.set_agent_status(t.onchain["id"], "paused", "owner")  # wallet assignment now waits on a paused agent
    for _ in range(4):
        wf.tick()
    w = [s for s in wf.db.subtasks(g["id"]) if s["skill"] == "wallet_analysis"][0]
    assert w["status"] == D.FAILED and "超时没有接手" in w["error"]
    assert any(m["event"] == "message.expired" for m in wf.db.messages(root_id=g["id"], limit=200))
    # a paused manager never picks the goal up: it expires
    wf.set_agent_status(t.onchain["id"], "active", "owner")
    g2 = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    wf.set_agent_status(t.mgr["id"], "paused", "owner")
    wf.tick()
    wf.tick()
    assert wf.db.task(g2["id"])["status"] == D.FAILED and "主管超时" in wf.db.task(g2["id"])["error"]
    # deadline
    wf.set_agent_status(t.mgr["id"], "active", "owner")
    g3 = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner", timeout_s=10)
    wf.db.update_task(g3["id"], deadline_at=_iso(NOW - timedelta(seconds=1)))
    wf.tick()
    assert "超时" in wf.db.task(g3["id"])["error"]
    wf.stop()


def test_delivered_but_unprocessed_messages_are_redelivered_and_recovered(wf):
    t = team(wf)
    mid = wf.send(t.mgr["id"], t.onchain["id"], "ping")
    wf.db.set_message_status(mid, (D.QUEUED,), D.DELIVERED, bump=True)
    wf.db._db.execute("UPDATE wf_messages SET updated_at = ? WHERE id = ?", (_iso(NOW - timedelta(hours=1)), mid))
    wf.tick()
    assert wf.db.message(mid)["status"] == D.PROCESSED  # re-queued then delivered and read
    # crash recovery: a subtask left running and a delivered message go back to the queue
    g = wf.submit_goal(t.ws["id"], f"analyze {EOA}", "owner")

    class Crashed:  # the process dies after delivering the assignment, before the worker runs it
        def submit(self, *a):
            f = Future()
            f.set_result(None)
            return f

        def shutdown(self, wait=True):
            pass

    wf.pool = Crashed()
    wf.tick()
    w = [s for s in wf.db.subtasks(g["id"])][0]
    wf.db.transition(w["id"], (D.ASSIGNED,), D.RUNNING)
    m = [x for x in wf.db.messages(task_id=w["id"], limit=10) if x["event"] == "task.assign"][0]
    assert m["status"] == D.DELIVERED
    fresh = Workforce(wf.store, reader=wf.reader)
    assert fresh.recover() == 2
    assert fresh.db.task(w["id"])["status"] == D.ASSIGNED and fresh.db.message(m["id"])["status"] == D.QUEUED
    fresh.run_until_idle()
    assert fresh.db.task(g["id"])["status"] == D.REVIEW
    assert fresh.recover() == 0
    fresh.stop()


# ---------------------------------------------------------------- schedules

def test_cron():
    c = Cron.parse("*/15 9-10 * * 1-5")
    t = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)  # Saturday
    assert c.next_after(t) == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    assert c.next_after(datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)).minute == 15
    assert Cron.parse("0 0 1 * 0").next_after(datetime(2026, 10, 24, tzinfo=timezone.utc)) == datetime(2026, 10, 25, tzinfo=timezone.utc)  # dom OR dow
    assert Cron.parse("0 12 * * 7").next_after(datetime(2026, 10, 24, tzinfo=timezone.utc)).isoweekday() == 7
    assert Cron.parse("5,10 * * 2 *").next_after(datetime(2026, 10, 24, tzinfo=timezone.utc)).month == 2
    for bad in ("* * *", "61 * * * *", "*/0 * * * *", "5-1 * * * *", "a * * * *"):
        with pytest.raises(ValueError):
            Cron.parse(bad)
    with pytest.raises(ValueError, match="五年内"):
        Cron.parse("0 0 31 2 *").next_after(datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_schedules_fire_pause_resume_and_stop_after_failures(wf):
    t = team(wf)
    with pytest.raises(WorkforceError, match="cron"):
        wf.add_schedule({"workspace_id": t.ws["id"], "cron": "bad", "goal": "x"}, "owner")
    with pytest.raises(WorkforceError, match="不能为空"):
        wf.add_schedule({"workspace_id": t.ws["id"], "cron": "0 9 * * *", "goal": " "}, "owner")
    s = wf.add_schedule({"workspace_id": t.ws["id"], "cron": "0 9 * * *", "goal": f"daily report {TOKEN}", "name": "Daily"}, "owner")
    assert datetime.fromisoformat(s["next_run_at"]) > NOW
    wf.db.update_schedule(s["id"], next_run_at=NOW - timedelta(minutes=1))
    wf.run_until_idle()
    s = wf.db.schedule(s["id"])
    assert s["last_status"] == "started" and datetime.fromisoformat(s["next_run_at"]) > NOW
    assert wf.db.task(s["last_task_id"])["status"] == D.REVIEW and wf.db.task(s["last_task_id"])["created_by"] == f"schedule#{s['id']}"
    assert wf.set_schedule_enabled(s["id"], False, "owner")["enabled"] == 0
    assert wf.set_schedule_enabled(s["id"], True, "owner")["enabled"] == 1
    assert wf.run_schedule_now(s["id"], "owner")["ok"]
    wf.set_agent_status(t.mgr["id"], "paused", "owner")
    for _ in range(3):
        r = wf.run_schedule_now(s["id"], "owner")
    assert not r["ok"] and wf.db.schedule(s["id"])["enabled"] == 0 and wf.db.schedule(s["id"])["failures"] == 3
    assert len(wf.db.schedule_runs(s["id"])) == 5
    with pytest.raises(WorkforceError, match="没有定时任务"):
        wf.run_schedule_now(999, "owner")
    with pytest.raises(WorkforceError, match="没有定时任务"):
        wf.set_schedule_enabled(999, True, "owner")
    assert wf.db.delete_schedule(s["id"]) and not wf.db.delete_schedule(s["id"])


# ---------------------------------------------------------------- memory

def test_memory_scopes_search_expiry(wf):
    t = team(wf)
    other = wf.create_agent({"name": "Other"}, "owner")
    wf.add_memory({"scope": "agent", "owner": t.contract["id"], "text": "PancakeSwap router 风险很低"}, "owner")
    wf.add_memory({"scope": "agent", "owner": other["id"], "text": "secret PancakeSwap note"}, "owner")
    wf.add_memory({"scope": "workspace", "owner": t.ws["id"], "text": "workspace PancakeSwap fact"}, "owner")
    m = wf.add_memory({"scope": "user", "text": "prefers Chinese reports", "ttl_days": 30}, "owner")
    vis = wf.memory_scopes(agent_id=t.contract["id"])
    found = {r["text"] for r in wf.db.search_memory(vis, "PancakeSwap")}
    assert found == {"PancakeSwap router 风险很低", "workspace PancakeSwap fact"}  # never another agent's private memory
    assert wf.db.search_memory(vis, "风险") and wf.db.search_memory(wf.memory_scopes(workspace_id=t.ws["id"]), "")
    assert len(wf.db.search_memory(wf.memory_scopes(), "PancakeSwap")) == 3
    assert wf.db.search_memory([], "x") == []
    assert wf.db.update_memory(m["id"], text="prefers English", ttl_days=0) and wf.db.memory(m["id"])["expires_at"] is None
    assert not wf.db.update_memory(m["id"])
    wf.db.update_memory(m["id"], ttl_days=1)
    wf.db._db.execute("UPDATE wf_memory SET expires_at = ? WHERE id = ?", (_iso(NOW - timedelta(days=1)), m["id"]))
    assert not wf.db.search_memory([("user", "")], "English")
    assert wf.db.purge_expired_memory() == 1 and wf.db.forget(wf.db.search_memory(vis, "router")[0]["id"])
    for bad, msg in (({"scope": "x"}, "范围"), ({"scope": "agent", "owner": "agt_no", "text": "x"}, "没有员工"),
                     ({"scope": "workspace", "owner": "ws_no", "text": "x"}, "没有工作区"), ({"scope": "project", "text": " "}, "不能为空")):
        with pytest.raises(WorkforceError, match=msg):
            wf.add_memory(bad, "owner")


# ---------------------------------------------------------------- skills

def test_skills_directly(reader, fake):
    ctx = SkillContext(reader, tokens={"USDT": (TOKEN, 18)})
    w = SKILL_BY_ID["wallet_analysis"].run(ctx, {"address": EOA})
    assert w["bnb"] == "2" and w["nonce"] == 7 and not w["is_contract"] and w["tokens"] == {"USDT": "5"}
    p = SKILL_BY_ID["contract_analysis"].run(ctx, {"address": PROXY})
    assert p["proxy"] and p["implementation"] == IMPL and p["owner"] is None
    assert {f["signature"] for f in p["privileged_functions"]} == {"upgradeTo(address)", "setFee(uint256)"}
    with pytest.raises(SkillError, match="没有合约代码"):
        SKILL_BY_ID["contract_analysis"].run(ctx, {"address": EOA})
    mon = SKILL_BY_ID["onchain_monitor"].run(ctx, {"addresses": [EOA, TOKEN]})
    assert mon["block"] == 1000 and mon["gas_price_gwei"] == "1" and len(mon["addresses"]) == 2
    fake.fail.add("eth_getLogs")
    with pytest.raises(SkillError, match="拒绝了日志查询"):
        SKILL_BY_ID["contract_events"].run(ctx, {"address": TOKEN, "blocks": 10})
    with pytest.raises(SkillError, match="没有配置链"):
        SKILL_BY_ID["wallet_analysis"].run(SkillContext(None), {"address": EOA})
    with pytest.raises(SkillError, match="ETHERSCAN_API_KEY"):
        SKILL_BY_ID["tx_history"].run(SkillContext(None), {"address": EOA})
    # verified source and Etherscan edge cases
    src = SkillContext(reader, etherscan=lambda p: {"result": [{"SourceCode": "", "ContractName": ""}]})
    assert "源码未在区块浏览器验证" in SKILL_BY_ID["contract_analysis"].run(src, {"address": TOKEN})["risks"]
    src = SkillContext(reader, etherscan=lambda p: {"result": [{"SourceCode": "a\nb", "ContractName": "KGU", "Proxy": "0"}]})
    assert SKILL_BY_ID["contract_analysis"].run(src, {"address": TOKEN})["source"]["source_lines"] == 2
    assert SKILL_BY_ID["contract_analysis"].run(SkillContext(reader, etherscan=lambda p: {"result": "x"}), {"address": TOKEN})["source"] is None
    none = SkillContext(None, etherscan=lambda p: {"status": "0", "message": "No transactions found", "result": "No transactions found"})
    assert SKILL_BY_ID["tx_history"].run(none, {"address": EOA})["count"] == 0
    with pytest.raises(SkillError, match="Etherscan 返回错误"):
        SKILL_BY_ID["tx_history"].run(SkillContext(None, etherscan=lambda p: {"result": "Invalid API Key"}), {"address": EOA})
    with pytest.raises(SkillError, match="无响应"):
        SKILL_BY_ID["tx_history"].run(SkillContext(None, etherscan=lambda p: None), {"address": EOA})
    # report formats every section, including unknown skills and missing results
    up = [{"skill": "onchain_monitor", "result": mon, "agent_name": "M"}, {"skill": "custom", "result": {"a": 1}, "agent": "x"},
          {"skill": "token_info", "result": None, "agent": "y"}, {"skill": "wallet_analysis", "result": w, "agent_name": "W"}]
    r = SKILL_BY_ID["data_report"].run(SkillContext(None, upstream=up), {"title": "T"})
    assert "## 链上状态（M）" in r["report_md"] and '"a": 1' in r["report_md"] and "未完成：没有结果" in r["report_md"] and "没有发现特权函数" in r["report_md"]
    assert r["sha256"] == SKILL_BY_ID["data_report"].run(SkillContext(None, upstream=up), {"title": "T"})["sha256"]  # deterministic


def test_validate_and_decoders():
    s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "enum": ["x"]}, "n": {"type": "integer", "minimum": 1, "maximum": 3},
                                                               "l": {"type": "array", "maxItems": 1, "items": {"type": "integer"}}}}
    validate(s, {"a": "x", "n": 2, "l": [1]})
    for bad, msg in (([], "object"), ({}, "缺少"), ({"a": "y"}, "只能是"), ({"a": "x", "n": 9}, "超出范围"), ({"a": "x", "n": True}, "integer"),
                     ({"a": "x", "l": [1, 2]}, "最多"), ({"a": "x", "l": ["s"]}, "integer")):
        with pytest.raises(SkillError, match=msg):
            validate(s, bad)
    with pytest.raises(SkillError, match="格式不对"):
        validate({"type": "string", "pattern": "^0x$"}, "1x")
    assert _decode_string("0x") is None and _decode_string("0x" + "ff" * 32) is None
    assert push4_selectors("0x63" + "deadbeef" + "7f" + "cafebabe" + "00" * 28) == {"deadbeef"}
    assert anchor_calldata("0x" + "ab" * 32, "tsk_1").startswith(b"CSTASK01") and decode_calldata("0x1234") is None
    with pytest.raises(ValueError):
        anchor_calldata("0x12", "t")


def test_chain_reader_extras(reader, fake):
    assert reader.erc20(OLD)["symbol"] == "OLD" and not reader.erc20(OLD)["is_erc20"]
    assert reader.erc20_balance(EOA, EOA, 18) is None and reader.owner(EOA) is None
    assert reader.explorer("address", EOA).endswith("/address/" + EOA)
    assert ChainReader(reader.rpc, "x", 97).explorer("tx", "0x1") is None
    d = reader.decode_log({"blockNumber": "0x1", "topics": [keccak(text="OwnershipTransferred(address,address)").hex().join(["0x", ""]), "0x" + "0" * 64, "0x" + OWNER[2:].rjust(64, "0")]})
    assert d["event"] == "OwnershipTransferred" and d["to"] == OWNER
    assert reader.decode_log({"topics": []})["event"] == "anonymous"
    assert reader.decode_log({"topics": [TRANSFER_TOPIC, "0x" + "0" * 64, "0x" + "0" * 64, hex(5)]})["value_raw"] == "5"


def test_skill_actions_and_manual_run(wf):
    with pytest.raises(WorkforceError, match="没有技能"):
        wf.skill_action("nope", "install", "owner")
    with pytest.raises(WorkforceError, match="操作只能"):
        wf.skill_action("token_info", "explode", "owner")
    wf.skill_action("token_info", "uninstall", "owner")
    for act in ("enable", "update"):
        with pytest.raises(WorkforceError, match="先安装"):
            wf.skill_action("token_info", act, "owner")
    with pytest.raises(WorkforceError, match="没有启用"):
        wf.run_skill("token_info", {"address": TOKEN}, "owner")
    wf.skill_action("token_info", "install", "owner")
    wf.db.set_skill_state("token_info", True, True, "0.9.0")
    view = next(s for s in wf.skills_view() if s["id"] == "token_info")
    assert view["update_available"] and view["installed_version"] == "0.9.0"
    assert wf.skill_action("token_info", "update", "owner")["installed_version"] == "1.0.0"
    assert wf.skill_action("token_info", "disable", "owner")["enabled"] is False
    assert wf.skill_action("token_info", "enable", "owner")["enabled"] is True
    assert wf.run_skill("token_info", {"address": TOKEN}, "owner")["output"]["symbol"] == "KGU"
    with pytest.raises(WorkforceError, match="不是标准"):
        wf.run_skill("token_info", {"address": EOA}, "owner")
    with pytest.raises(WorkforceError, match="没有技能"):
        wf.run_skill("nope", {}, "owner")
    tx = next(s for s in wf.skills_view() if s["id"] == "tx_history")
    assert tx["missing"] == ["ETHERSCAN_API_KEY"] and not tx["needs_chain"]


# ---------------------------------------------------------------- anchoring

def test_anchor_guards_and_failures(reader, fake):
    with pytest.raises(RuntimeError, match="chain id 97"):
        Anchor(None, ChainReader(reader.rpc, "bsc", 56), WALLET)
    from agent.execution import RpcSender

    store = Store()
    wf = Workforce(store, reader=reader, anchor=Anchor(RpcSender(FakeWallet(), reader.rpc, 97), reader, WALLET))
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    with pytest.raises(WorkforceError, match="审批通过"):
        wf.anchor_task(g["id"], "owner")
    wf.run_until_idle()
    wf.decide(wf.db.approvals(status="pending")[0]["id"], True, "owner")
    fake.fail.add("eth_estimateGas")  # simulation fails → nothing is broadcast
    rec = wf.anchor_task(g["id"], "owner")
    assert rec["status"] == "failed" and "estimateGas" in rec["error"] and not fake.sent
    fake.fail.clear()
    rec = wf.anchor_task(g["id"], "owner")  # failed records don't block a new attempt
    fake.mine(ok=False)
    wf.tick()
    assert wf.db.chain_records(task_id=g["id"])[0]["status"] == "failed"
    # a transaction that never shows up is marked failed after 15 minutes
    rid = wf.db.add_chain_record(task_id=g["id"], kind="task_hash", network="bsc-testnet", chain_id=97, payload_hash="0x", status="pending", tx_hash="0xlost")
    wf.tick()
    assert [r for r in wf.db.chain_records() if r["id"] == rid][0]["status"] == "pending"
    wf.db.update_chain_record(rid, at=_iso(NOW - timedelta(minutes=20)))
    wf.tick()
    assert [r for r in wf.db.chain_records() if r["id"] == rid][0]["status"] == "failed"
    wf.db.add_chain_record(task_id=None, kind="task_hash", network="bsc-testnet", chain_id=97, payload_hash="0x", status="pending")
    assert wf.anchor.poll(wf.db, wf.db.chain_records(status="pending")[0]) is None
    wf.stop()


def test_build_workforce_from_env(tmp_path, fake):
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg["tokens"]["USDT"]["address"] = TOKEN
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    chain = load_chain(p)
    got = []
    wf = build_workforce(Store(), chain, {"ETHERSCAN_API_KEY": "k", "ANTHROPIC_API_KEY": "a", "CHAINSTAFF_MODEL_PRICE": "5, 25",
                                          "CHAINSTAFF_ANCHOR_WALLET": "evm"},
                         rpc_factory=lambda url: RpcClient(url, post=fake), anthropic_factory=FakeLLM(), wallet_factory=lambda kind: FakeWallet(),
                         http_get=lambda url, params: got.append((url, params)) or {"result": []})
    assert wf.reader.chain_id == 97 and wf.tokens == {"USDT": (TOKEN, 18)} and wf.anchor.address == WALLET
    assert wf.planner.price == (Decimal(5), Decimal(25)) and wf.llm is not None
    wf.etherscan({"module": "account"})
    assert got[0][1]["apikey"] == "k"
    assert next(s for s in wf.skills_view() if s["id"] == "tx_history")["missing"] == []
    bare = build_workforce(Store(), None, {})
    assert bare.reader is None and bare.llm is None and bare.anchor is None
    assert next(s for s in bare.skills_view() if s["id"] == "wallet_analysis")["needs_chain"]
    wf.stop()
    bare.stop()


def test_overview_and_views_with_ranges(wf):
    t = team(wf)
    g = wf.submit_goal(t.ws["id"], f"analyze {TOKEN}", "owner")
    wf.tick()
    busy = {a["id"]: a for a in wf.agent_views()}
    assert busy[t.mgr["id"]]["live"] == "busy" and busy[t.mgr["id"]]["current_task"]["id"] == g["id"]
    wf.run_until_idle()
    ov = wf.overview(NOW - timedelta(hours=1))
    assert ov["series"] and ov["engine"]["chain"] and ov["running"] == 0 and ov["pending_approvals"] == 1
    wf.set_agent_status(t.contract["id"], "paused", "owner")
    assert {a["id"]: a for a in wf.agent_views()}[t.contract["id"]]["live"] == "paused"
    items, total = wf.db.tasks(q="analyze", kind="goal", priority=2, until=NOW + timedelta(days=1), sort="cost_usd", desc=False)
    assert total == 1 and items[0]["id"] == g["id"]
    assert wf.db.tasks(sort="bogus")[1] >= 1 and wf.db.tasks(workspace_id=t.ws["id"], status=["review"])[1] == 1
    assert wf.db.agent_task_stats(NOW - timedelta(days=1))
    assert wf._name(None) == "—" and wf._name("agt_gone") == "agt_gone"
