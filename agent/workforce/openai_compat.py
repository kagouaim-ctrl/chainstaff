"""Models behind an OpenAI-compatible chat completions API: Qwen first, and anything else that speaks the same shape.

The same endpoint shape is offered by Qwen (Alibaba Cloud Model Studio), Google Gemini (AI Studio), Groq, OpenRouter,
and by local servers (Ollama, vLLM), so one provider covers all of them:

    AI_PROVIDER          qwen | gemini | groq | openrouter | openai_compatible | local   (anthropic: see build_workforce)
    AI_BASE_URL          the provider's OpenAI-compatible base URL, copied from its console, e.g. for Qwen in Singapore
                         https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1. https only, except local
    AI_API_KEY           the provider's API key; stays on the server (optional for local)
    AI_MODEL             the model to use, which must support function calling (Qwen: e.g. qwen-plus)
    AI_PRICE             "input,output" USD per million tokens. Unset: Qwen on a Singapore or Tokyo endpoint uses the published
                         list price; local is 0 (your own hardware); anything else is recorded as unpriced
    AI_ENABLE_THINKING   Qwen only: 1 turns on thinking mode (off by default: cheaper, and its function calling
                         examples turn it off)

KAGOU_OPENAI_COMPAT_URL / _KEY / _MODEL / _PRICE are the older names for the same settings and still work.

The runtime keeps its conversation in the Claude message shape; this module converts it to chat completions on the way
out and the answer back on the way in. Function calling is the provider's own: if the model answers without a valid
function call, nothing is made up here, and the runtime treats the text as the agent's answer.
Requests are not streamed: the text reaches the timeline when the call returns.
"""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlsplit

from .chain import Cancelled
from .models import QWEN_JP_HOSTS, QWEN_SG_HOSTS, qwen_price

KINDS = ("qwen", "gemini", "groq", "openrouter", "openai_compatible", "local")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "host.docker.internal")

FINISH = {"tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens", "stop": "end_turn", "content_filter": "refusal"}


class CompatError(Exception):
    def __init__(self, msg: str, retryable: bool = False) -> None:
        super().__init__(msg)
        self.retryable = retryable


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, Any]:  # pragma: no cover - real network
    import httpx

    try:
        r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise CompatError(f"请求超时（{timeout:.0f}s）", retryable=True) from e
    except httpx.HTTPError as e:
        raise CompatError(f"连接失败：{type(e).__name__}", retryable=True) from e
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": {"message": r.text[:300]}}


def to_chat(system: str, messages: list) -> list[dict]:
    """Claude-shaped history (text, tool_use, tool_result blocks) to chat completions messages."""
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(b["text"] for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
            continue
        for b in content:  # a user turn: tool results (one chat message each) and any text
            if b.get("type") == "tool_result":
                body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"], ensure_ascii=False)
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": ("ERROR: " if b.get("is_error") else "") + body})
            elif b.get("type") == "text":
                out.append({"role": "user", "content": b["text"]})
    return out


def to_functions(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}}
            for t in tools]


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str, price: tuple[Decimal, Decimal] | None = None,
                 post: Callable[[str, dict, dict, float], tuple[int, Any]] | None = None, retries: int = 2, backoff_s: float = 1.0,
                 label: str = "openai_compatible", extra_body: dict | None = None,
                 price_fn: Callable[[str, int], tuple[Decimal, Decimal] | None] | None = None) -> None:
        if not (base_url and api_key and model):
            raise ValueError("AI_BASE_URL, AI_API_KEY and AI_MODEL are all needed")
        parts = urlsplit(base_url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
            raise ValueError(f"AI_BASE_URL 必须是 https（本机模型服务除外）：{base_url}")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.model = model
        self.price = price
        self.price_fn = price_fn
        self.post = post or _http_post
        self.retries = retries
        self.backoff_s = backoff_s
        self.label = label  # which service this is, for the console (qwen, gemini, local, ...)
        self.extra_body = extra_body or {}

    def price_for(self, model: str, input_tokens: int) -> tuple[Decimal, Decimal] | None:
        """USD per million tokens for this call: AI_PRICE, else the provider's published price if known, else None."""
        if self.price is not None:
            return self.price
        return self.price_fn(model, input_tokens) if self.price_fn else None

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    def _request(self, body: dict, timeout_s: float, should_stop: Callable[[], bool]) -> dict:
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        for attempt in range(self.retries + 1):
            if should_stop():
                raise Cancelled("任务已取消")
            try:
                status, data = self.post(self.url, headers, body, timeout_s)
                if status == 200 and isinstance(data, dict) and data.get("choices"):
                    return data
                err = data.get("error") if isinstance(data, dict) else None
                msg = err.get("message") if isinstance(err, dict) else err
                raise CompatError(f"{self.host} 返回 HTTP {status}：{str(msg or data)[:200]}", retryable=status == 429 or status >= 500)
            except CompatError as e:
                if not e.retryable or attempt == self.retries:
                    raise
                time.sleep(self.backoff_s * 2 ** attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _usage(data: dict) -> dict:
        u = data.get("usage") or {}
        return {"input_tokens": int(u.get("prompt_tokens") or 0), "output_tokens": int(u.get("completion_tokens") or 0), "cache_read_input_tokens": 0}

    def complete(self, *, model: str, system: str, messages: list, tools: list[dict], limits, on_text: Callable[[str], None],
                 should_stop: Callable[[], bool], timeout_s: float):
        from .runtime import ModelTurn, ToolUse

        body: dict[str, Any] = {"model": model or self.model, "messages": to_chat(system, messages), "max_tokens": limits.max_tokens, **self.extra_body}
        if tools:
            body["tools"] = to_functions(tools)
        data = self._request(body, timeout_s, should_stop)
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        uses = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = raw  # the runtime answers "参数必须是 JSON 对象" and the model can try again
            uses.append(ToolUse(c.get("id") or f"call_{uuid.uuid4().hex[:12]}", fn.get("name") or "", args))
        if text:
            on_text(text)
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": u.id, "name": u.name, "input": u.input if isinstance(u.input, dict) else {}} for u in uses]
        stop = FINISH.get(choice.get("finish_reason") or "", "end_turn")
        if uses:
            stop = "tool_use"
        usage, used = self._usage(data), data.get("model") or body["model"]
        return ModelTurn(content=content, text=text, tool_uses=uses, stop_reason=stop, usage=usage, model=used,
                         price=self.price_for(used, usage["input_tokens"]), foreign=True)

    def json_call(self, *, model: str | None, system: str, user: str, schema: dict | None, max_tokens: int, timeout_s: float,
                  cancelled: Callable[[], bool] | None = None) -> tuple[str, dict, str]:
        """One answer that must be JSON (the manager's plan). The schema goes in the prompt; the planner validates the result."""
        if schema:
            system += "\nReply with only one JSON object, no prose and no code fence, valid for this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        body = {"model": model or self.model, "max_tokens": max_tokens, **self.extra_body,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        data = self._request(body, timeout_s, cancelled or (lambda: False))
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise CompatError("模型输出被截断")
        if choice.get("finish_reason") == "content_filter":
            raise CompatError("模型拒绝了这个请求")
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return text, self._usage(data), data.get("model") or body["model"]


def _price(v: str, name: str) -> tuple[Decimal, Decimal]:
    try:
        a, b = v.split(",")
        p = (Decimal(a.strip()), Decimal(b.strip()))
    except Exception:
        raise ValueError(f"{name} 应该是 \"输入价,输出价\"（美元 / 百万 Token），例如 0.4,1.2；现在是 {v!r}") from None
    if p[0] < 0 or p[1] < 0:
        raise ValueError(f"{name} 不能是负数")
    return p


def from_env(env: dict, post=None) -> OpenAICompatProvider | None:
    """The provider described by AI_* (or the older KAGOU_OPENAI_COMPAT_*), None if neither is set.
    A half-filled AI_* configuration is an error, so the server does not start believing it has a model."""
    kind = (env.get("AI_PROVIDER") or "").strip().lower()
    if kind and kind != "anthropic":
        if kind == "openai_compat":
            kind = "openai_compatible"
        if kind not in KINDS:
            raise ValueError(f"AI_PROVIDER 只能是 anthropic、{'、'.join(KINDS)}，现在是 {kind!r}")
        url, key, model = env.get("AI_BASE_URL", "").strip(), env.get("AI_API_KEY", "").strip(), env.get("AI_MODEL", "").strip()
        if kind == "local":
            key = key or "local"
        missing = [n for n, v in (("AI_BASE_URL", url), ("AI_API_KEY", key), ("AI_MODEL", model)) if not v]
        if missing:
            raise ValueError(f"AI_PROVIDER={kind} 还需要 {'、'.join(missing)}")
        price = _price(env["AI_PRICE"], "AI_PRICE") if env.get("AI_PRICE") else ((Decimal(0), Decimal(0)) if kind == "local" else None)
        extra: dict = {}
        price_fn = None
        if kind == "qwen":
            thinking = env.get("AI_ENABLE_THINKING", "").strip().lower() in ("1", "true", "yes", "on")
            extra = {"enable_thinking": thinking}
            host = urlsplit(url).hostname or ""
            for region, hosts in (("sg", QWEN_SG_HOSTS), ("jp", QWEN_JP_HOSTS)):
                if any(host == h or (h.startswith(".") and host.endswith(h)) for h in hosts):
                    price_fn = lambda m, n, r=region: qwen_price(m, n, thinking, r)  # noqa: E731
        return OpenAICompatProvider(url, key, model, price, post=post, label=kind, extra_body=extra, price_fn=price_fn)
    url, key, model = env.get("KAGOU_OPENAI_COMPAT_URL"), env.get("KAGOU_OPENAI_COMPAT_KEY"), env.get("KAGOU_OPENAI_COMPAT_MODEL")
    if not (url and key and model):
        return None
    price = _price(env["KAGOU_OPENAI_COMPAT_PRICE"], "KAGOU_OPENAI_COMPAT_PRICE") if env.get("KAGOU_OPENAI_COMPAT_PRICE") else None
    return OpenAICompatProvider(url, key, model, price, post=post)
