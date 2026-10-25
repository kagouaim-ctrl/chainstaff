"""Event intake and trigger rules.

Every watcher produces the same normalized event: event_id, source, workspace_id, timestamp, payload, severity and
a processing status. Events are deduplicated on arrival (a webhook delivery id, a chain event id, or an error's
fingerprint inside a ten-minute window), stored before anything acts on them, and processed by the worker:
trigger rules decide which missions start a task, the task wakes an agent. A failed processing attempt is retried
with backoff; processing the same event twice cannot create a second task, because task creation is deduplicated
on the event too.

Chain events arrive as pending, become confirmed after the configured number of blocks, and can be reorged away.
By default only confirmed events start work; a reorged event is marked as such, and tasks it started that have
not run yet are cancelled.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from . import constants as C
from .activity import Activity
from .db import AutonomyDB, iso, now_dt
from .missions import trigger_matches

PROBLEM_TYPES = ("app.error", "ci.failed", "health.down", "github.workflow_failed")
VERIFY_TYPES = ("patch.applied", "pr.merged")
DEFAULT_KIND = {"app.error": "investigate", "ci.failed": "investigate", "health.down": "investigate", "github.workflow_failed": "investigate",
                "health.recovered": "verify", "patch.applied": "verify", "pr.merged": "verify"}
MAX_PAYLOAD = 64_000


class EventError(ValueError):
    pass


def fingerprint_of(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


class Events:
    def __init__(self, db: AutonomyDB, activity: Activity, missions, wakeup, limits: C.Limits, reader=None) -> None:
        self.db = db
        self.act = activity
        self.missions = missions
        self.wakeup = wakeup
        self.limits = limits
        self.reader = reader  # agent.workforce.chain.ChainReader, for holder balances (worker side)

    # ------------------------------------------------------------------ intake

    def ingest(self, *, workspace_id: str, source: str, type_: str, severity: str = "info", title: str = "", payload: Any = None,
               fingerprint: str | None = None, dedupe_key: str | None = None, watcher_id: str | None = None, occurred_at: str | None = None,
               chain_status: str | None = None, act_on_pending: bool = False) -> tuple[dict, bool]:
        if self.db.workspace(workspace_id) is None:
            raise EventError(f"没有工作区 {workspace_id}")
        if severity not in C.SEV_RANK:
            raise EventError(f"severity 只能是 {', '.join(C.SEVERITIES)}")
        body = payload if isinstance(payload, dict) else {"value": payload}
        if len(json.dumps(body, ensure_ascii=False, default=str)) > MAX_PAYLOAD:
            raise EventError(f"payload 太大（上限 {MAX_PAYLOAD} 字符）")
        if dedupe_key is None and fingerprint:
            bucket = now_dt().strftime("%Y%m%d%H") + str(now_dt().minute // 10)
            dedupe_key = f"{watcher_id or source}:{type_}:{fingerprint}:{bucket}"
        status = C.E_PENDING_CONFIRMATION if chain_status == "pending" and not act_on_pending else C.E_RECEIVED
        ev, created = self.db.add_event(source=source, watcher_id=watcher_id, workspace_id=workspace_id, type=type_[:60], severity=severity,
                                        title=str(title or "")[:300], payload=body, fingerprint=fingerprint, dedupe_key=dedupe_key,
                                        occurred_at=occurred_at, processing_status=status, chain_status=chain_status)
        if watcher_id:
            self.db.update_watcher(watcher_id, last_event_at=iso(now_dt()))
        if created and (source != "bsc_events" or C.SEV_RANK[severity] >= 2):
            problem = type_ in PROBLEM_TYPES
            self.act.log("bug.detected" if problem else ("alert.triggered" if C.SEV_RANK[severity] >= 3 else "event.received"),
                         f"{'发现问题' if problem else '事件'}：{title or type_}"[:200], severity=severity, workspace_id=workspace_id, event_id=ev["id"],
                         actor=watcher_id or source, reason=f"{C.WATCHER_KINDS.get(source, source)} · {type_}",
                         data={"fingerprint": fingerprint, "chain_status": chain_status})
        return ev, created

    # ------------------------------------------------------------------ processing (worker)

    def process_due(self, limit: int = 50) -> int:
        n = 0
        for e in self.db.due_events(limit):
            n += self.process(e)
        return n

    def process(self, e: dict) -> int:
        if not self.db.set_event_status(e["id"], (C.E_RECEIVED, C.E_FAILED), C.E_PROCESSED, attempts=e["attempts"] + 1):
            return 0  # another worker took it
        try:
            self._enrich(e)
            e = self.db.event(e["id"])
            tasks = self._route(e)
        except Exception as ex:  # retried with backoff; task creation is deduplicated on the event, so a retry is safe
            wait = min(3600, 10 * 2 ** e["attempts"])
            final = e["attempts"] + 1 >= 6
            self.db.update_event(e["id"], processing_status=C.E_FAILED if not final else C.E_IGNORED, error=f"{type(ex).__name__}: {str(ex)[:300]}",
                                 next_attempt_at=iso(now_dt() + timedelta(seconds=wait)))
            self.act.log("system.error", f"处理事件 {e['id']} 失败{'，不再重试' if final else f'，{wait} 秒后重试'}：{ex}"[:300], severity="medium",
                         workspace_id=e["workspace_id"], event_id=e["id"])
            return 1
        if tasks:
            self.db.update_event(e["id"], task_ids=list(dict.fromkeys([*(e["task_ids"] or []), *tasks])), error=None)
        elif C.SEV_RANK.get(e["severity"], 0) >= 3:
            self.act.log("event.ignored", f"高优先级事件没有负责的 Agent：{e['title'] or e['type']}", severity=e["severity"], workspace_id=e["workspace_id"],
                         event_id=e["id"], needs_human=True, reason="这个工作区没有任何进行中的 Mission 会被这类事件触发")
            self.act.notify(f"没有 Agent 负责这个事件：{e['title'] or e['type']}", "给这个工作区的某个 Agent 设置长期职责，或者建一个 Event-driven Mission。",
                            severity=e["severity"], workspace_id=e["workspace_id"], dedupe_key=f"orphan:{e['workspace_id']}:{e['fingerprint'] or e['id']}")
        return 1

    def _route(self, e: dict) -> list[str]:
        out: list[str] = []
        if e["type"] in VERIFY_TYPES and (e["payload"] or {}).get("origin_task_id"):
            t = self._verify(e)
            return [t["id"]] if t else []
        since = now_dt() - timedelta(hours=1)
        hits = []
        for m in self.db.missions(workspace_id=e["workspace_id"], status=[C.M_ACTIVE], limit=200):
            tr = next((x for x in m["triggers"] or [] if trigger_matches(x, e)), None)
            if tr is not None:
                hits.append((m, tr))
        # Missions a person wrote each run. Standing responsibilities that the event matches wake one agent, the most
        # relevant: the first responder, who hands work on. Waking every agent for one error would be noise and cost.
        own = [h for h in hits if h[0].get("responsibility_of")]
        if len(own) > 1:
            from .agents import relevance

            kind0 = DEFAULT_KIND.get(e["type"]) or "investigate"
            text = f"{e['type']} {e['title']}"

            def score(h):
                a = self.db.agent(h[0]["responsibility_of"]) or {}
                exact = any(t == e["type"] for t in h[1].get("types") or [])
                return (-int(exact), -relevance(a, kind0, text), a.get("created_at") or "")

            own.sort(key=score)
            skipped = own[1:]
            hits = [h for h in hits if not h[0].get("responsibility_of")] + own[:1]
            self.act.log("event.routed", f"事件 {e['type']} 交给 {self.db.agent(own[0][0]['responsibility_of'])['name']} 处理", workspace_id=e["workspace_id"],
                         event_id=e["id"], target_agent_id=own[0][0]["responsibility_of"],
                         reason="匹配多个 Agent 的长期职责，只唤醒最相关的一个；其他 Agent 需要时由它委派：" + "、".join(
                             (self.db.agent(h[0]["responsibility_of"]) or {}).get("name", "?") for h in skipped))
        for m, tr in hits:
            recent = self.db.count_tasks(mission_id=m["id"], since=since)
            if recent >= self.limits.event_runs_per_hour:
                self.act.log("mission.triggered", f"Mission {m['name']} 一小时内已经触发 {recent} 次，这个事件不再创建任务", severity="low",
                             workspace_id=e["workspace_id"], mission_id=m["id"], event_id=e["id"], reason="事件触发频率上限（KAGOU_MAX_EVENT_RUNS_PER_HOUR）")
                continue
            kind = tr.get("task_kind") or DEFAULT_KIND.get(e["type"]) or ("analyze" if e["type"].startswith("bsc.") else "investigate")
            objective = _objective(e, m)
            t = self.missions._start(m, e["watcher_id"] or e["source"], objective=objective, kind=kind, event=e,
                                     reason=f"事件 {e['type']}（{e['severity']}）匹配 Mission 的触发条件")
            if t:
                out.append(t["id"])
        return out

    def _verify(self, e: dict) -> dict | None:
        """A fix was merged or applied: the agent that owns the original issue checks that it is really gone."""
        from .wakeup import TaskError

        origin = self.db.task(e["payload"]["origin_task_id"])
        if origin is None:
            return None
        root = self.db.task(origin["root_id"]) or origin
        owner = root["to_agent_id"]
        objective = (f"修复已经{'合并' if e['type'] == 'pr.merged' else '应用到仓库'}（{e['title']}）。验证原来的问题是否已经消失："
                     f"{root['objective'][:400]}。用你能用的工具检查（运行测试、请求 API、查看最近的事件），给出验证结论。")
        try:
            t, _ = self.wakeup.create_task(workspace_id=e["workspace_id"], objective=objective, kind="verify", priority=1,
                                           evidence={"origin_task_id": root["id"], "fix": e["payload"]}, to_agent_id=owner, mission_id=root["mission_id"],
                                           event_id=e["id"], fingerprint=root["fingerprint"], created_by="system", reason="修复合并后由问题的发现者验证")
        except TaskError as ex:
            self.act.log("task.blocked", f"没有创建验证任务：{ex}", severity="medium", workspace_id=e["workspace_id"], event_id=e["id"], needs_human=True)
            return None
        return t

    # ------------------------------------------------------------------ chain events

    def chain_event(self, ev: dict, mon: dict, phase: str) -> None:
        """Called by the BSC listener (agent.bsc) for monitors a bsc_events watcher created."""
        wid = (mon.get("trigger") or {}).get("watcher_id")
        w = self.db.watcher(wid) if wid else None
        if w is None or w["status"] != "active":
            return
        cfg = w["config"] or {}
        key = f"chain:{ev['id']}"
        existing = self.db.event_by_key(key)
        if phase == "reorged":
            if existing:
                self.db.update_event(existing["id"], chain_status="reorged", processing_status=C.E_REORGED)
                for tid in existing["task_ids"] or []:
                    t = self.db.task(tid)
                    if t and t["status"] in (C.T_QUEUED, C.T_UNASSIGNED):
                        self.db.transition(tid, (C.T_QUEUED, C.T_UNASSIGNED), C.T_CANCELLED, error="链上事件被区块重组移除")
                self.act.log("event.reorged", f"链上事件被区块重组移除：{existing['title']}", severity="low", workspace_id=w["workspace_id"], event_id=existing["id"],
                             actor=w["id"], reason=f"区块 {ev['block']} 不再在主链上")
            return
        if existing:
            if phase == "confirmed" and existing["chain_status"] != "confirmed":
                to = C.E_RECEIVED if existing["processing_status"] == C.E_PENDING_CONFIRMATION else existing["processing_status"]
                self.db.update_event(existing["id"], chain_status="confirmed", processing_status=to)
            return
        typ, sev, title = classify_chain(ev, cfg)
        d = ev.get("data") or {}
        self.ingest(workspace_id=w["workspace_id"], source="bsc_events", type_=typ, severity=sev, title=title, watcher_id=w["id"], dedupe_key=key,
                    fingerprint=fingerprint_of(w["id"], d.get("from"), d.get("to")), chain_status="confirmed" if phase == "confirmed" else "pending",
                    act_on_pending=bool(cfg.get("act_on_pending")),
                    payload={"chain_event_id": ev["id"], "kind": ev["kind"], "block": ev["block"], "block_hash": ev["block_hash"], "tx_hash": ev["tx_hash"],
                             "log_index": ev["log_index"], "address": ev["address"], **{k: d[k] for k in d if k in ("from", "to", "value_raw", "token", "event", "topics", "direction", "value_bnb")}})

    def _enrich(self, e: dict) -> None:
        """Confirmed token transfers: record the observed flow and read both sides' balances at that block."""
        if e["source"] != "bsc_events" or e["type"] not in ("bsc.transfer", "bsc.large_transfer"):
            return
        p = e["payload"] or {}
        w = self.db.watcher(e["watcher_id"]) if e["watcher_id"] else None
        if not w or not p.get("from") or not p.get("to"):
            return
        self.db.add_flow(w["id"], p["from"], p["to"], p.get("value_raw") or "0", int(p.get("block") or 0), p.get("tx_hash") or "")
        cfg = w["config"] or {}
        if not cfg.get("track_holders", True) or self.reader is None or not p.get("token"):
            return
        notes = {}
        for side in ("to", "from"):
            addr = p[side]
            if addr == "0x" + "0" * 40:
                continue
            try:
                bal = self.reader.rpc.call("eth_call", [{"to": p["token"], "data": "0x70a08231" + addr[2:].rjust(64, "0")}, hex(int(p["block"]))])
                bal_raw = str(int(bal, 16)) if bal and bal != "0x" else "0"
            except Exception:
                continue
            prev = self.db.set_holder(w["id"], addr, bal_raw, int(p["block"]))
            if side == "to" and prev is None and bal_raw == str(int(p.get("value_raw") or 0)):
                notes["new_holder"] = addr
            if side == "from" and bal_raw == "0":
                notes["holder_exited"] = addr
            notes[f"{side}_balance_raw"] = bal_raw
        if notes:
            sev = e["severity"]
            if notes.get("new_holder") and cfg.get("alert_new_holders") and C.SEV_RANK[sev] < 2:
                sev = "medium"
            self.db.update_event(e["id"], payload={**p, "holders": notes}, severity=sev)


def classify_chain(ev: dict, cfg: dict) -> tuple[str, str, str]:
    d = ev.get("data") or {}
    if ev["kind"] == "log":
        return "bsc.contract_event", cfg.get("contract_event_severity") or "low", f"合约事件 {d.get('event') or (d.get('topics') or ['?'])[0][:10]} @ {ev['address'][:10]}…"
    value = int(d.get("value_raw") or 0)
    big = int(cfg.get("large_transfer_raw") or 0)
    sym = cfg.get("symbol") or "token"
    short = lambda a: (a or "?")[:8] + "…" + (a or "?")[-4:]  # noqa: E731
    if big and value >= big:
        return "bsc.large_transfer", cfg.get("large_transfer_severity") or "high", f"大额转账 {value} raw {sym}：{short(d.get('from'))} → {short(d.get('to'))}"
    return "bsc.transfer", "info", f"转账 {value} raw {sym}：{short(d.get('from'))} → {short(d.get('to'))}"


def _objective(e: dict, m: dict) -> str:
    p = e["payload"] or {}
    if e["type"] == "app.error":
        return f"应用报错（{p.get('service') or 'service'}）：{(p.get('message') or e['title'])[:300]}。调查原因，判断是否是代码 Bug；需要分析或修复时交给合适的 Agent。"
    if e["type"] in ("ci.failed", "github.workflow_failed"):
        return f"CI 失败：{e['title'][:300]}。找出失败的测试和原因，需要修复时交给合适的 Agent。"
    if e["type"] == "health.down":
        return f"API 健康检查失败：{e['title'][:300]}。确认故障范围和原因，通知用户。"
    if e["type"] == "health.recovered":
        return f"API 已恢复：{e['title'][:300]}。确认是否真的恢复，更新记录。"
    if e["type"].startswith("bsc."):
        return (f"链上事件：{e['title'][:300]}。读取链上证据，判断是否值得注意；值得注意就通知用户。"
                "钱包之间有转账不代表有不法行为，结论要区分链上事实和推断。")
    return f"{e['title'] or e['type']}（Mission：{m['name']}）"
