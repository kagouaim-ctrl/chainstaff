"""Which models agents use, what they cost, and the request options each one accepts.

The default is the cheapest Claude model (Claude Haiku 5.5); an agent can name another in its own settings, and
KAGOU_DEFAULT_MODEL changes the default for the whole server. Costs come from the usage the API returns times the
published price; a model with no known price is charged 0 and reported as unpriced, never given a made-up figure.
"""

from __future__ import annotations

import os
from decimal import Decimal

CHEAPEST_MODEL = "claude-haiku-5-5"
DEFAULT_MODEL = os.environ.get("KAGOU_DEFAULT_MODEL") or CHEAPEST_MODEL

# USD per million tokens (input, output) on the Claude API; CHAINSTAFF_MODEL_PRICE overrides all of them.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5-1": (Decimal(10), Decimal(50)),
    "claude-fable-5": (Decimal(10), Decimal(50)),
    "claude-opus-5-5": (Decimal(4), Decimal(20)),
    "claude-opus-5": (Decimal(5), Decimal(25)),
    "claude-opus-4-8": (Decimal(5), Decimal(25)),
    "claude-sonnet-5-5": (Decimal(2), Decimal(10)),
    "claude-sonnet-5": (Decimal(2), Decimal(10)),
    "claude-haiku-5-5": (Decimal("0.1"), Decimal("0.5")),
}
# Claude Haiku 5.5 has a second rate card for prompts over 100K tokens.
LONG_PROMPT_TOKENS = 100_000
LONG_PROMPT_PRICES: dict[str, tuple[Decimal, Decimal]] = {"claude-haiku-5-5": (Decimal("0.5"), Decimal("2.5"))}

# Models that accept the server-side refusal fallback; on the others (Claude Haiku 5.5) the field is not sent.
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


def _match(model: str, table: dict) -> tuple[Decimal, Decimal] | None:
    for name, p in table.items():
        if model == name or model.startswith(name + "-"):
            return p
    return None


def price_of(model: str, override: tuple[Decimal, Decimal] | None, input_tokens: int = 0) -> tuple[Decimal, Decimal] | None:
    if override:
        return override
    if input_tokens > LONG_PROMPT_TOKENS:
        long = _match(model, LONG_PROMPT_PRICES)
        if long:
            return long
    return _match(model, PRICES)


def cost_of(model: str, input_tokens: int, output_tokens: int, override: tuple[Decimal, Decimal] | None) -> Decimal | None:
    """USD for one call, or None when the model's price is unknown."""
    p = price_of(model, override, input_tokens)
    if p is None:
        return None
    return (Decimal(input_tokens) * p[0] + Decimal(output_tokens) * p[1]) / Decimal(1_000_000)


def anthropic_extras(model: str) -> dict:
    """Request options that depend on the model: the refusal fallback only where the model takes it."""
    if any(model == m or model.startswith(m + "-") for m in FALLBACK_MODELS):
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return {}


# Qwen on Alibaba Cloud Model Studio, Singapore region, International deployment: USD per million tokens by the
# input size of the request, (input tokens up to, input, output), from the published list prices (checked 2026-10-25,
# alibabacloud.com/help/en/model-studio/billing-for-model-studio). Other regions bill in CNY at other prices, so these
# apply only to the Singapore endpoints; elsewhere set AI_PRICE. Prices are list prices before limited-time discounts
# and before the new-user free quota, so the recorded cost can be higher than the bill, never lower.
K = 1024
QWEN_SG_PRICES: dict[str, list[tuple[int, Decimal, Decimal]]] = {
    "qwen3.7-plus": [(256 * K, Decimal("0.4"), Decimal("1.6")), (1024 * K, Decimal("1.2"), Decimal("4.8"))],
    "qwen3.6-plus": [(256 * K, Decimal("0.5"), Decimal("3")), (1024 * K, Decimal("2"), Decimal("6"))],
    "qwen3.5-plus": [(256 * K, Decimal("0.4"), Decimal("2.4")), (1024 * K, Decimal("0.5"), Decimal("3"))],
    "qwen-plus": [(256 * K, Decimal("0.4"), Decimal("1.2")), (1024 * K, Decimal("1.2"), Decimal("3.6"))],
    "qwen3.7-flash": [(32 * K, Decimal("0.03"), Decimal("0.13")), (256 * K, Decimal("0.1"), Decimal("0.4")), (1024 * K, Decimal("0.2"), Decimal("0.8"))],
    "qwen3.5-flash": [(1024 * K, Decimal("0.1"), Decimal("0.4"))],
    "qwen-flash": [(256 * K, Decimal("0.05"), Decimal("0.4")), (1024 * K, Decimal("0.25"), Decimal("2"))],
    "qwen-max": [(10**9, Decimal("1.6"), Decimal("6.4"))],
}
# qwen-plus bills thinking output separately (the other models above list one output price).
QWEN_SG_THINKING_OUTPUT = {"qwen-plus": [Decimal("4"), Decimal("12")]}
QWEN_SG_HOSTS = ("dashscope-intl.aliyuncs.com", ".ap-southeast-1.maas.aliyuncs.com")


def qwen_price(model: str, input_tokens: int, thinking: bool = False) -> tuple[Decimal, Decimal] | None:
    """The Singapore list price for this Qwen model at this request size, or None for a model not in the table."""
    name = next((n for n in sorted(QWEN_SG_PRICES, key=len, reverse=True) if model == n or model.startswith(n + "-")), None)
    if name is None:
        return None
    tiers = QWEN_SG_PRICES[name]
    i = next((i for i, (upto, _, _) in enumerate(tiers) if input_tokens <= upto), len(tiers) - 1)
    _, pin, pout = tiers[i]
    if thinking and name in QWEN_SG_THINKING_OUTPUT:
        pout = QWEN_SG_THINKING_OUTPUT[name][i]
    return pin, pout

