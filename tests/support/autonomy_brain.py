"""A scripted stand-in for a model, for tests and the end-to-end check of the bug workflow. It is NOT a model: it
reads the agent's prompt and the tool results and follows fixed rules, so anything it produces is a mock result.
The point is to exercise everything around the model for real: the queue, wake-ups, delegation, the sandbox, the
tests, the patch, the approval, the verification, restarts.

    decide(chat, tools) -> ("tools", [(name, args), ...]) | ("text", str)

`chat` is the chat-completions history (system first). Used in-process by ScriptedProvider and over HTTP by
scripted_autonomy_server.py (through KAGOU's OpenAI-compatible provider).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

TASK = re.compile(r"Task (\S+) \((\w+), priority")
OLD_RETURN = "    return notional / volume"
NEW_RETURN = "    if not volume:\n        return None  # no trades in the window: no price yet\n    return notional / volume"
OLD_QUOTE = '    return {"symbol": symbol, "price": str(average_price(trades)), "trades": len(trades)}'
NEW_QUOTE = '    price = average_price(trades)\n    return {"symbol": symbol, "price": None if price is None else str(price), "trades": len(trades)}'


def _calls(chat: list[dict]) -> list[tuple[str, dict, str]]:
    """(tool name, args, result) for each tool call so far."""
    results = {m["tool_call_id"]: m["content"] for m in chat if m["role"] == "tool"}
    out = []
    for m in chat:
        for c in m.get("tool_calls") or []:
            out.append((c["function"]["name"], json.loads(c["function"]["arguments"] or "{}"), results.get(c["id"], "")))
    return out


def decide(chat: list[dict], tools: list[str]) -> tuple[str, object]:
    system = chat[0]["content"]
    brief = next((m["content"] for m in chat if m["role"] == "user"), "")
    m = TASK.search(brief)
    kind = m.group(2) if m else "general"
    calls = _calls(chat)
    names = [c[0] for c in calls]
    resumed = any("Results of the tasks you delegated" in (x.get("content") or "") for x in chat if x["role"] == "user")
    ev = re.search(r'"id": "(evt\w+)"', brief)

    def complete(summary: str, outcome: str = "resolved", **details):
        return "tools", [("complete_task", {"summary": summary, "outcome": outcome, "details": details})]

    # Verification after a fix landed: run the tests again on a fresh copy.
    if kind == "verify":
        if "run_tests" in tools and "run_tests" not in names:
            return "tools", [("run_tests", {})]
        res = next((json.loads(r) for n, _, r in calls if n == "run_tests" and r.startswith("{")), None)
        if res and res.get("passed"):
            return complete("验证通过：修复后测试全部通过，原来的 ZeroDivisionError 不再出现。", tests=res.get("summary"))
        return complete("验证没有通过，问题还在。", "needs_human")

    # A fix task: change the code in the isolated copy, run the tests, submit a patch for review.
    if "submit_patch" in tools and kind == "fix":
        if "repo_read" not in names:
            return "tools", [("repo_read", {"path": "pricing.py"})]
        if names.count("repo_replace") < 2:
            old, new = (OLD_RETURN, NEW_RETURN) if "repo_replace" not in names else (OLD_QUOTE, NEW_QUOTE)
            return "tools", [("repo_replace", {"path": "pricing.py", "old": old, "new": new})]
        if "run_tests" not in names:
            return "tools", [("run_tests", {})]
        tests = next(json.loads(r) for n, _, r in calls if n == "run_tests")
        if not tests.get("passed"):
            return complete("修改后测试仍然失败，没有提交补丁。", "needs_human", tests=tests.get("summary"))
        if "submit_patch" not in names:
            return "tools", [("submit_patch", {"title": "Return no price when there are no trades",
                                               "description": "average_price() divided by the total volume, so a window with no trades raised "
                                                              "ZeroDivisionError and /price returned 500. It now returns None and quote() reports price null."})]
        patch = next(json.loads(r) for n, _, r in calls if n == "submit_patch")
        return complete(f"已修复并提交待审核补丁 {patch.get('artifact_id')}（{patch.get('stat')}），隔离环境中测试通过。", "resolved",
                        artifact_id=patch.get("artifact_id"), approval_id=patch.get("approval_id"))

    # An investigation handed over by another agent: find the root cause in the code, hand the fix on.
    if kind == "investigate" and "from System" not in brief and "repo_search" in tools:
        if resumed:
            res = _last_result_text(chat)
            return complete(f"根本原因：pricing.average_price() 在没有成交时除以 0。修复已交给修复 Agent：{res}", "resolved",
                            root_cause="average_price divides by total volume without checking for zero", file="pricing.py")
        if "repo_search" not in names:
            return "tools", [("repo_search", {"pattern": "def average_price"})]
        if "repo_read" not in names:
            return "tools", [("repo_read", {"path": "pricing.py"})]
        return "tools", [("delegate_task", {"kind": "fix", "objective": "修复 pricing.average_price() 在没有成交时的 ZeroDivisionError：没有成交时返回 None，"
                                                                          "quote() 返回 price=null；运行测试后提交待审核补丁。",
                                            "evidence": {"file": "pricing.py", "function": "average_price",
                                                         "root_cause": "notional / volume with volume == 0 when trades is empty",
                                                         "failing_test": "test_pricing.AveragePrice.test_no_trades"},
                                            "required_permissions": ["code:write", "code:test", "code:propose"], "wait_for_result": True})]

    # The first responder for a problem event: look at the evidence, hand the investigation on, report when it returns.
    if kind == "investigate":
        if resumed:
            res = _last_result_text(chat)
            return complete(f"问题已处理：{res}", "resolved")
        if "get_event" not in names and ev:
            return "tools", [("get_event", {"event_id": ev.group(1)})]
        if "delegate_task" in tools:
            return "tools", [("delegate_task", {"kind": "investigate", "objective": "调查 /price 接口的 ZeroDivisionError：找出根本原因并安排修复。",
                                                "evidence": {"event_id": ev.group(1) if ev else None, "error": "ZeroDivisionError in average_price"},
                                                "wait_for_result": True})]
        return complete("发现了错误，但我没有委派权限。", "needs_human")

    # Chain events: report facts only.
    if kind == "analyze":
        if "token_flows" in tools and "token_flows" not in names:
            return "tools", [("token_flows", {})]
        return complete("链上事实：观察到一笔转账（见证据）。统计：见 token_flows。推断：无。转账本身不能说明任何不法行为。", "no_issue")

    return "text", "没有需要做的事。"


def _last_result_text(chat: list[dict]) -> str:
    for m in reversed(chat):
        if m["role"] == "user" and "Results of the tasks you delegated" in (m.get("content") or ""):
            try:
                kids = json.loads(m["content"].split("\n", 1)[1].rsplit("\nContinue the task", 1)[0])
                return "；".join(f"{k['agent']}：{(k.get('result') or {}).get('summary', k['status'])}" for k in kids)[:600]
            except (ValueError, KeyError, IndexError):
                return "（子任务已结束）"
    return ""


def openai_answer(body: dict) -> dict:
    tools = [t["function"]["name"] for t in body.get("tools", [])]
    what, out = decide(body["messages"], tools)
    usage = {"prompt_tokens": 1200, "completion_tokens": 150}
    if what == "text":
        return {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage}
    calls = [{"id": f"call_{i}_{abs(hash(json.dumps(a, sort_keys=True))) % 10**8}", "type": "function",
              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for i, (n, a) in enumerate(out)]
    return {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}, "finish_reason": "tool_calls"}],
            "usage": usage}


class ScriptedProvider:
    """In-process provider over decide(); shaped like the Anthropic provider so the runtime treats it the same."""

    def __init__(self, name: str = "anthropic", model: str = "scripted", fail: int = 0) -> None:
        self.name = name
        self.model = model
        self.fail = fail  # raise on the first N calls (provider outages)
        self.calls = 0

    def complete(self, *, model, system, messages, tools, limits, on_text, should_stop, timeout_s):
        from agent.workforce.openai_compat import to_chat
        from agent.workforce.runtime import ModelTurn, ToolUse

        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise ConnectionError("scripted outage")
        what, out = decide(to_chat(system, messages), [t["name"] for t in tools])
        usage = {"input_tokens": 1200, "output_tokens": 150}
        if what == "text":
            return ModelTurn(content=[{"type": "text", "text": out}], text=out, tool_uses=[], stop_reason="end_turn", usage=usage, model=self.model,
                             price=(Decimal(0), Decimal(0)), foreign=True)
        uses = [ToolUse(f"tu_{self.calls}_{i}", n, a) for i, (n, a) in enumerate(out)]
        content = [{"type": "tool_use", "id": u.id, "name": u.name, "input": u.input} for u in uses]
        return ModelTurn(content=content, text="", tool_uses=uses, stop_reason="tool_use", usage=usage, model=self.model,
                         price=(Decimal("1"), Decimal("5")), foreign=True)
