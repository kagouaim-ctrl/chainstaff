"""Console API for the BSC realtime data layer (/api/chain/*).

REST for status, health checks, monitors and stored events; a WebSocket at /api/chain/ws for live blocks
and events. Browsers cannot set headers on a WebSocket, and a token in the URL ends up in logs, so the
socket's first message carries the token: {"type": "auth", "token": "...", "since": <seq>, "topics": [...]}.
Everything here reads the chain; monitors only watch and, if asked, start an analysis mission.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from fastapi import Body, Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from agent.bsc.service import ChainService
from agent.bsc.store import MONITOR_KINDS

AUTH_TIMEOUT_S = 5.0
WS_TOPICS = {"block", "event", "event.confirmed", "event.reorged", "reorg", "gap", "status", "trigger", "trigger.error", "resync"}


def mount_chain(app: FastAPI, svc: ChainService | None, auth: Callable, token_ok: Callable[[str], bool], write: Callable | None = None,
                origin_ok: Callable[[str | None, str | None], bool] | None = None) -> None:
    A = [Depends(auth)]
    W = [Depends(auth)] + ([Depends(write)] if write else [])

    def need() -> ChainService:
        if svc is None:
            raise HTTPException(409, "没有配置链（CHAINSTAFF_CHAIN），实时链上数据层没有运行")
        return svc

    @app.get("/api/chain/status", dependencies=A)
    def status() -> dict:
        if svc is None:
            return {"enabled": False, "reason": "没有配置链（CHAINSTAFF_CHAIN）", "read_only": True}
        return svc.status()

    @app.post("/api/chain/health-check", dependencies=W)
    def health() -> dict:
        s = need()
        rows = s.rpc.health() if hasattr(s.rpc, "health") else []
        return {"endpoints": rows, "ok": any(r.get("ok") for r in rows), "expected_chain_id": s.chain.chain_id}

    @app.get("/api/chain/monitors", dependencies=A)
    def monitors() -> dict:
        s = need()
        return {"monitors": s.events.monitors(), "kinds": MONITOR_KINDS}

    @app.post("/api/chain/monitors", dependencies=W)
    def add_monitor(body: dict = Body(...)) -> dict:
        s = need()
        try:
            return s.add_monitor(name=body.get("name", ""), kind=body.get("kind", ""), target=body.get("target", ""),
                                 topics=body.get("topics"), min_value=body.get("min_value", "0"), trigger=body.get("trigger") or None,
                                 enabled=bool(body.get("enabled", True)))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except Exception as e:  # WorkforceError for an unknown workspace
            raise HTTPException(400, str(e)) from None

    @app.patch("/api/chain/monitors/{mid}", dependencies=W)
    def update_monitor(mid: str, body: dict = Body(...)) -> dict:
        s = need()
        allowed = {k: body[k] for k in ("name", "target", "topics", "min_value", "trigger", "enabled") if k in body}
        try:
            return s.update_monitor(mid, **allowed)
        except ValueError as e:
            raise HTTPException(404 if "不存在" in str(e) else 400, str(e)) from None
        except Exception as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/chain/monitors/{mid}", dependencies=W)
    def delete_monitor(mid: str) -> dict:
        if not need().events.delete_monitor(mid):
            raise HTTPException(404, "监控不存在")
        return {"ok": True}

    @app.get("/api/chain/events", dependencies=A)
    def events(monitor_id: str | None = None, address: str | None = None, tx_hash: str | None = None, status: str | None = None,
               after_id: int | None = None, limit: int = 100) -> dict:
        rows = need().events.events(monitor_id=monitor_id, address=address, tx_hash=tx_hash, status=status, after_id=after_id,
                                    limit=min(max(limit, 1), 500))
        return {"events": rows}

    @app.websocket("/api/chain/ws")
    async def ws(sock: WebSocket) -> None:
        if origin_ok and not origin_ok(sock.headers.get("origin"), sock.headers.get("host")):
            await sock.close(code=4403)  # a page on another site; CORS does not apply to WebSockets
            return
        await sock.accept()
        try:
            first = json.loads(await asyncio.wait_for(sock.receive_text(), AUTH_TIMEOUT_S))
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            await _close(sock, 4401, "需要先发送 auth 消息")
            return
        if not isinstance(first, dict) or first.get("type") != "auth" or not token_ok(str(first.get("token", ""))):
            await _close(sock, 4401, "令牌不对")
            return
        if svc is None:
            await sock.send_json({"type": "status", "enabled": False, "reason": "没有配置链（CHAINSTAFF_CHAIN）"})
            await _close(sock, 4409, "chain not configured")
            return
        topics = set(first.get("topics") or []) & WS_TOPICS or None
        since = first.get("since")
        sub, backlog = svc.hub.subscribe(asyncio.get_running_loop(), since if isinstance(since, int) else None, topics)
        try:
            await sock.send_json({"type": "hello", "seq": svc.hub.seq, "status": svc.status()})
            for m in backlog:
                await sock.send_json(m)
            reader = asyncio.create_task(_drain(sock))
            while not reader.done():
                getter = asyncio.create_task(sub.queue.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    break
                msg = getter.result()
                await sock.send_json(msg)
                if msg["type"] == "resync" and sub.overflowed:
                    await _close(sock, 4408, "too slow, reconnect with since")
                    break
        except WebSocketDisconnect:
            pass
        finally:
            svc.hub.unsubscribe(sub)


async def _drain(sock: WebSocket) -> None:
    """Read (and ignore) what the browser sends, so a closed socket is noticed right away."""
    try:
        while True:
            msg = await sock.receive()
            if msg.get("type") == "websocket.disconnect":
                return
    except Exception:
        return


async def _close(sock: WebSocket, code: int, reason: str) -> None:
    try:
        await sock.close(code=code, reason=reason)
    except Exception:
        pass
