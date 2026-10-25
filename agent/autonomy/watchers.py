"""Watcher Manager: the components that observe a workspace and turn what they see into events.

    bsc_events      a monitor in the BSC data layer (agent/bsc): new blocks over WebSocket with HTTP fallback, backfill
                    after a disconnect, confirmations and reorgs. Runs in the server process that holds the listener.
    github_webhook  GitHub POSTs push / pull_request / workflow_run / check_run / issues; the HMAC signature is checked.
    ci_failure      any CI POSTs a small JSON result with the watcher's token.
    app_logs        the application POSTs error logs with the watcher's token; repeated errors are folded by fingerprint.
    api_health      the worker GETs a URL on an interval; an outage and a recovery are one event each, not one per check.

No watcher calls a model. Models only run when an event starts a task.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import socket
import time
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from . import constants as C
from .activity import Activity
from .db import AutonomyDB, iso, now_dt, parse
from .events import Events, fingerprint_of

ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
LEVELS = {"fatal": "critical", "critical": "critical", "error": "high", "err": "high", "warning": "medium", "warn": "medium"}


class WatcherError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_url(url: str, allow_private: bool) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise WatcherError(f"不是 http(s) 地址：{url}")
    if not allow_private:
        try:
            infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise WatcherError(f"解析不到主机：{p.hostname}") from None
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise WatcherError(f"{p.hostname} 指向内网地址 {ip}；只有设置 KAGOU_ALLOW_PRIVATE_HTTP=1 的服务器可以检查内网地址")
    return url


def http_get(url: str, timeout_s: float) -> tuple[int, float, str]:  # pragma: no cover - real network
    import httpx

    t = time.monotonic()
    r = httpx.get(url, timeout=timeout_s, follow_redirects=False, headers={"User-Agent": "KAGOU-Watcher/1"})
    return r.status_code, round((time.monotonic() - t) * 1000, 1), r.text[:400]


class Watchers:
    def __init__(self, db: AutonomyDB, activity: Activity, events: Events, *, env: dict | None = None,
                 get: Callable[[str, float], tuple[int, float, str]] | None = None) -> None:
        self.db = db
        self.act = activity
        self.events = events
        self.env = env or {}
        self.http_get = get or http_get
        self.chain = None  # agent.bsc.service.ChainService, in the process that runs the listener

    @property
    def allow_private(self) -> bool:
        return self.env.get("KAGOU_ALLOW_PRIVATE_HTTP") == "1"

    # ------------------------------------------------------------------ CRUD

    def create(self, body: dict, by: str) -> tuple[dict, str | None]:
        """Returns (watcher, secret). The secret (an ingest token or a webhook secret) is shown once and never again."""
        wid = str(body.get("workspace_id") or "")
        if self.db.workspace(wid) is None:
            raise WatcherError("请选择工作区")
        kind = body.get("kind")
        if kind not in C.WATCHER_KINDS:
            raise WatcherError(f"Watcher 类型只能是 {', '.join(C.WATCHER_KINDS)}")
        cfg = self._config(kind, body.get("config") or {})
        name = str(body.get("name") or "").strip()[:80] or C.WATCHER_KINDS[kind].split("（")[0]
        secret = None
        extra: dict[str, Any] = {}
        if kind in ("app_logs", "ci_failure"):
            secret = "kgw_" + secrets.token_urlsafe(24)
            extra["secret_hash"] = _hash(secret)
        elif kind == "github_webhook":
            secret = secrets.token_urlsafe(32)
            extra["webhook_secret"] = secret
        w = self.db.add_watcher(workspace_id=wid, kind=kind, name=name, config=cfg, state={}, status="active", **extra,
                                next_check_at=iso(now_dt()) if kind == "api_health" else None)
        if kind == "bsc_events":
            w = self._attach_chain(w)
        self.act.log("watcher.started", f"Watcher {name} 已启动（{C.WATCHER_KINDS[kind].split('（')[0]}）", workspace_id=wid, actor=by,
                     data={"watcher_id": w["id"], "kind": kind}, severity="info" if w["status"] == "active" else "medium",
                     result=w.get("last_error") or "")
        self.act.audit(f"建立 Watcher {name}（{w['id']}）", by, {"watcher": w["id"]})
        return public(w), secret

    def _config(self, kind: str, cfg: dict) -> dict:
        if not isinstance(cfg, dict):
            raise WatcherError("config 必须是对象")
        if kind == "api_health":
            url = check_url(str(cfg.get("url") or ""), self.allow_private)
            interval = int(cfg.get("interval_s") or 60)
            if not 30 <= interval <= 86400 and not (self.allow_private and interval >= 1):
                raise WatcherError("检查间隔只能是 30 秒到 1 天")
            return {"url": url, "interval_s": interval, "timeout_s": max(1, min(int(cfg.get("timeout_s") or 10), 30)),
                    "expect_status": int(cfg.get("expect_status") or 200), "fail_after": max(1, min(int(cfg.get("fail_after") or 2), 10)),
                    "severity": cfg.get("severity") if cfg.get("severity") in C.SEVERITIES else "high"}
        if kind == "bsc_events":
            addr = str(cfg.get("address") or "").strip()
            if not ADDR.match(addr):
                raise WatcherError(f"合约地址格式不对：{addr}")
            mk = cfg.get("monitor") or "token_transfer"
            if mk not in ("token_transfer", "contract", "address"):
                raise WatcherError("monitor 只能是 token_transfer / contract / address")
            try:
                big = int(cfg.get("large_transfer_raw") or 0)
                low = int(cfg.get("min_value_raw") or 0)
            except (TypeError, ValueError):
                raise WatcherError("金额要写链上原始单位的整数") from None
            topics = [t for t in cfg.get("topics") or [] if isinstance(t, str)]
            return {"address": addr.lower(), "monitor": mk, "symbol": str(cfg.get("symbol") or "")[:20], "large_transfer_raw": str(big),
                    "min_value_raw": str(low), "topics": topics, "track_holders": bool(cfg.get("track_holders", True)),
                    "alert_new_holders": bool(cfg.get("alert_new_holders", False)), "act_on_pending": bool(cfg.get("act_on_pending", False)),
                    "large_transfer_severity": cfg.get("large_transfer_severity") if cfg.get("large_transfer_severity") in C.SEVERITIES else "high"}
        if kind == "app_logs":
            return {"min_level": cfg.get("min_level") if cfg.get("min_level") in ("warning", "error", "critical") else "error",
                    "service": str(cfg.get("service") or "")[:80]}
        if kind == "github_webhook":
            events = [e for e in cfg.get("events") or ["push", "pull_request", "workflow_run", "check_run", "issues"] if isinstance(e, str)]
            return {"events": events, "repo": str(cfg.get("repo") or "")[:200]}
        return {"pipeline": str(cfg.get("pipeline") or "")[:120]}

    def _attach_chain(self, w: dict) -> dict:
        if self.chain is None:
            return self.db.update_watcher(w["id"], status="error", last_error="这个服务器没有运行 BSC 监听（没有配置 CHAINSTAFF_CHAIN，或 KAGOU_CHAIN_LISTENER=0）")
        cfg = w["config"]
        try:
            mon = self.chain.add_monitor(name=f"watcher {w['name']}"[:80], kind=cfg["monitor"], target=cfg["address"], topics=cfg["topics"] or None,
                                         min_value=cfg["min_value_raw"], trigger={"watcher_id": w["id"]})
        except ValueError as e:
            return self.db.update_watcher(w["id"], status="error", last_error=str(e)[:300])
        return self.db.update_watcher(w["id"], state={**(w["state"] or {}), "monitor_id": mon["id"]}, last_error=None, last_heartbeat_at=iso(now_dt()))

    def update(self, wid: str, body: dict, by: str) -> dict:
        w = self.get(wid)
        f: dict[str, Any] = {}
        if "name" in body:
            f["name"] = str(body["name"] or "").strip()[:80] or w["name"]
        if "config" in body:
            f["config"] = self._config(w["kind"], {**w["config"], **(body["config"] or {})})
        w = self.db.update_watcher(wid, **f)
        if w["kind"] == "bsc_events" and "config" in body and self.chain and (w["state"] or {}).get("monitor_id"):
            cfg = w["config"]
            try:
                self.chain.update_monitor(w["state"]["monitor_id"], min_value=cfg["min_value_raw"], topics=cfg["topics"] or None)
            except ValueError as e:
                raise WatcherError(str(e)) from None
        self.act.audit(f"修改 Watcher {w['name']}", by, {"watcher": wid})
        return public(w)

    def set_status(self, wid: str, active: bool, by: str) -> dict:
        w = self.get(wid)
        w = self.db.update_watcher(wid, status="active" if active else "paused", next_check_at=iso(now_dt()) if active and w["kind"] == "api_health" else w["next_check_at"])
        mon = (w["state"] or {}).get("monitor_id")
        if self.chain and mon:
            try:
                self.chain.update_monitor(mon, enabled=active)
            except ValueError:
                pass
        if active and w["kind"] == "bsc_events" and not mon:
            w = self._attach_chain(w)
        self.act.log("watcher.started" if active else "watcher.paused", f"Watcher {w['name']} {'已恢复' if active else '已暂停'}", workspace_id=w["workspace_id"], actor=by,
                     data={"watcher_id": wid})
        self.act.audit(f"Watcher {w['name']} → {'active' if active else 'paused'}", by, {"watcher": wid})
        return public(w)

    def rotate(self, wid: str, by: str) -> tuple[dict, str]:
        w = self.get(wid)
        if w["kind"] in ("app_logs", "ci_failure"):
            secret = "kgw_" + secrets.token_urlsafe(24)
            w = self.db.update_watcher(wid, secret_hash=_hash(secret))
        elif w["kind"] == "github_webhook":
            secret = secrets.token_urlsafe(32)
            w = self.db.update_watcher(wid, webhook_secret=secret)
        else:
            raise WatcherError("这个 Watcher 没有密钥")
        self.act.audit(f"更换 Watcher {w['name']} 的密钥", by, {"watcher": wid})
        return public(w), secret

    def delete(self, wid: str, by: str) -> None:
        w = self.get(wid)
        mon = (w["state"] or {}).get("monitor_id")
        if self.chain and mon:
            self.chain.events.delete_monitor(mon)
        self.db.delete_watcher(wid)
        self.act.audit(f"删除 Watcher {w['name']}", by, {"watcher": wid})

    def get(self, wid: str) -> dict:
        w = self.db.watcher(wid)
        if w is None:
            raise WatcherError(f"没有 Watcher {wid}")
        return public(w)

    def token_ok(self, w: dict, token: str | None) -> bool:
        import hmac

        return bool(token and w.get("secret_hash") and hmac.compare_digest(_hash(token.strip()), w["secret_hash"]))

    # ------------------------------------------------------------------ health (is the watcher itself running?)

    def healthy(self, w: dict, worker_alive: bool, chain_alive: bool | None = None) -> tuple[bool, str]:
        if w["status"] != "active":
            return False, "已暂停" if w["status"] == "paused" else (w.get("last_error") or "出错")
        if w["kind"] == "api_health":
            hb = parse(w["last_heartbeat_at"])
            limit = max(90, 3 * int((w["config"] or {}).get("interval_s") or 60))
            if not worker_alive:
                return False, "没有在线的 Worker 执行检查"
            if hb is None or (now_dt() - hb).total_seconds() > limit:
                return False, "检查没有按时执行"
            return True, f"每 {w['config']['interval_s']} 秒检查一次"
        if w["kind"] == "bsc_events":
            hb = parse(w["last_heartbeat_at"])
            if hb is None or (now_dt() - hb).total_seconds() > 120:
                return False, "BSC 监听没有新区块（监听没运行或节点断开）"
            return True, "跟随新区块"
        return True, "等待推送（Webhook / 日志接口）"

    def chain_heartbeat(self, block: int) -> None:
        """Called by the BSC listener after each block, throttled: bsc watchers are alive while blocks keep coming."""
        now = now_dt()
        for w in self.db.watchers(kind="bsc_events", status="active"):
            hb = parse(w["last_heartbeat_at"])
            if hb is None or (now - hb).total_seconds() >= 10:
                self.db.update_watcher(w["id"], last_heartbeat_at=iso(now), state={**(w["state"] or {}), "last_block": block})

    # ------------------------------------------------------------------ api health checks (worker)

    def poll_health(self) -> int:
        n = 0
        now = now_dt()
        for w in self.db.watchers(kind="api_health", status="active"):
            if w["next_check_at"] and parse(w["next_check_at"]) > now:
                continue
            cfg, st = w["config"], dict(w["state"] or {})
            self.db.update_watcher(w["id"], next_check_at=iso(now + timedelta(seconds=int(cfg["interval_s"]))))
            try:
                code, ms, preview = self.http_get(cfg["url"], float(cfg["timeout_s"]))
                ok, err = code == int(cfg["expect_status"]), None if code == int(cfg["expect_status"]) else f"HTTP {code}"
            except Exception as e:
                code, ms, preview, ok, err = None, None, "", False, f"{type(e).__name__}: {str(e)[:200]}"
            fails = 0 if ok else int(st.get("consecutive_failures") or 0) + 1
            was_down = st.get("health") == "down"
            st.update({"last_status": code, "last_ms": ms, "last_error": err, "consecutive_failures": fails, "last_checked_at": iso(now),
                       "checks": int(st.get("checks") or 0) + 1})
            if not ok and fails >= int(cfg["fail_after"]) and not was_down:
                st["health"], st["down_since"] = "down", iso(now)
                self.events.ingest(workspace_id=w["workspace_id"], source="api_health", type_="health.down", severity=cfg["severity"], watcher_id=w["id"],
                                   title=f"{w['name']} 不可用：{err}（连续 {fails} 次）", fingerprint=fingerprint_of(w["id"], "down"),
                                   dedupe_key=f"health:{w['id']}:down:{iso(now)}",
                                   payload={"url": cfg["url"], "status": code, "error": err, "latency_ms": ms, "body_preview": preview[:300], "consecutive_failures": fails})
                self.act.log("watcher.down", f"{w['name']} 健康检查失败", severity=cfg["severity"], workspace_id=w["workspace_id"], actor=w["id"], reason=err or "")
            elif ok and was_down:
                st["health"], st["recovered_at"] = "up", iso(now)
                self.events.ingest(workspace_id=w["workspace_id"], source="api_health", type_="health.recovered", severity="medium", watcher_id=w["id"],
                                   title=f"{w['name']} 已恢复（HTTP {code}，{ms} ms）", fingerprint=fingerprint_of(w["id"], "down"),
                                   dedupe_key=f"health:{w['id']}:up:{iso(now)}", payload={"url": cfg["url"], "status": code, "latency_ms": ms, "down_since": st.get("down_since")})
                self.act.log("watcher.recovered", f"{w['name']} 已恢复", workspace_id=w["workspace_id"], actor=w["id"])
            elif ok:
                st["health"] = "up"
            self.db.update_watcher(w["id"], state=st, last_heartbeat_at=iso(now), last_error=err)
            n += 1
        return n

    # ------------------------------------------------------------------ push intake

    def ingest_log(self, w: dict, body: dict) -> dict:
        if not isinstance(body, dict):
            raise WatcherError("日志必须是 JSON 对象")
        level = str(body.get("level") or "error").lower()
        sev = LEVELS.get(level)
        floor = {"warning": "medium", "error": "high", "critical": "critical"}[(w["config"] or {}).get("min_level", "error")]
        if sev is None or C.SEV_RANK[sev] < C.SEV_RANK[floor]:
            return {"accepted": False, "reason": f"级别 {level} 低于这个 Watcher 的阈值"}
        msg = str(body.get("message") or "").strip()
        if not msg:
            raise WatcherError("message 不能为空")
        service = str(body.get("service") or (w["config"] or {}).get("service") or "app")[:80]
        stack = str(body.get("stack") or "")[:20000]
        top = next((ln.strip() for ln in reversed(stack.splitlines()) if ln.strip().startswith("File ")), "") if stack else ""
        fp = str(body.get("fingerprint") or "") or fingerprint_of(w["workspace_id"], service, re.sub(r"\d+", "N", msg)[:200], top)
        ev, created = self.events.ingest(workspace_id=w["workspace_id"], source="app_logs", type_="app.error" if C.SEV_RANK[sev] >= 3 else "app.warning",
                                         severity=sev, title=f"{service}: {msg[:200]}", watcher_id=w["id"], fingerprint=fp,
                                         occurred_at=_ts(body.get("timestamp")),
                                         payload={"service": service, "level": level, "message": msg[:4000], "stack": stack,
                                                  "context": body.get("context") if isinstance(body.get("context"), dict) else None})
        return {"accepted": True, "event_id": ev["id"], "deduplicated": not created, "occurrences": ev["occurrences"]}

    def ingest_ci(self, w: dict, body: dict) -> dict:
        if not isinstance(body, dict):
            raise WatcherError("CI 结果必须是 JSON 对象")
        status = str(body.get("status") or body.get("conclusion") or "").lower()
        if status not in ("failed", "failure", "passed", "success", "error", "timed_out"):
            raise WatcherError("status 只能是 failed / passed")
        failed = status in ("failed", "failure", "error", "timed_out")
        pipeline = str(body.get("pipeline") or (w["config"] or {}).get("pipeline") or "ci")[:120]
        ref = str(body.get("commit") or body.get("run_id") or "")[:80]
        ev, created = self.events.ingest(workspace_id=w["workspace_id"], source="ci_failure", type_="ci.failed" if failed else "ci.passed",
                                         severity="high" if failed else "info", title=f"{pipeline} {'失败' if failed else '通过'}" + (f"（{body.get('branch')}）" if body.get("branch") else ""),
                                         watcher_id=w["id"], fingerprint=fingerprint_of(w["workspace_id"], pipeline, ",".join(sorted(map(str, body.get("failed_tests") or []))[:20])),
                                         dedupe_key=f"ci:{w['id']}:{pipeline}:{ref}:{status}" if ref else None,
                                         payload={k: body.get(k) for k in ("pipeline", "branch", "commit", "url", "failed_tests", "log_tail", "run_id") if body.get(k) is not None})
        return {"accepted": True, "event_id": ev["id"], "deduplicated": not created}

    def ingest_github(self, w: dict, event: str, delivery: str | None, body: dict) -> dict:
        allowed = (w["config"] or {}).get("events") or []
        if event == "ping":
            self.db.update_watcher(w["id"], last_heartbeat_at=iso(now_dt()))
            return {"accepted": True, "ping": True}
        if allowed and event not in allowed:
            return {"accepted": False, "reason": f"这个 Watcher 不处理 {event}"}
        repo = ((body.get("repository") or {}).get("full_name")) or ""
        key = f"gh:{delivery}" if delivery else None
        typ, sev, title, payload = "github." + event, "info", f"{repo} {event}", {"repo": repo}
        fp = None
        if event == "workflow_run":
            run = body.get("workflow_run") or {}
            if body.get("action") != "completed":
                return {"accepted": False, "reason": "只处理 completed"}
            concl = run.get("conclusion")
            typ, sev = ("ci.failed", "high") if concl in ("failure", "timed_out", "startup_failure") else ("ci.passed", "info")
            title = f"{repo} · {run.get('name')} {concl}（{run.get('head_branch')}）"
            payload.update({"workflow": run.get("name"), "conclusion": concl, "branch": run.get("head_branch"), "commit": run.get("head_sha"), "url": run.get("html_url")})
            fp = fingerprint_of(w["workspace_id"], repo, run.get("name"), run.get("head_branch"))
        elif event == "check_run":
            cr = body.get("check_run") or {}
            if body.get("action") != "completed":
                return {"accepted": False, "reason": "只处理 completed"}
            concl = cr.get("conclusion")
            typ, sev = ("ci.failed", "high") if concl in ("failure", "timed_out") else ("ci.passed", "info")
            title = f"{repo} · check {cr.get('name')} {concl}"
            payload.update({"check": cr.get("name"), "conclusion": concl, "commit": cr.get("head_sha"), "url": cr.get("html_url")})
            fp = fingerprint_of(w["workspace_id"], repo, cr.get("name"))
        elif event == "pull_request":
            pr = body.get("pull_request") or {}
            if body.get("action") == "closed" and pr.get("merged"):
                typ, sev = "pr.merged", "medium"
                title = f"{repo} #{pr.get('number')} 已合并：{pr.get('title')}"
                art = self._artifact_for_pr(pr.get("html_url"))
                payload.update({"number": pr.get("number"), "url": pr.get("html_url"), "title": pr.get("title"), "merged_by": (pr.get("merged_by") or {}).get("login"),
                                "origin_task_id": art["task_id"] if art else None, "artifact_id": art["id"] if art else None})
                if art:
                    self.db.update_artifact(art["id"], status="merged")
            else:
                typ = f"github.pull_request.{body.get('action')}"
                title = f"{repo} #{pr.get('number')} {body.get('action')}：{pr.get('title')}"
                payload.update({"number": pr.get("number"), "url": pr.get("html_url"), "action": body.get("action")})
        elif event == "push":
            title = f"{repo} push {body.get('ref')}（{len(body.get('commits') or [])} commits）"
            payload.update({"ref": body.get("ref"), "after": body.get("after"), "commits": [c.get("message", "")[:200] for c in (body.get("commits") or [])[:20]]})
        elif event == "issues":
            iss = body.get("issue") or {}
            typ = f"github.issue.{body.get('action')}"
            sev = "medium" if body.get("action") == "opened" else "info"
            title = f"{repo} issue #{iss.get('number')} {body.get('action')}：{iss.get('title')}"
            payload.update({"number": iss.get("number"), "url": iss.get("html_url"), "title": iss.get("title"), "body": (iss.get("body") or "")[:4000]})
        ev, created = self.events.ingest(workspace_id=w["workspace_id"], source="github_webhook", type_=typ, severity=sev, title=title, watcher_id=w["id"],
                                         dedupe_key=key, fingerprint=fp, payload=payload)
        return {"accepted": True, "event_id": ev["id"], "deduplicated": not created}

    def _artifact_for_pr(self, url: str | None) -> dict | None:
        if not url:
            return None
        for a in self.db.artifacts(limit=500):
            if (a["meta"] or {}).get("pr_url") == url:
                return a
        return None


def public(w: dict) -> dict:
    """A watcher as the console sees it: never its token hash or webhook secret."""
    out = {k: v for k, v in w.items() if k not in ("secret_hash", "webhook_secret")}
    out["has_secret"] = bool(w.get("secret_hash") or w.get("webhook_secret"))
    return out


def _ts(v: Any) -> str | None:
    if not v:
        return None
    d = parse(str(v))
    return iso(d) if d else None
