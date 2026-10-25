"""End-to-end check of the autonomous agents across real processes: the API server (uvicorn), a separate worker process
(python -m agent.autonomy.worker) and an OpenAI-compatible model endpoint, all on one temporary SQLite file.

    python scripts/autonomy_check.py                       # scripted model endpoint (a mock; the report says so)
    python scripts/autonomy_check.py --out autonomy.json   # also write the results as JSON
    AI_PROVIDER=anthropic ANTHROPIC_API_KEY=... python scripts/autonomy_check.py --real-model   # a real model
    python scripts/autonomy_check.py --testnet --chain-wait 300   # also a Token Watcher on BSC testnet (needs network)

What it does, in order, and records as pass/fail:
  1. bug workflow: a local copy of examples/buggy-service, three agents (bug hunter, investigator, fix agent) and an
     app-log watcher; one error log goes to the ingest endpoint, the same error again is deduplicated; the worker wakes
     the bug hunter, which hands the investigation on, which hands the fix on; the fix agent edits an isolated copy,
     runs the tests and submits a patch; the original repository stays untouched until the approval is granted through
     the API; then the patch is applied and a verify task re-runs the tests.
  2. crash recovery: a task is running inside the worker when the worker process is killed (SIGKILL); a new worker
     process finds the expired lease, marks the interrupted run and finishes the task.
  3. runtime health and activity: the worker heartbeat shows on /api/at/runtime, and every step above is in the
     activity log with who triggered it.
  4. (--testnet) Token Watcher: the server follows BSC testnet (config/chain.example.json, public nodes, read only) and a
     bsc_events watcher on testnet WBNB (every transfer counted as large, so each one wakes the agent); confirmed
     transfers become events, wake the Token Watcher agent, and its tasks finish with a facts-only report. This shows minutes of listening, not a long-running soak.

With the scripted endpoint, the model answers come from fixed rules (tests/support/autonomy_brain.py), so the results
prove the runtime (queue, wake-up, delegation, sandbox, approvals, recovery), not model quality. Nothing here touches a
chain, GitHub or any production system.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "tests" / "support" / "scripted_autonomy_server.py"
WBNB_TESTNET = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"
ERROR = {"level": "error", "message": "ZeroDivisionError: division by zero",
         "stack": 'Traceback (most recent call last):\n  File "pricing.py", line 14, in average_price\nZeroDivisionError: division by zero'}


class Check:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, name: str, ok: bool, detail: object = "") -> bool:
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail != "" else ""), flush=True)
        return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=check", "-c", "user.email=check@kagou.invalid", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def tests_pass(repo: Path) -> bool:
    # No bytecode files: an untracked __pycache__ would make the repository look modified to the patch step.
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "-q"], cwd=repo, capture_output=True).returncode == 0


class Proc:
    def __init__(self, name: str, cmd: list[str], env: dict, log: Path) -> None:
        self.name, self.cmd, self.env, self.log, self.p = name, cmd, env, log, None

    def start(self) -> "Proc":
        self.p = subprocess.Popen(self.cmd, cwd=ROOT, env=self.env, stdout=self.log.open("a"), stderr=subprocess.STDOUT)
        return self

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.alive():
            self.p.send_signal(sig)
            try:
                self.p.wait(15)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()


def wait_for(fn, timeout: float, step: float = 0.3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--real-model", action="store_true", help="use AI_PROVIDER / API key from the environment instead of the scripted endpoint")
    ap.add_argument("--wait", type=float, default=90, help="seconds to wait for each phase")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory")
    ap.add_argument("--testnet", action="store_true", help="also run a Token Watcher on BSC testnet (public nodes, read only)")
    ap.add_argument("--chain-wait", type=float, default=240, help="seconds to wait for testnet transfers")
    args = ap.parse_args()
    check = Check()
    data = Path(tempfile.mkdtemp(prefix="autonomy-check-"))
    repo = data / "repos" / "buggy-service"
    shutil.copytree(ROOT / "examples" / "buggy-service", repo, ignore=shutil.ignore_patterns("__pycache__"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "buggy service")
    head0 = git(repo, "rev-parse", "HEAD").strip()

    token = "check-" + os.urandom(12).hex()
    port, brain_port = free_port(), free_port()
    policy = data / "policy.json"
    policy.write_text((ROOT / "config" / "policy.example.json").read_text())
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CHAINSTAFF_", "KAGOU_"))}
    env.update(CHAINSTAFF_CONSOLE_TOKEN=token, CHAINSTAFF_DB=str(data / "kagou.db"), CHAINSTAFF_POLICY=str(policy), KAGOU_BACKGROUND="0",
               KAGOU_CHAIN_LISTENER="0", KAGOU_DATA_DIR=str(data), KAGOU_LOCAL_REPO_ROOTS=str(data / "repos"), KAGOU_LEASE_S="10",
               KAGOU_HEARTBEAT_S="1", KAGOU_WORKER_STALE_S="10", PYTHONUNBUFFERED="1")
    if args.testnet:
        env.update(KAGOU_CHAIN_LISTENER="1", CHAINSTAFF_CHAIN=str(ROOT / "config" / "chain.example.json"))
    mode = "real model" if args.real_model else "scripted endpoint (mock model)"
    if not args.real_model:
        env.pop("ANTHROPIC_API_KEY", None)
        env.update(AI_PROVIDER="local", AI_BASE_URL=f"http://127.0.0.1:{brain_port}/v1", AI_MODEL="scripted")
    print(f"model: {mode}; data: {data}", flush=True)

    api = Proc("api", [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"], env, data / "api.log")
    worker = Proc("worker", [sys.executable, "-m", "agent.autonomy.worker"], env, data / "worker.log")
    brain = Proc("brain", [sys.executable, str(BRAIN), str(brain_port)], env, data / "brain.log")
    procs = [api, worker, brain]
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def j(r: httpx.Response):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def tasks(**q) -> list[dict]:
        return j(c.get("/api/at/tasks", params={"limit": 200, **q}))["items"]

    try:
        if not args.real_model:
            brain.start()
        api.start()
        check("api server ready", bool(wait_for(lambda: _ready(c), 30)), f"port {port}")
        worker.start()
        rt = wait_for(lambda: (lambda r: r if r.get("worker_alive") else None)(j(c.get("/api/at/runtime"))), 30)
        check("separate worker process is alive (heartbeat in the database)", bool(rt), (rt or {}).get("workers", [{}])[0].get("id", "") if rt else "no heartbeat")

        # ---- 1. bug workflow
        ws = j(c.post("/api/at/workspaces", json={"name": "Pricing service", "resources": {"repos": [
            {"name": "buggy-service", "provider": "local", "path": str(repo), "branch": "main", "test_command": "python -m unittest -q"}]}}))
        ids = {}
        for tpl, name in (("bug_hunter", "Agent 1"), ("investigator", "Agent 2"), ("fix_agent", "Agent 3")):
            ids[name] = j(c.post("/api/at/agents", json={"template": tpl, "name": name, "workspace_ids": [ws["id"]], "activate": True}))["id"]
        w = j(c.post("/api/at/watchers", json={"workspace_id": ws["id"], "kind": "app_logs", "config": {"service": "pricing"}}))
        hook = lambda: httpx.post(f"http://127.0.0.1:{port}{w['ingest_path']}", headers={"Authorization": "Bearer " + w["secret"]}, json=ERROR, timeout=30)  # noqa: E731
        check("ingest without the watcher secret is refused", httpx.post(f"http://127.0.0.1:{port}{w['ingest_path']}", json=ERROR).status_code == 401)
        first = j(hook())["results"][0]
        again = j(hook())["results"][0]
        check("error log accepted as an event", first.get("accepted") and not first.get("deduplicated"), first.get("event_id"))
        check("the same error again is deduplicated", again.get("deduplicated") and again.get("event_id") == first.get("event_id"),
              f"occurrences {again.get('occurrences')}")

        # The patch approval appears when the fix agent submits it; the chain is done when the bug hunter's task (the root) is.
        settled = lambda: (lambda ts: ts and all(t["status"] in ("completed", "failed", "cancelled") for t in ts))(tasks(workspace_id=ws["id"]))  # noqa: E731
        wait_for(lambda: len(tasks(workspace_id=ws["id"])) >= 3 and settled(), args.wait)
        ap_row = (j(c.get("/api/at/approvals")) or [None])[0]
        chain = {t["to_agent_id"]: t for t in tasks(workspace_id=ws["id"])}
        t1, t2, t3 = (chain.get(ids[n]) or {} for n in ("Agent 1", "Agent 2", "Agent 3"))
        check("bug hunter woke on the event and delegated", t1.get("kind") == "investigate" and t2.get("from_agent_id") == ids["Agent 1"],
              f"{t1.get('id')} → {t2.get('id')}")
        check("investigator delegated the fix", t3.get("kind") == "fix" and t3.get("from_agent_id") == ids["Agent 2"] and t3.get("depth") == 2,
              f"{t2.get('id')} → {t3.get('id')}")
        check("fix agent ran the tests in its isolated copy", ((t3.get("result") or {}).get("tests") or {}).get("passed") is True,
              (t3.get("result") or {}).get("tests"))
        check("patch waits for a human; the original repository is untouched", bool(ap_row) and ap_row["kind"] == "patch" and git(repo, "rev-parse", "HEAD").strip() == head0
              and not tests_pass(repo), f"approval #{ap_row['id']}" if ap_row else "no approval")
        dup = [t for t in tasks(workspace_id=ws["id"], kind="investigate") if t["to_agent_id"] == ids["Agent 1"]]
        check("one investigation for the duplicated error", len(dup) == 1, len(dup))
        if ap_row:
            out = j(c.post(f"/api/at/approvals/{ap_row['id']}", json={"approve": True, "note": "autonomy_check"}))
            check("approval applied the patch to the local repository", bool(out.get("applied_commit")) and tests_pass(repo), (out.get("applied_commit") or "")[:10])
            ver = wait_for(lambda: next((t for t in tasks(workspace_id=ws["id"], kind="verify") if t["status"] in ("completed", "failed")), None), args.wait)
            check("verify task re-ran the tests after the fix", bool(ver) and ver["status"] == "completed", (ver or {}).get("result", {}).get("summary") if ver else "no verify task")
        roots = {t["root_id"] for t in tasks(workspace_id=ws["id"]) if t["kind"] != "verify"}
        check("all tasks of the workflow finished", all(t["status"] == "completed" for t in tasks(workspace_id=ws["id"])), sorted(roots))

        # ---- 2. crash recovery
        if not args.real_model:
            brain.stop()
            brain = Proc("brain", [sys.executable, str(BRAIN), str(brain_port), "4"], env, data / "brain.log").start()
            procs.append(brain)
            time.sleep(0.5)
        solo = j(c.post("/api/at/agents", json={"name": "Solo", "responsibility": "处理交给你的任务。", "workspace_ids": [ws["id"]], "work_mode": "on_demand",
                                                "activate": True}))["id"]
        t = j(c.post(f"/api/at/agents/{solo}/tasks", json={"objective": "检查一下服务是否还有别的问题", "workspace_id": ws["id"]}))
        running = wait_for(lambda: (lambda x: x if x["status"] == "running" else None)(j(c.get(f"/api/at/tasks/{t['id']}"))["task"]), 30, 0.1)
        check("task is running inside the worker", bool(running), t["id"])
        time.sleep(1)
        worker.stop(signal.SIGKILL)
        check("worker process killed mid-run (SIGKILL)", not worker.alive(), f"exit {worker.p.returncode}")
        worker = Proc("worker", [sys.executable, "-m", "agent.autonomy.worker"], env, data / "worker.log").start()
        procs.append(worker)
        done = wait_for(lambda: (lambda x: x if x["task"]["status"] in ("completed", "failed") else None)(j(c.get(f"/api/at/tasks/{t['id']}"))), max(args.wait, 60))
        runs = (done or {}).get("runs") or []
        check("a new worker recovered the task after the lease expired", bool(done) and done["task"]["status"] == "completed",
              (done or {}).get("task", {}).get("status"))
        check("the interrupted run is recorded, the task ran again", any(r["status"] == "interrupted" for r in runs) and len(runs) >= 2,
              [r["status"] for r in runs])
        acts = j(c.get("/api/at/activity", params={"limit": 500}))
        items = acts["items"] if isinstance(acts, dict) else acts
        check("recovery is in the activity log", any(a["type"] == "worker.recovered" for a in items))

        # ---- 3. runtime and activity
        rt = j(c.get("/api/at/runtime"))
        check("runtime health shows the live worker", rt.get("worker_alive") is True, rt.get("level"))
        types = {a["type"] for a in items}
        need = {"event.received", "agent.awakened", "task.delegated", "approval.requested", "approval.decided", "task.completed"}
        check("activity log covers the whole workflow", need <= types, sorted(need - types) or "all present")
        check("every activity row says who triggered it", all(a.get("actor") for a in items), len(items))

        # ---- 4. Token Watcher on BSC testnet
        if args.testnet:
            tws = j(c.post("/api/at/workspaces", json={"name": "BSC testnet WBNB"}))
            tw = j(c.post("/api/at/agents", json={"template": "token_watcher", "name": "Token Watcher", "workspace_ids": [tws["id"]], "activate": True}))["id"]
            bw = j(c.post("/api/at/watchers", json={"workspace_id": tws["id"], "kind": "bsc_events", "config": {"address": WBNB_TESTNET, "symbol": "WBNB",
                                                                                                     # A plain transfer is recorded but wakes nobody (chain noise); a
                                                                                                     # threshold of 1 raw unit makes every real transfer "large" so the
                                                                                                     # wake-up path runs on real testnet data.
                                                                                                     "large_transfer_raw": "1"}}))["watcher"]
            check("bsc_events watcher created on the live listener", bw["status"] == "active", bw.get("last_error") or bw["id"])
            hb = wait_for(lambda: next((x for x in j(c.get("/api/at/watchers", params={"workspace_id": tws["id"]})) if x["healthy"]), None), 60, 1)
            check("watcher follows new testnet blocks", bool(hb), (hb or {}).get("health"))
            evs = wait_for(lambda: [e for e in j(c.get("/api/at/events", params={"workspace_id": tws["id"], "limit": 500})) if e.get("chain_status") == "confirmed"],
                           args.chain_wait, 3)
            check("confirmed WBNB transfers recorded as events", bool(evs), f"{len(evs)} confirmed, blocks {sorted({(e.get('payload') or {}).get('block') for e in evs})[:5]}")
            done_t = wait_for(lambda: [t for t in tasks(workspace_id=tws["id"]) if t["to_agent_id"] == tw and t["status"] == "completed"], args.wait, 2)
            check("Token Watcher woke on the events and finished its tasks", bool(done_t),
                  (done_t[0].get("result") or {}).get("summary", "")[:160] if done_t else [t["status"] for t in tasks(workspace_id=tws["id"])])
            st = j(c.get("/api/at/runtime")).get("chain") or {}
            check("listener status reported", bool(st), {k: st.get(k) for k in ("network", "head", "connected", "mode") if k in st})
    except Exception as e:  # report and fail instead of a traceback mid-run
        check("check ran to the end", False, f"{type(e).__name__}: {e}")
    finally:
        for p in procs:
            p.stop()
    failed = [r for r in check.rows if not r["ok"]]
    print(f"\n{len(check.rows) - len(failed)} passed, {len(failed)} failed (model: {mode})", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({"model": mode, "data_dir": str(data), "checks": check.rows}, ensure_ascii=False, indent=2))
    if failed:
        for name in ("api", "worker"):
            log = data / f"{name}.log"
            if log.exists():
                print(f"--- {name}.log (tail)\n" + "\n".join(log.read_text(errors="replace").splitlines()[-30:]))
    if not args.keep and not failed:
        shutil.rmtree(data, ignore_errors=True)
    return 1 if failed else 0


def _ready(c: httpx.Client) -> bool:
    try:
        return c.get("/readyz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


if __name__ == "__main__":
    sys.exit(main())
