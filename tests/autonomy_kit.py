"""Shared setup for the autonomy tests: a workforce on a temporary database, the Autonomy service with a scripted
provider (tests/support/autonomy_brain.py, a mock, not a model), and a local copy of examples/buggy-service."""

from __future__ import annotations

import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "support"))

from autonomy_brain import ScriptedProvider  # noqa: E402

from agent.autonomy.service import Autonomy  # noqa: E402
from agent.autonomy.worker import Worker  # noqa: E402
from agent.store import Store  # noqa: E402
from agent.workforce import Workforce  # noqa: E402
from agent.workforce.runtime import ModelTurn, ToolUse  # noqa: E402


def git_repo(dest: Path, src: Path | None = None) -> Path:
    if src is not None:
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True)
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", "--allow-empty"]):
        subprocess.run(c, cwd=dest, check=True, capture_output=True)
    return dest


class Kit:
    """One autonomy deployment in a temp folder."""

    def __init__(self, tmp: Path, *, provider=None, env: dict | None = None, http_get=None, github_post=None, providers: dict | None = None, **kw) -> None:
        self.tmp = tmp
        self.repos = tmp / "repos"
        self.repos.mkdir(exist_ok=True)
        self.repo = git_repo(self.repos / "buggy-service", ROOT / "examples" / "buggy-service")
        self.store = Store(str(tmp / "k.db"))
        self.wf = Workforce(self.store)
        self.env = {"KAGOU_LOCAL_REPO_ROOTS": str(self.repos), **(env or {})}
        self.provider = provider or ScriptedProvider()
        self.at = Autonomy(self.wf, env=self.env, providers=providers if providers is not None else {"anthropic": self.provider}, data_dir=tmp,
                           http_get=http_get, github_post=github_post, network_isolation=False, **kw)
        self.worker = Worker(self.at, worker_id="wkr-test")
        self.worker.db.heartbeat(self.worker.id, running=[])

    def workspace(self, name: str = "Pricing", **res) -> dict:
        resources = {"repos": [{"name": "buggy-service", "provider": "local", "path": str(self.repo), "branch": "main", "test_command": "python -m unittest -q"}], **res}
        return self.at.create_workspace({"name": name, "resources": resources}, "user")

    def agent(self, tpl: str | None, name: str, ws: dict | list | None, activate: bool = True, **body) -> dict:
        wss = [] if ws is None else ([w["id"] for w in ws] if isinstance(ws, list) else [ws["id"]])
        b = {"name": name, "workspace_ids": wss, "activate": activate, **body}
        if tpl:
            b["template"] = tpl
        return self.at.create_agent(b, "user")

    def trio(self, ws: dict) -> tuple[dict, dict, dict]:
        return self.agent("bug_hunter", "Agent 1", ws), self.agent("investigator", "Agent 2", ws), self.agent("fix_agent", "Agent 3", ws)

    def log_error(self, ws: dict, message: str = "ZeroDivisionError: division by zero") -> dict:
        w = next((x for x in self.at.db.watchers(workspace_id=ws["id"], kind="app_logs")), None)
        if w is None:
            w, _ = self.at.watchers.create({"workspace_id": ws["id"], "kind": "app_logs", "config": {"service": "pricing"}}, "user")
        w = self.at.db.watcher(w["id"])
        return self.at.watchers.ingest_log(w, {"level": "error", "message": message,
                                               "stack": 'Traceback\n  File "pricing.py", line 14, in average_price\nZeroDivisionError'})

    def drain(self, timeout: float = 60) -> None:
        self.worker.drain(timeout)

    def close(self) -> None:
        self.worker.pool.shutdown(wait=True)
        self.wf.stop()
        self.store._db._conn.close()


def turn(*calls, text: str = "", stop: str | None = None, model: str = "scripted", usage=(100, 20)) -> ModelTurn:
    """A model turn for hand-written providers: calls are (name, input) pairs."""
    uses = [ToolUse(f"tu_{i}_{n}", n, a) for i, (n, a) in enumerate(calls)]
    content = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": u.id, "name": u.name, "input": u.input} for u in uses]
    return ModelTurn(content=content, text=text, tool_uses=uses, stop_reason=stop or ("tool_use" if uses else "end_turn"),
                     usage={"input_tokens": usage[0], "output_tokens": usage[1]}, model=model, price=(Decimal("1"), Decimal("5")), foreign=True)


class Script:
    """A provider that returns the given turns in order (a callable item is called with the request)."""

    name = "anthropic"
    model = "scripted"

    def __init__(self, *turns) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []

    def complete(self, *, model, system, messages, tools, limits, on_text, should_stop, timeout_s):
        req = {"model": model, "system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]}
        self.requests.append(req)
        t = self.turns.pop(0) if self.turns else turn(("complete_task", {"summary": "done"}))
        if isinstance(t, BaseException):
            raise t
        return t(req) if callable(t) else t
