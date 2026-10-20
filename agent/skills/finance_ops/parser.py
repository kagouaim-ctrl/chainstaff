"""Turn one chat sentence into a structured payment request.

Deterministic first: common phrasings are parsed with rules. Anything ambiguous
returns a ParseError with a question for the requester, never a guess. A later
LLM fallback must produce the same ParsedRequest type, and its output still
goes through the rule engine like everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from agent.rules import Payee, PaymentRequest

TOKENS = ("USDT", "USDC", "USD1", "BNB")
DEFAULT_TOKEN = "USDT"

# keyword (lowercase) -> category used by Policy.approval_categories
CATEGORY_KEYWORDS: dict[str, str] = {
    "设计": "设计", "design": "设计",
    "开发": "开发", "dev": "开发", "合约": "开发", "contract": "开发",
    "kol": "KOL", "推文": "KOL", "tweet": "KOL", "推广": "KOL",
    "服务器": "基础设施", "infra": "基础设施", "rpc": "基础设施",
    "工资": "工资", "salary": "工资", "月薪": "工资",
    "翻译": "翻译", "translation": "翻译",
}

_ADDR = r"0x[0-9a-fA-F]{40}"
_VERB = r"(?:付给|付|转给|转账给|转账|转|打给|pay|send|transfer)"
_AMOUNT = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_TOKEN = r"(?:(USDT|USDC|USD1|BNB|U)(?![A-Za-z0-9]))?"
# An amount immediately followed by a token, e.g. "300 USDT" or "1,500U".
_AMOUNT_WITH_TOKEN = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:USDT|USDC|USD1|BNB|U)(?![A-Za-z0-9])", re.I)
_ALTERNATIVE = re.compile(r"^(?:或者?|还是|or|/)\s*\d", re.I)

# "付 Alice 300 USDT 设计费" / "pay 0xabc… 300 USDT for design" / "给 Bob 转 1,500 U 合约"
_PATTERNS = [
    re.compile(rf"^\s*{_VERB}\s*(?:to\s+)?(?P<payee>{_ADDR}|[^\s\d,，]+)\s*[,，]?\s*{_AMOUNT}\s*{_TOKEN}\s*[,，。]?\s*(?:for\s+)?(?P<purpose>.*)$", re.I),
    re.compile(rf"^\s*给\s*(?P<payee>{_ADDR}|[^\s\d,，]+)\s*{_VERB}\s*{_AMOUNT}\s*{_TOKEN}\s*[,，。]?\s*(?P<purpose>.*)$", re.I),
]


@dataclass(frozen=True)
class ParsedRequest:
    payee_address: str
    payee_name: str | None  # None when an address was given that isn't on the whitelist
    amount: Decimal
    token: str
    token_assumed: bool  # True when the sender didn't name a token; the bot must echo it back
    purpose: str
    category: str

    def to_payment_request(self, request_id: str, requester: str, at: datetime) -> PaymentRequest:
        return PaymentRequest(
            request_id=request_id,
            requester=requester,
            payee=self.payee_address,
            amount=self.amount,
            token=self.token,
            created_at=at,
            category=self.category,
            memo=self.purpose,
        )


@dataclass(frozen=True)
class ParseError:
    code: str
    question: str  # what to ask the requester


def _category(purpose: str) -> str:
    p = purpose.lower()
    for kw, cat in CATEGORY_KEYWORDS.items():
        if kw in p:
            return cat
    return ""


def _resolve_payee(name: str, payees: Iterable[Payee]) -> list[Payee]:
    n = name.strip().lower()
    return [p for p in payees if n == p.name.lower() or n in (a.lower() for a in p.aliases)]


def parse(text: str, payees: Iterable[Payee]) -> ParsedRequest | ParseError:
    payees = list(payees)
    m = next((p.match(text) for p in _PATTERNS if p.match(text)), None)
    if not m:
        return ParseError("UNRECOGNIZED", "没看懂这条付款请求。请按「付 收款方 金额 币种 用途」发，例如：付 Alice 300 USDT 设计费")

    payee_raw, amount_raw, token_raw = m.group("payee"), m.group(2), m.group(3)
    purpose = m.group("purpose").strip()
    if len(_AMOUNT_WITH_TOKEN.findall(text)) > 1 or _ALTERNATIVE.match(purpose):
        return ParseError("MULTIPLE_AMOUNTS", "这条消息里有不止一个金额，请一次只写一笔：付 收款方 金额 币种 用途")

    # The regex only lets digits, commas and one dot through, so Decimal can't fail here.
    amount = Decimal(amount_raw.replace(",", ""))

    token_assumed = token_raw is None
    token = DEFAULT_TOKEN if token_assumed or token_raw.upper() == "U" else token_raw.upper()

    if re.fullmatch(_ADDR, payee_raw):
        named = [p for p in payees if p.address.lower() == payee_raw.lower()]
        return ParsedRequest(payee_raw, named[0].name if named else None, amount, token, token_assumed, purpose, _category(purpose))

    matches = _resolve_payee(payee_raw, payees)
    if not matches:
        return ParseError("UNKNOWN_PAYEE", f"白名单里没有「{payee_raw}」。请写对方的完整钱包地址，或请老板先把他加进白名单。")
    if len(matches) > 1:
        names = "、".join(p.name for p in matches)
        return ParseError("AMBIGUOUS_PAYEE", f"「{payee_raw}」对应多个收款方：{names}。请写全名或钱包地址。")
    p = matches[0]
    return ParsedRequest(p.address, p.name, amount, token, token_assumed, purpose, _category(purpose))
