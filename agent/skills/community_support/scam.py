"""Scam detection for crypto community chats. Rules only, no model: every flag has a reason
an admin can read, and the same message always gets the same verdict.

What it catches (the patterns that drain wallets in Telegram groups):
  SECRET_REQUEST   asks someone to send, enter or "verify" a seed phrase or private key
  FAKE_ADMIN       username or display name that copies a real admin (0/o, l/1, rn/m, Cyrillic letters)
  ADMIN_NAME       display name claims to be admin / support / official but is not on the admin list
  PHISHING_LINK    lookalike or punycode domain of an official domain
  SUSPICIOUS_LINK  unknown domain with wallet-drainer words (claim, airdrop, connect, validate, sync…)
  DM_BAIT          "DM me for support" style lures
  GIVEAWAY         "send 1, get 2 back" doubling scams
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HIGH, MEDIUM = "high", "medium"

_CONFUSABLE = str.maketrans({
    "0": "o", "1": "l", "i": "l", "|": "l", "!": "l", "5": "s", "3": "e", "4": "a", "7": "t", "8": "b",
    # Cyrillic and Greek letters that render like Latin ones
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "l", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ӏ": "l", "ο": "o", "α": "a", "ν": "v", "τ": "t", "κ": "k",
})


def skeleton(s: str) -> str:
    """What a name looks like at a glance: case, separators and look-alike characters folded."""
    s = s.lower().lstrip("@").translate(_CONFUSABLE)
    s = s.replace("rn", "m").replace("vv", "w")
    return re.sub(r"[\s_.\-·]", "", s)


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


SECRET = re.compile(r"seed\s*phrase|mnemonic|recovery\s*(?:phrase|words?)|secret\s*(?:phrase|recovery)|private\s*key|(?:12|24)[\s-]*words?|助记词|私钥|恢复短语|密钥|钱包密码", re.I)
ASK = re.compile(r"\b(?:send|share|give|provide|enter|input|type|paste|submit|verify|validate|sync|import|dm|pm|fill)\b|发(?:给|我|送)?|提供|输入|填(?:写|入)?|粘贴|提交|验证|同步|导入|私信|私聊", re.I)
NEGATION = re.compile(r"\b(?:never|don'?t|do\s+not|not|no\s+one)\b|不要|别|切勿|千万|永远不|绝不|不会", re.I)
ADMIN_WORDS = re.compile(r"\b(?:admin|mod(?:erator)?|support|official|helpdesk|team)\b|管理员|管理|客服|官方|版主", re.I)
DM = re.compile(r"\b(?:dm|pm|inbox|message|msg|text)\s+me\b|私聊我|私信我|加我|联系我|找我", re.I)
HELP = re.compile(r"\b(?:help|support|issue|problem|fix|assist|stuck|refund|recover)\b|问题|客服|帮|解决|处理|退款|找回|卡住", re.I)
DOUBLE = re.compile(r"\b(?:double|2x|x2|twice|send\s+back|return\s+double)\b|翻倍|双倍|返还|原路返回|返\s*\d+\s*倍", re.I)
SEND = re.compile(r"\b(?:send|deposit|transfer)\b|转(?:入|账|给)?|打款|充值", re.I)
BAIT = re.compile(r"claim|airdrop|connect|wallet|validat|rectif|sync|migrat|restore|verif|bonus|reward|giveaway|unlock|whitelist|mint|领取|空投|验证|同步|解锁|奖励", re.I)
URL = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'）)]+"
    r"|\b(?:[a-z0-9\-¡-￿]+\.)+(?:com|io|xyz|app|org|net|finance|top|site|online|link|vip|cc|me|co|gg|pro|club|live|fi|exchange|network|info|ai|claims?|biz|tech|digital)\b(?:/[^\s]*)?",
    re.I,
)


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    reason: str


def host_of(url: str) -> str:
    host = re.sub(r"^(?:https?://)", "", url.strip(), flags=re.I).split("/")[0].split("?")[0].split(":")[0]
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _main_label(host: str) -> str:
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host


def _is_official(host: str, official: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in official)


def check_link(url: str, official: list[str]) -> Finding | None:
    host = host_of(url)
    if not host or _is_official(host, official):
        return None
    if "xn--" in host or any(ord(c) > 127 for c in host):
        return Finding("PHISHING_LINK", HIGH, f"{host} 用了特殊字符伪装成正常网址")
    label = _main_label(host)
    for d in official:
        off = _main_label(d)
        if len(off) < 4:
            continue
        if skeleton(label) == skeleton(off) or (len(off) >= 6 and _distance(label, off) <= 2) or off in host.replace(".", "-").split("-") or off in host:
            return Finding("PHISHING_LINK", HIGH, f"{host} 是仿冒的 {d}，不是官方网址")
    if BAIT.search(url):
        return Finding("SUSPICIOUS_LINK", MEDIUM, f"{host} 不是官方网址，而且像领取/连接钱包类的钓鱼页")
    return None


def detect(text: str, username: str = "", display_name: str = "", admins: list[str] = (), official_domains: list[str] = ()) -> list[Finding]:
    admins = [a.lstrip("@").lower() for a in admins if a]
    if username and username.lstrip("@").lower() in admins:
        return []
    found: list[Finding] = []

    # Impersonation: the sender's name copies a real admin.
    for name in (username, display_name):
        sk = skeleton(name or "")
        if sk and any(sk == skeleton(a) or (len(a) >= 6 and _distance(sk, skeleton(a)) == 1) for a in admins):
            found.append(Finding("FAKE_ADMIN", HIGH, f"发言人「{name}」冒充管理员"))
            break
    else:
        if ADMIN_WORDS.search(display_name or "") or ADMIN_WORDS.search(username or ""):
            found.append(Finding("ADMIN_NAME", MEDIUM, f"「{display_name or username}」自称官方或管理员，但不在管理员名单里"))

    for sentence in re.split(r"[。！？!?\n.]+", text):
        if SECRET.search(sentence) and ASK.search(sentence) and not NEGATION.search(sentence):
            found.append(Finding("SECRET_REQUEST", HIGH, "要求提供或“验证”助记词/私钥"))
            break

    for m in URL.finditer(text):
        f = check_link(m.group(0), [d.lower() for d in official_domains])
        if f and all(x.reason != f.reason for x in found):
            found.append(f)

    if DM.search(text) and HELP.search(text):
        found.append(Finding("DM_BAIT", MEDIUM, "引导私聊“处理问题”，官方不会私聊你"))

    if SEND.search(text) and DOUBLE.search(text):
        found.append(Finding("GIVEAWAY", HIGH, "“转币翻倍返还”是骗局"))
    return found


def severity(findings: list[Finding]) -> str | None:
    if not findings:
        return None
    return HIGH if any(f.severity == HIGH for f in findings) else MEDIUM


def warning_text(findings: list[Finding], official_domains: list[str]) -> str:
    lines = ["⚠️ 小心骗局：" + "；".join(f.reason for f in findings) + "。",
             "管理员不会主动私聊你，任何人索要助记词或私钥都是骗子。"]
    if official_domains:
        lines.append("官方网址只有：" + "、".join(official_domains))
    return "\n".join(lines)
