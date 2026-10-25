"""Live-mode check against a real KAGOU server process: real HTTP, real SSE, a real SQLite file, a real restart.

    python scripts/live_check.py                     # starts its own server on a temporary database, no chain
    python scripts/live_check.py --chain             # same, with config/chain.example.json (BSC testnet, needs network)
    python scripts/live_check.py --sim-chain         # same, with a local simulated node (tests' FakeChain): no network,
                                                     # and the results say the chain data is simulated
    python scripts/live_check.py --url https://app.example.com --token ...   # an already running server (no restart step)

What it does, in order, and records as pass/fail with details (JSON with --out):
  readiness and capabilities (/readyz, /api/system, /api/session), CORS preflight when --origin is given,
  the SSE stream (/api/wf/stream) opened before any change, agent create and edit, workspace with a squad,
  mission create (submitted twice with one Idempotency-Key: one mission), mission read until it ends or the wait runs out,
  the lifecycle events the stream delivered, then (own server only) a restart on the same database file and a re-read.

It writes only to the server it is pointed at: two agents, one workspace and one mission named "live-check …".
No model key is needed; without one the mission ends the way a real server ends it (the report says which), and nothing
here pretends otherwise. Without --chain the server has no chain, so on-chain skills report that and the mission fails.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
WBNB_TESTNET = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"
TERMINAL = {"completed", "failed", "cancelled", "review"}  # review: the result waits for a person


class Check:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, name: str, ok: bool, detail: object = "") -> bool:
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail != "" else ""), flush=True)
        return ok


class Server:
    """uvicorn backend.app:app as a child process on a database file that survives restarts."""

    def __init__(self, data: Path, token: str, chain: str | None, port: int) -> None:
        self.port, self.proc = port, None
        policy = data / "policy.json"
        if not policy.exists():
            policy.write_text((ROOT / "config" / "policy.example.json").read_text())
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CHAINSTAFF_", "KAGOU_"))}
        self.env.update(CHAINSTAFF_CONSOLE_TOKEN=token, CHAINSTAFF_DB=str(data / "kagou.db"), CHAINSTAFF_POLICY=str(policy),
                        KAGOU_BACKGROUND="0", KAGOU_CHAIN_LISTENER="1" if chain == "testnet" else "0", PYTHONUNBUFFERED="1")
        if chain == "testnet":
            self.env["CHAINSTAFF_CHAIN"] = str(ROOT / "config" / "chain.example.json")
        elif chain:  # a chain.json written for the simulated node
            self.env["CHAINSTAFF_CHAIN"] = chain
        self.log = data / "server.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 30) -> float:
        t0 = time.monotonic()
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(self.port),
                                      "--workers", "1", "--timeout-graceful-shutdown", "3"], cwd=ROOT, env=self.env, stdout=self.log.open("a"), stderr=subprocess.STDOUT)
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited with {self.proc.returncode}; see {self.log}")
            try:
                if httpx.get(self.url + "/readyz", timeout=1).status_code == 200:
                    return time.monotonic() - t0
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"server not ready after {timeout}s; see {self.log}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def sim_chain(data: Path) -> tuple[str, str]:
    """A local JSON-RPC node answering like BSC testnet from tests/test_workforce.FakeChain. Returns (chain.json, token address)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sys.path.insert(0, str(ROOT / "tests"))
    from test_workforce import TOKEN, FakeChain

    fake = FakeChain()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), **fake("sim", body)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    cfg = json.loads((ROOT / "config" / "chain.example.json").read_text())
    cfg.update(network="bsc-testnet (simulated)", rpc_url=url, rpc_urls=[], ws_urls=[], explorer_api_url=None)
    path = data / "chain.sim.json"
    path.write_text(json.dumps(cfg))
    return str(path), TOKEN


class Stream(threading.Thread):
    """Reads /api/wf/stream the way the console does (fetch with a bearer header) and keeps every message."""

    def __init__(self, url: str, token: str, after: int) -> None:
        super().__init__(daemon=True)
        self.url, self.token, self.after = url, token, after
        self.messages: list[dict] = []
        self.connected = threading.Event()
        self.error: str | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            with httpx.stream("GET", f"{self.url}/api/wf/stream?after={self.after}", headers={"Authorization": f"Bearer {self.token}"},
                              timeout=httpx.Timeout(10, read=30)) as r:
                if r.status_code != 200:
                    self.error = f"HTTP {r.status_code}"
                    return
                self.connected.set()
                for line in r.iter_lines():
                    if self._stop.is_set():
                        return
                    if line.startswith("data: "):
                        m = json.loads(line[6:])
                        m["_recv"] = time.time()
                        self.messages.append(m)
        except Exception as e:  # noqa: BLE001 - recorded, not hidden
            if not self._stop.is_set():
                self.error = f"{type(e).__name__}: {e}"

    def stop(self) -> None:
        self._stop.set()

    def events(self, task_ids: set[str]) -> list[str]:
        return [m["event"] for m in self.messages if m.get("task_id") in task_ids or m.get("root_id") in task_ids]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="an already running server; omit to start one")
    ap.add_argument("--token", default=os.environ.get("CHAINSTAFF_CONSOLE_TOKEN"))
    ap.add_argument("--chain", action="store_true", help="own server only: run with config/chain.example.json (BSC testnet)")
    ap.add_argument("--sim-chain", action="store_true", help="own server only: run against a local simulated node (no network)")
    ap.add_argument("--origin", help="also check CORS for this console origin")
    ap.add_argument("--wait", type=float, default=90, help="seconds to wait for the mission to end")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", help="write the results as JSON here")
    a = ap.parse_args()

    check = Check()
    server = None
    data = Path(tempfile.mkdtemp(prefix="kagou-live-"))
    token = a.token if a.url else secrets.token_urlsafe(24)
    if a.url and not token:
        ap.error("--token (or CHAINSTAFF_CONSOLE_TOKEN) is needed with --url")
    chain, target = None, WBNB_TESTNET
    if a.sim_chain:
        chain, target = sim_chain(data)
    elif a.chain:
        chain = "testnet"
    if not a.url:
        server = Server(data, token, chain, a.port)
        check("server starts and /readyz answers", True, f"{server.start():.1f}s, database {data / 'kagou.db'}")
    base = (a.url or server.url).rstrip("/")
    H = {"Authorization": f"Bearer {token}"}
    c = httpx.Client(base_url=base, headers=H, timeout=30)
    run_id = uuid.uuid4().hex[:6]
    stream = None
    try:
        check("/readyz", c.get("/readyz").json() == {"ok": True})
        check("unauthenticated API call is refused", httpx.get(base + "/api/wf/agents", timeout=10).status_code == 401)
        system = c.get("/api/system").json()
        check("/api/system answers in live mode", system.get("mode") == "live", {k: system.get(k) for k in ("database", "model", "chain", "writes")})
        check("session role", c.get("/api/session").json().get("role") == "owner")
        if a.origin:
            pre = httpx.options(base + "/api/wf/agents", headers={"Origin": a.origin, "Access-Control-Request-Method": "POST",
                                                                   "Access-Control-Request-Headers": "authorization,content-type"}, timeout=10)
            check(f"CORS preflight for {a.origin}", pre.headers.get("access-control-allow-origin") == a.origin, pre.status_code)

        meta = c.get("/api/wf/meta").json()
        stream = Stream(base, token, meta["last_message_id"])
        stream.start()
        check("SSE stream connects", stream.connected.wait(10), stream.error or f"after id {meta['last_message_id']}")

        # ---- agents ----
        perms = sorted({p for s in meta["skills"] if s["id"] in ("wallet_analysis", "token_info", "data_report") for p in s["permissions"]})
        manager = c.post("/api/wf/agents", json={"name": f"live-check manager {run_id}", "role": "Manager", "is_manager": True, "provider": "none",
                                                 "skills": ["data_report"], "permissions": perms}).json()
        analyst = c.post("/api/wf/agents", json={"name": f"live-check analyst {run_id}", "role": "Analyst", "provider": "none",
                                                 "skills": ["wallet_analysis", "token_info"], "permissions": perms, "budget_usd": "0.5"}).json()
        check("agent create", bool(manager.get("id")) and bool(analyst.get("id")), f"{manager.get('id')}, {analyst.get('id')}")
        edit = {"role": "Wallet analyst", "system_prompt": f"live-check prompt {run_id}", "budget_usd": "0.75"}
        c.put(f"/api/wf/agents/{analyst['id']}", json=edit).raise_for_status()
        got = c.get(f"/api/wf/agents/{analyst['id']}").json()
        check("agent edit is read back", got["role"] == edit["role"] and got["system_prompt"] == edit["system_prompt"]
              and float(got["budget_usd"]) == 0.75, {k: got[k] for k in ("role", "budget_usd")})

        # ---- mission ----
        ws = c.post("/api/wf/workspaces", json={"name": f"live-check {run_id}", "manager_id": manager["id"], "members": [analyst["id"]]}).json()
        goal = f"live-check {run_id}: analyse token {target}"
        pf = c.post("/api/wf/missions/preflight", json={"workspace_id": ws["id"], "text": goal}).json()
        check("mission preflight", True, [f"{x['level']}: {x['text']}" for x in pf.get("checks", []) if x["level"] != "ok"] or "all ok")
        key = str(uuid.uuid4())
        r1 = c.post(f"/api/wf/workspaces/{ws['id']}/goals", json={"text": goal, "budget_usd": "0.5", "timeout_s": 300}, headers={"Idempotency-Key": key})
        if not check("mission create", r1.status_code == 200, r1.status_code if r1.status_code == 200 else r1.text[:300]):
            mission_id = None
        else:
            mission_id = r1.json()["id"]
            r2 = c.post(f"/api/wf/workspaces/{ws['id']}/goals", json={"text": goal}, headers={"Idempotency-Key": key})
            check("same Idempotency-Key returns the same mission", r2.json().get("id") == mission_id, f"{mission_id} / {r2.json().get('id')}")
        status, mission = None, None
        if mission_id:
            t0 = time.monotonic()
            while time.monotonic() - t0 < a.wait:
                mission = c.get(f"/api/wf/missions/{mission_id}").json()
                status = mission.get("status")
                if status in TERMINAL:
                    break
                time.sleep(1)
            check("mission read until it ends", status in TERMINAL, f"{status} after {time.monotonic() - t0:.1f}s; "
                  f"error: {(mission or {}).get('error') or '-'}")
            check("proof state before review", (mission or {}).get("proof", {}).get("state") in ("awaiting_review", "not_completed"),
                  (mission or {}).get("proof", {}).get("state"))
            if status == "review":
                pending = [x for x in c.get("/api/wf/approvals", params={"status": "pending"}).json() if x.get("task_id") in
                           {mission_id} | {t["id"] for t in mission.get("tasks", [])}]
                for x in pending:
                    c.post(f"/api/wf/approvals/{x['id']}", json={"approve": True, "note": "live-check", "anchor": False}).raise_for_status()
                time.sleep(1)
                mission = c.get(f"/api/wf/missions/{mission_id}").json()
                status = mission["status"]
                state = mission["proof"]["state"]
                # Approved without an anchor transaction: completed off-chain, and never labelled on-chain.
                check("approval completes the mission off-chain", status == "completed" and state == "offchain_completed",
                      f"{len(pending)} approval(s); {status} / {state}")
            time.sleep(1.5)  # the stream polls every 0.5 s
            ids = {mission_id} | {t["id"] for t in (mission or {}).get("tasks", [])}
            evs = stream.events(ids)
            check("SSE delivered the mission's events", "task.created" in evs, evs[:40])

        # ---- restart on the same file ----
        if server:
            stream.stop()
            server.stop()
            took = server.start()
            check("restart on the same database file", True, f"ready in {took:.1f}s")
            got = c.get(f"/api/wf/agents/{analyst['id']}").json()
            check("agent edit survives the restart", got.get("system_prompt") == edit["system_prompt"])
            if mission_id:
                again = c.get(f"/api/wf/missions/{mission_id}").json()
                check("mission survives the restart", again.get("id") == mission_id and again.get("status") == status,
                      f"{again.get('status')} (was {status})")
                r3 = c.post(f"/api/wf/workspaces/{ws['id']}/goals", json={"text": goal}, headers={"Idempotency-Key": key})
                check("idempotency survives the restart", r3.json().get("id") == mission_id)
    except Exception as e:  # noqa: BLE001
        check("run finished without an exception", False, f"{type(e).__name__}: {e}")
    finally:
        if stream:
            stream.stop()
        if server:
            server.stop()
    failed = [r for r in check.rows if not r["ok"]]
    out = {"server": base, "own_server": server is not None, "chain": ("simulated" if a.sim_chain else "bsc-testnet" if a.chain else "none") if server else "remote", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "passed": len(check.rows) - len(failed), "failed": len(failed), "checks": check.rows}
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n{out['passed']} passed, {out['failed']} failed" + (f"; server log {server.log}" if server else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
