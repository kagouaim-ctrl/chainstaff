"""Seed a KAGOU server with sample data for the static demo (step 1 of scripts/demo/README.md).

Everything is sample data: the scripted model from the tests stands in for Claude, and the chains are the test fakes.
Serves the console on http://127.0.0.1:8766 with the token "t" * 24. Needs web/terminal/dist (npm run build)."""
import json, sys, tempfile, threading, time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import uvicorn
import test_bsc as B
from test_agent_runtime import ScriptedModel, call, text
from test_workforce import EOA, TOKEN, FakeChain
from agent.runtime import build_employee
from agent.config import load_chain
from agent.execution.rpc import RpcClient
from agent.workforce import Workforce
from agent.workforce.chain import ChainReader
from agent.bsc.service import ChainService
from backend.app import create_app

tmp = Path(tempfile.mkdtemp())
p = tmp / "policy.json"; p.write_text((ROOT / "config/policy.example.json").read_text())
emp, _ = build_employee({"CHAINSTAFF_POLICY": str(p), "CHAINSTAFF_DB": str(tmp / "l.db")})
chain = load_chain(ROOT / "config/chain.example.json")

llm = ScriptedModel(routes={
    "钱包画像": [[text("先看余额和交易记录。"), call("wallet_analysis", {"address": EOA}, "w1")],
                 [call("tx_history", {"address": EOA}, "w2")],
                 [text("钱包画像：外部账户，余额约 2 BNB，近 30 天 41 笔交易，主要和 PancakeSwap 路由交互，没有和已知风险地址往来。")]],
    "合约权限": [[text("检查代币合约的权限设置。"), call("contract_analysis", {"address": TOKEN}, "k1")],
                 [call("token_info", {"address": TOKEN}, "k2")],
                 [text("合约权限：代币可增发、可暂停，owner 仍是单个外部账户，没有时间锁。持有人集中度偏高。")]],
    "综合结论": [[text("综合结论：钱包本身正常；代币合约权限集中在单个 owner，可随时增发和暂停，持有风险中等偏高。建议在 owner 转给多签并加时间锁之前，控制仓位。")]],
    "巡检": [[call("wallet_analysis", {"address": B.WALLET}, "t1")],
             [text("国库钱包巡检：余额 1.2 BNB，最近一笔转出 0.3 BNB 到已知地址，无异常。")]],
}, plans=[], usage=(1800, 320))

fc = FakeChain()
reader = ChainReader(RpcClient("http://rpc", post=fc), "bsc-testnet", 97, chain.explorer_tx_url)
wf = Workforce(emp.store, reader=reader, tokens={"USDT": (TOKEN, 18)}, llm=llm, max_workers=4)

def agent(name, role, skills, model="claude-opus-5-5", **kw):
    return wf.create_agent({"name": name, "role": role, "provider": "anthropic", "model": model, "skills": ["agent_task", *skills],
                            "permissions": ["chain:read", "model:invoke", "net:etherscan"], **kw}, "owner")

mgr = wf.create_agent({"name": "AI Manager", "role": "Manager", "is_manager": True, "provider": "anthropic"}, "owner")
wallet = agent("Wallet Agent", "On-chain analyst", ("wallet_analysis", "tx_history"))
contract = agent("Contract Agent", "Contract auditor", ("contract_analysis", "token_info"), model="claude-sonnet-5-5")
risk = agent("Risk Summarizer", "Risk lead", ())
reporter = wf.create_agent({"name": "Report Agent", "role": "Reporter", "skills": ["data_report"], "permissions": ["report:write"]}, "owner")
ws = wf.create_workspace({"name": "BSC Research", "manager_id": mgr["id"], "members": [wallet["id"], contract["id"], risk["id"], reporter["id"]]}, "owner")

llm.plans = [json.dumps({"steps": [
    {"key": "w", "title": "钱包画像", "skill": "agent_task", "agent_id": wallet["id"], "input_json": json.dumps({"instruction": f"钱包画像：{EOA}"}), "depends_on": []},
    {"key": "k", "title": "合约权限", "skill": "agent_task", "agent_id": contract["id"], "input_json": json.dumps({"instruction": f"合约权限：{TOKEN}"}), "depends_on": []},
    {"key": "s", "title": "综合结论", "skill": "agent_task", "agent_id": risk["id"], "input_json": json.dumps({"instruction": "综合结论：结合上面两份结果"}), "depends_on": ["w", "k"]},
]}), json.dumps({"steps": [
    {"key": "t", "title": "国库巡检", "skill": "agent_task", "agent_id": wallet["id"], "input_json": json.dumps({"instruction": f"巡检国库钱包 {B.WALLET}"}), "depends_on": []},
]})]
wf.submit_goal(ws["id"], f"{EOA} 适合长期持有 {TOKEN} 这个代币吗？", "owner")
wf.run_until_idle(timeout_s=60)
wf.submit_goal(ws["id"], f"巡检国库钱包 {B.WALLET}", "owner")
wf.run_until_idle(timeout_s=60)
g3 = wf.submit_goal(ws["id"], f"每小时巡检 {TOKEN} 的大额转账", "owner")
wf.cancel(g3["id"], "owner")  # the owner called it off before it was planned
wf.run_until_idle(timeout_s=60)

# Realtime layer on its own fake BSC node, with a few blocks of WBNB-like traffic.
fake = B.FakeBSC(start=1000)
node = B.FakeNode(fake); node.run()
svc = ChainService(emp.store, chain, wf, rpc=B.pool(fake))
svc.listener.ws_urls = [node.url]
svc.processor.confirmations = 2
svc.add_monitor(name="Treasury wallet", kind="address", target=B.WALLET)
svc.add_monitor(name="USDT large transfers", kind="token_transfer", target=B.TOKEN, min_value="1000")
svc.add_monitor(name="Pool swaps", kind="contract", target=B.POOL)
svc.start()
time.sleep(1.5)
for i in range(14):
    logs = [B.transfer(B.TOKEN, B.OTHER, B.POOL, 5000 + 137 * i)]
    if i % 3 == 0:
        logs.append(B.transfer(B.TOKEN, B.OTHER, B.WALLET, 2500 + 42 * i))
    txs = [{"from": B.WALLET, "to": B.OTHER, "value": 3 * 10**17}] if i == 5 else []
    fake.mine(txs=txs, logs=logs)
    node.push_head()
    time.sleep(0.3)
time.sleep(2)
wf.start()

def miner():
    i = 0
    while True:
        time.sleep(1.2)
        i += 1
        logs = [B.transfer(B.TOKEN, B.OTHER, B.POOL, 7000 + 91 * i)]
        if i % 2 == 0:
            logs.append(B.transfer(B.TOKEN, B.OTHER, B.WALLET, 1800 + 37 * i))
        fake.mine(logs=logs)
        node.push_head()
threading.Thread(target=miner, daemon=True).start()

# Autonomous workers (/api/at): the tests' scripted provider (tests/support/autonomy_brain.py) answers instead of a model,
# on a local copy of examples/buggy-service. One error log runs the whole hand-off and leaves a patch waiting for approval.
from autonomy_kit import ScriptedProvider, git_repo
from agent.autonomy.service import Autonomy
from agent.autonomy.worker import Worker
repos = tmp / "repos"; repos.mkdir()
repo = git_repo(repos / "pricing-service", ROOT / "examples" / "buggy-service")
at = Autonomy(wf, env={"KAGOU_LOCAL_REPO_ROOTS": str(repos)}, providers={"anthropic": ScriptedProvider()}, data_dir=tmp, network_isolation=False)
worker = Worker(at, worker_id="wkr-demo")
worker.db.heartbeat(worker.id, running=[])
aws = at.create_workspace({"name": "BSC Treasury", "description": "国库钱包、代币合约和定价服务", "resources": {
    "repos": [{"name": "pricing-service", "provider": "local", "path": str(repo), "branch": "main", "test_command": "python -m unittest -q"}],
    "tokens": [{"address": B.TOKEN, "symbol": "USDT", "network": "bsc-testnet"}], "wallets": [{"address": B.WALLET, "label": "Treasury", "network": "bsc-testnet"}]}}, "owner")
def worker_(tpl, name, shape, color, **kw):
    return at.create_agent({"template": tpl, "name": name, "workspace_ids": [aws["id"]], "activate": True, "appearance": {"shape": shape, "color": color}, **kw}, "owner")
worker_("bug_hunter", "Chain Monitor", "pulse", "coral")
worker_("investigator", "Contract Analyst", "shield", "azure")
worker_("fix_agent", "Fix Engineer", "grid", "emerald")
worker_("token_watcher", "Wallet Monitor", "orbit", "gold")
worker_("research_agent", "Chain Researcher", "node", "slate")
# The goal workflow's employees above are workers too: on demand, with their job written down.
for a, resp, shape, color in ((mgr, "把用户的目标拆成步骤，分派给合适的员工，并汇总结果。", "hex", "gold"),
                              (wallet, "按需分析钱包：余额、代币持仓和交易记录，只陈述链上事实。", "orbit", "azure"),
                              (contract, "按需检查代币合约的权限：owner、增发、暂停和时间锁。", "shield", "emerald"),
                              (risk, "综合其他员工的结果，给出有依据的风险结论。", "prism", "coral"),
                              (reporter, "把分析结果整理成报告。", "grid", "slate")):
    at.update_agent(a["id"], {"responsibility": resp, "work_mode": "on_demand", "appearance": {"shape": shape, "color": color}}, "owner")
    at.activate(a["id"], "owner")
at.create_agent({"name": "Airdrop Checker", "appearance": {"shape": "spark", "color": "amber"}}, "owner")  # a draft, not activated
w = next(x for x in at.db.watchers(workspace_id=aws["id"], kind="app_logs")) if at.db.watchers(workspace_id=aws["id"], kind="app_logs") else \
    at.db.watcher(at.watchers.create({"workspace_id": aws["id"], "kind": "app_logs", "config": {"service": "pricing"}}, "owner")[0]["id"])
at.watchers.ingest_log(w, {"level": "error", "message": "ZeroDivisionError: division by zero",
                           "stack": 'Traceback\n  File "pricing.py", line 14, in average_price\nZeroDivisionError'})
# Two chats, as a person would start them: one routed by KAGOU, one with a chosen agent. Each message is a real task.
monitor = next(a for a in at.db.agents() if a["name"] == "Wallet Monitor")
at.chats.create({"text": "看一下国库钱包最近的 USDT 转账，有没有异常？", "kind": "analyze"}, "owner")
at.chats.create({"text": "总结一下 USDT 最近的大额资金流向", "agent_id": monitor["id"], "kind": "analyze"}, "owner")
worker.drain(60)
def beat():
    # Keeps the worker alive and runs whatever is queued (new chat messages included) with the scripted model.
    while True:
        worker.db.heartbeat(worker.id, running=[]); worker.drain(10); time.sleep(2)
threading.Thread(target=beat, daemon=True).start()

app = create_app(emp, "t" * 24, chain, workforce=wf, chain_service=svc, autonomy=at)
uvicorn.run(app, host="127.0.0.1", port=8766, log_level="warning")
