"""Run the API server and the worker in one container and restart whichever exits.

    python -m agent.autonomy.supervise

A platform restart policy (Railway, Docker restart=always) restarts the whole container; this restarts one process
without dropping the other. Restarts back off (1 s doubling to 60 s) and reset after a process stays up 5 minutes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def commands() -> dict[str, list[str]]:
    # Fixed argv, built from constants only: the platform's PORT reaches uvicorn as UVICORN_PORT (see child_env).
    return {"api": [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--workers", "1",
                    "--timeout-graceful-shutdown", "10", "--proxy-headers", "--forwarded-allow-ips", "*"],
            "worker": [sys.executable, "-m", "agent.autonomy.worker"]}


def wanted(env: dict) -> list[str]:
    """KAGOU_WORKER=0 runs the API alone (e.g. when the worker is deployed separately)."""
    return ["api"] if env.get("KAGOU_WORKER", "1") == "0" else ["api", "worker"]


def child_env(env: dict) -> dict:
    port = str(env.get("PORT") or "8000")
    if not port.isdigit():
        raise SystemExit(f"[supervise] PORT must be a number, got {port!r}")
    return {**env, "UVICORN_PORT": port}


def main() -> None:  # pragma: no cover - process entry point; the restart logic is the loop below
    env = child_env(dict(os.environ))
    cmds = commands()
    if "worker" not in wanted(env):
        del cmds["worker"]
    procs: dict[str, subprocess.Popen] = {}
    started: dict[str, float] = {}
    backoff = {k: 1.0 for k in cmds}
    due = {k: 0.0 for k in cmds}
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for p in procs.values():
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        now = time.monotonic()
        for name, cmd in cmds.items():
            p = procs.get(name)
            if p is not None and p.poll() is None:
                if now - started[name] > 300:
                    backoff[name] = 1.0
                continue
            if p is not None:
                print(f"[supervise] {name} exited with {p.returncode}; restarting in {backoff[name]:.0f}s", flush=True)
                due[name] = now + backoff[name]
                backoff[name] = min(60.0, backoff[name] * 2)
                procs.pop(name)
                continue
            if now >= due[name]:
                procs[name] = subprocess.Popen(cmd, env=env)
                started[name] = now
                print(f"[supervise] started {name} (pid {procs[name].pid})", flush=True)
        time.sleep(0.5)
    for p in procs.values():
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":  # pragma: no cover
    main()
