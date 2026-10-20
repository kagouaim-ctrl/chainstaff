"""Minimal owner console API: view the ledger, edit rules, pause, approve.

Run:
    export CHAINSTAFF_CONSOLE_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
    uvicorn backend.app:app --port 8000
Then open http://localhost:8000 and paste the token.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from agent.skills.finance_ops.employee import FinanceOpsEmployee

WEB = Path(__file__).resolve().parent.parent / "web" / "console.html"
OWNER_ID = "console"


def create_app(employee: FinanceOpsEmployee, token: str) -> FastAPI:
    if not token or len(token) < 16:
        raise RuntimeError("CHAINSTAFF_CONSOLE_TOKEN must be set (at least 16 characters)")
    app = FastAPI(title="ChainStaff console", docs_url=None, redoc_url=None)

    def auth(authorization: str = Header(default="")) -> None:
        given = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "令牌不对")

    # The console acts as the owner, so it bypasses the chat-handle owner list.
    def as_owner(fn, *args):
        owners = employee.owners
        return fn(*args, next(iter(owners))) if owners else fn(*args, OWNER_ID)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB)

    @app.get("/api/status", dependencies=[Depends(auth)])
    def status() -> dict:
        return {"paused": employee.store.paused, "live": employee.executor.live, "pending": len(employee.store.pending())}

    @app.get("/api/rules", dependencies=[Depends(auth)])
    def get_rules() -> dict:
        return employee.policy_file.raw()

    @app.put("/api/rules", dependencies=[Depends(auth)])
    def put_rules(cfg: dict) -> dict:
        try:
            employee.policy_file.save(cfg)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "rules_text": employee.rules_text()}

    @app.get("/api/payments", dependencies=[Depends(auth)])
    def payments(limit: int = 100) -> list[dict]:
        names = {a: p.name for a, p in employee.policy.whitelist.items()}
        out = []
        for r in employee.records(min(max(limit, 1), 500)):
            d = r.to_dict()
            d["payee_name"] = names.get(d["payee"])
            out.append(d)
        return out

    @app.post("/api/pause", dependencies=[Depends(auth)])
    def pause() -> dict:
        return {"message": as_owner(employee.pause)}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    def resume() -> dict:
        return {"message": as_owner(employee.resume)}

    @app.post("/api/payments/{request_id}/approve", dependencies=[Depends(auth)])
    def approve(request_id: str) -> dict:
        return {"message": as_owner(employee.approve, request_id)}

    @app.post("/api/payments/{request_id}/reject", dependencies=[Depends(auth)])
    def reject(request_id: str) -> dict:
        return {"message": as_owner(employee.reject, request_id)}

    @app.get("/api/report/weekly", dependencies=[Depends(auth)])
    def report() -> dict:
        return {"text": employee.weekly_report_text()}

    return app


def _default_app() -> FastAPI:  # pragma: no cover - process entry point
    from agent.runtime import build_employee

    employee, _ = build_employee()
    return create_app(employee, os.environ.get("CHAINSTAFF_CONSOLE_TOKEN", ""))


def __getattr__(name: str):  # pragma: no cover - lets `uvicorn backend.app:app` build lazily
    if name == "app":
        return _default_app()
    raise AttributeError(name)
