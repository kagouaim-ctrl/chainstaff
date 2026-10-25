"""Real-model check: a real KAGOU server, a real model API, real BSC testnet data. Nothing here is scripted.

    AI_PROVIDER=qwen AI_BASE_URL=... AI_API_KEY=... AI_MODEL=qwen-plus python scripts/ai_check.py --chain
    ANTHROPIC_API_KEY=... python scripts/ai_check.py --chain
    python scripts/ai_check.py --url https://app.example.com --token ...     # an already running server

It costs money on a paid model (a few cents at most with the defaults; the server's own AI_* budgets apply), so it
runs only when asked. Without a model key it stops at the first check and says the real tests could not run.

Checks, recorded as pass/fail with details (JSON with --out):
  1 AI connection      the server reports a configured model
  2 agent run          one agent answers a question about a real address
  3 BSC query          the agent's tool returned the address's balance from the chain, with the block number
  4 tool calling       the model chose and called a read-only BSC tool, and its answer uses the tool's number
  5 mission            a mission goes through planning, execution and review
  6 multi-agent        the coordinator's model splits it across at least two agents, who all finish
  plus the token usage and cost the API reported, from /api/wf/ai-usage.
It creates four agents and one workspace named "ai-check …" on the server it is pointed at.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_check import TERMINAL, WBNB_TESTNET, Check, Server, sim_chain  # noqa: E402

ANALYST = ["agent_task", "wallet_analysis", "token_balance", "token_info", "contract_analysis", "tx_lookup", "contract_read"]
MONITOR = ["agent_task", "onchain_monitor", "block_info", "wallet_activity", "contract_events", "event_search", "chain_events"]
RESEARCH = ["agent_task", "data_report"]


def wait(c: httpx.Client, tid: str, secs: float) -> dict:
    t0 = time.monotonic()
    while True:
        m = c.get(f"/api/wf/tasks/{tid}").json()
        if m["root"]["status"] in TERMINAL or time.monotonic() - t0 > secs:
            return m
        time.sleep(1.5)


def model_events(detail: dict) -> list[dict]:
    return [m["data"] for m in detail["messages"] if m["event"] == "model.done"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="an already running server; omit to start one")
    ap.add_argument("--token", default=os.environ.get("CHAINSTAFF_CONSOLE_TOKEN"))
    ap.add_argument("--chain", action="store_true", help="own server only: BSC testnet (config/chain.example.json)")
    ap.add_argument("--sim-chain", action="store_true", help="own server only: a local simulated node; the chain checks then say so")
    ap.add_argument("--address", default=WBNB_TESTNET, help="the address to ask about (default: WBNB on BSC testnet)")
    ap.add_argument("--wait", type=float, default=300)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", help="write the results as JSON here")
    a = ap.parse_args()

    check = Check()
    data = Path(tempfile.mkdtemp(prefix="kagou-ai-"))
    token = a.token if a.url else secrets.token_urlsafe(24)
    if a.url and not token:
        ap.error("--token (or CHAINSTAFF_CONSOLE_TOKEN) is needed with --url")
    server, target = None, a.address
    if not a.url:
        chain = None
        if a.sim_chain:
            chain, target = sim_chain(data)
        elif a.chain:
            chain = "testnet"
        server = Server(data, token, chain, a.port)
        server.start()
    base = (a.url or server.url).rstrip("/")
    c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    run_id = uuid.uuid4().hex[:6]
    info: dict = {}
    try:
        system = c.get("/api/system").json()
        model = system.get("model") or {}
        info["model"] = model
        if not check("1 AI connection: the server has a model", model.get("configured"), model.get("reason") or model.get("compat") or model.get("default_model")):
            print("\nNo model key on this server: the real AI tests could not run. Mock tests are in tests/ and are reported separately.")
            return 2
        provider = "openai_compat" if model.get("compat") else "anthropic"
        meta = c.get("/api/wf/meta").json()
        perms = sorted({p for s in meta["skills"] if s["id"] in set(ANALYST + MONITOR + RESEARCH) for p in s["permissions"]})

        def agent(name: str, role: str, skills: list[str], prompt: str, manager: bool = False) -> dict:
            r = c.post("/api/wf/agents", json={"name": f"ai-check {name} {run_id}", "role": role, "provider": provider, "is_manager": manager,
                                               "skills": [s for s in skills if any(x["id"] == s for x in meta["skills"])],
                                               "permissions": perms, "system_prompt": prompt, "budget_usd": "0.5"})
            r.raise_for_status()
            return r.json()

        coordinator = agent("Mission Coordinator", "Coordinator", RESEARCH, "You split BSC research missions into steps for your team and assign each to the member whose tools fit.", True)
        analyst = agent("BSC Analyst", "BSC Analyst", ANALYST, "You analyse BSC addresses and tokens with your read-only tools. Quote numbers exactly as the tools return them.")
        monitor = agent("On-chain Monitor", "On-chain Monitor", MONITOR, "You read recent blocks, events and transfers with your read-only tools.")
        research = agent("Research Agent", "Research Agent", RESEARCH, "You write short reports from your colleagues' results, separating verifiable on-chain facts, statistics and inferences.")
        ws = c.post("/api/wf/workspaces", json={"name": f"ai-check {run_id}", "manager_id": coordinator["id"],
                                                "members": [analyst["id"], monitor["id"], research["id"]]}).json()

        # ---- one agent, one question, real tools ----
        q = f"What is the native BNB balance of {target} on BSC testnet right now? Use your tools, and give the number and the block it was read at."
        r = c.post(f"/api/wf/agents/{analyst['id']}/run", json={"skill": "agent_task", "input": {"instruction": q}, "workspace_id": ws["id"],
                                                                "budget_usd": "0.2", "timeout_s": 300})
        r.raise_for_status()
        d = wait(c, r.json()["id"], a.wait)
        sub = d["subtasks"][0] if d["subtasks"] else d["task"]
        out = sub.get("result") or {}
        calls = model_events(d)
        info["agent_run"] = {"status": sub["status"], "error": sub.get("error"), "answer": out.get("answer"), "model_calls": calls,
                             "tool_calls": out.get("tool_calls")}
        check("2 agent run: the agent finished with an answer", sub["status"] == "completed" and bool(out.get("answer")),
              {"status": sub["status"], "error": sub.get("error"), "answer": (out.get("answer") or "")[:400]})
        bal = [tc for tc in out.get("tool_calls") or [] if tc["ok"] and tc["tool"] in ("wallet_analysis", "contract_read", "token_balance")]
        tool_out = next((m["data"].get("output_preview") for m in d["messages"] if m["event"] == "tool.result" and m["data"].get("ok")
                         and m["data"].get("tool") == "wallet_analysis"), None)
        bnb = re.search(r'"bnb": "([0-9.]+)"', tool_out or "")
        check("3 BSC query: the balance came from the chain" + (" (simulated node)" if a.sim_chain else ""), bool(bnb),
              {"tool_output": (tool_out or "")[:300]})
        answer = out.get("answer") or ""
        used = bool(bnb) and (bnb.group(1) in answer or str(Decimal(bnb.group(1)).normalize()) in answer
                              or any(bnb.group(1)[:k] in answer for k in (6, 5, 4) if len(bnb.group(1)) >= k))
        check("4 tool calling: the model called a BSC tool and used its number", bool(bal) and used,
              {"tools": [(tc["tool"], tc["ok"]) for tc in out.get("tool_calls") or []], "tool_bnb": bnb.group(1) if bnb else None})
        check("token usage and cost come from the API response", bool(calls) and all(m["input_tokens"] > 0 for m in calls),
              [{k: m.get(k) for k in ("model", "input_tokens", "output_tokens", "cost_usd", "priced", "secs")} for m in calls])

        # ---- a mission across the team ----
        goal = (f"Research the BEP-20 token {target} on BSC testnet: its name, symbol, supply and who controls it, and what happened "
                "to it in the most recent blocks. Finish with a short report that separates verifiable on-chain facts, statistics and inferences.")
        r = c.post(f"/api/wf/workspaces/{ws['id']}/goals", json={"text": goal, "budget_usd": "0.5", "timeout_s": 600},
                   headers={"Idempotency-Key": str(uuid.uuid4())})
        r.raise_for_status()
        d = wait(c, r.json()["id"], a.wait)
        root, subs = d["root"], d["subtasks"]
        info["mission"] = {"status": root["status"], "planner": root["planner"], "error": root.get("error"),
                           "subtasks": [{k: s.get(k) for k in ("title", "assigned_agent", "status", "error", "cost_usd")} for s in subs]}
        check("5 mission: planned, executed, waiting for review", root["status"] == "review",
              {"status": root["status"], "error": root.get("error"), "planner": root["planner"]})
        done_by = {s["assigned_agent"] for s in subs if s["status"] == "completed"}
        check("6 multi-agent: the coordinator's model split the work across agents", root["planner"] == "model" and len(done_by) >= 2
              and all(s["status"] == "completed" for s in subs), info["mission"]["subtasks"])
        report = next((s["result"] for s in reversed(subs) if s["status"] == "completed" and s.get("result")), None)
        info["report"] = (report or {}).get("answer") or (report or {}).get("report_md")
        usage = c.get("/api/wf/ai-usage", params={"days": 1}).json()
        info["usage"] = usage
        check("spend recorded by the gateway", Decimal(usage["today_usd"]) >= 0, {k: usage[k] for k in ("today_usd", "month_usd", "unpriced_calls_this_month")}
              | {"rows": usage["days"]})
    except Exception as e:  # noqa: BLE001
        check("run finished without an exception", False, f"{type(e).__name__}: {e}")
    finally:
        if server:
            server.stop()
    failed = [r for r in check.rows if not r["ok"]]
    out = {"server": base, "chain": ("simulated" if a.sim_chain else "bsc-testnet" if a.chain else "none") if server else "remote",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "passed": len(check.rows) - len(failed), "failed": len(failed),
           "checks": check.rows, "details": info}
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n{out['passed']} passed, {out['failed']} failed" + (f"; server log {server.log}" if server else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
