"""The AI airdrop sybil checker: finds farm wallets in an airdrop list before the snapshot.

Read only and rule based, so every removal comes with a reason a human can check:
- many wallets got their first coins from the same address (a farm's funding wallet)
- wallets in the list fund each other in a chain or a star
- many wallets were created within minutes of each other with the same funding amount
- many wallets did the same few transactions in the same order
- wallets with almost no history, or created just before the snapshot
The owner decides what to do with "review" wallets; only "excluded" ones are removed.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Protocol

from agent.rules import normalize_address
from agent.rules.batch import parse_rows
from agent.rules.models import ADDRESS_RE
from agent.store import Store

ROLE = "airdrop_sybil"
HIGH, MEDIUM, INFO = "high", "medium", "info"
CLEAN, REVIEW, EXCLUDED = "clean", "review", "excluded"
VERDICT_NAMES = {CLEAN: "干净", REVIEW: "待复核", EXCLUDED: "剔除"}
RULE_NAMES = {
    "SHARED_FUNDER": "同一资金来源",
    "FUNDING_CHAIN": "名单内互相打钱",
    "BATCH_CREATED": "批量创建",
    "SAME_PATTERN": "相同操作序列",
    "LOW_ACTIVITY": "几乎没有活动",
    "NEW_WALLET": "快照前新建",
    "RISK_LIST": "风险名单",
    "DUPLICATE": "重复地址",
    "INVALID": "地址无效",
    "NO_DATA": "没有链上数据",
}
MAX_ADDRESSES = 3000
PROFILE_MAX_AGE = 7 * 86400


@dataclass(frozen=True)
class Profile:
    address: str
    funder: str | None = None  # who sent the first incoming coins
    fund_amount: str | None = None  # native amount of that first funding, as text
    first_seen: int | None = None  # unix time of the first transaction
    tx_count: int = 0  # capped at the page size of the source
    pattern: tuple[str, ...] = ()  # the first outgoing calls: "to:selector"

    def to_dict(self) -> dict:
        return {"address": self.address, "funder": self.funder, "fund_amount": self.fund_amount,
                "first_seen": self.first_seen, "tx_count": self.tx_count, "pattern": list(self.pattern)}

    @staticmethod
    def from_dict(d: Mapping) -> "Profile":
        return Profile(d["address"], d.get("funder"), d.get("fund_amount"), d.get("first_seen"),
                       int(d.get("tx_count") or 0), tuple(d.get("pattern") or ()))


class ProfileSource(Protocol):
    def profile(self, address: str) -> Profile: ...


class EtherscanProfileSource:
    """First 100 transactions of an address from the Etherscan V2 API (BSC included)."""

    PAGE = 100

    def __init__(self, api_url: str, api_key: str, http_get: Callable | None = None, pause: float = 0.22) -> None:
        if http_get is None:  # pragma: no cover - real network
            import httpx

            http_get = lambda url, params: httpx.get(url, params=params, timeout=20).json()  # noqa: E731
        self.api_url, self.api_key, self.http_get, self.pause = api_url, api_key, http_get, pause

    def profile(self, address: str) -> Profile:
        a = normalize_address(address)
        if self.pause:
            time.sleep(self.pause)  # free tier: 5 calls a second
        data = self.http_get(self.api_url, {"module": "account", "action": "txlist", "address": a, "sort": "asc",
                                            "page": 1, "offset": self.PAGE, "apikey": self.api_key})
        rows = [r for r in (data.get("result") or []) if isinstance(r, dict)]
        if not rows and str(data.get("status")) == "0" and "No transactions" not in str(data.get("message", "")) and data.get("result") not in ([], None):
            raise RuntimeError(f"浏览器 API 返回错误：{str(data.get('result'))[:120]}")
        funder = amount = None
        for r in rows:
            if normalize_address(r.get("to") or "") == a and int(r.get("value") or 0) > 0 and r.get("isError") != "1":
                funder = normalize_address(r["from"])
                amount = f"{(Decimal(int(r['value'])).scaleb(-18)).normalize():f}"
                break
        pattern = tuple(f"{normalize_address(r.get('to') or '')}:{str(r.get('input') or '0x')[:10]}"
                        for r in rows if normalize_address(r.get("from") or "") == a)[:6]
        return Profile(a, funder, amount, int(rows[0]["timeStamp"]) if rows else None, len(rows), pattern)


@dataclass
class SybilSettings:
    cluster_min: int = 5  # this many wallets sharing a funder / creation burst / pattern = a farm
    window_minutes: int = 30
    min_tx: int = 3
    created_after: str = ""  # ISO date: wallets first seen after this are "new"
    ignore_funders: list[str] = field(default_factory=list)  # exchange hot wallets, faucets, bridges
    review_as: str = "keep"  # keep | exclude: what the clean list does with "review" wallets

    KEY = "sybil_settings"

    @classmethod
    def from_dict(cls, d: Mapping) -> "SybilSettings":
        try:
            s = cls(int(d.get("cluster_min", 5)), int(d.get("window_minutes", 30)), int(d.get("min_tx", 3)),
                    str(d.get("created_after") or "").strip(), [str(a).strip() for a in d.get("ignore_funders") or [] if str(a).strip()],
                    str(d.get("review_as") or "keep"))
        except (TypeError, ValueError):
            raise ValueError("反女巫设置里的数字格式不对") from None
        if not 2 <= s.cluster_min <= 1000:
            raise ValueError("多少个地址算一个团伙：要在 2 到 1000 之间")
        if not 1 <= s.window_minutes <= 1440:
            raise ValueError("批量创建的时间窗口：要在 1 到 1440 分钟之间")
        if not 0 <= s.min_tx <= 100:
            raise ValueError("最少交易数：要在 0 到 100 之间")
        if s.created_after:
            try:
                datetime.fromisoformat(s.created_after)
            except ValueError:
                raise ValueError(f"新钱包日期格式不对：{s.created_after}（例如 2026-09-01）") from None
        for a in s.ignore_funders:
            if not ADDRESS_RE.match(a):
                raise ValueError(f"忽略的资金来源地址格式不对：{a}")
        s.ignore_funders = sorted({normalize_address(a) for a in s.ignore_funders})
        if s.review_as not in ("keep", "exclude"):
            raise ValueError("待复核地址的处理方式只能是 keep 或 exclude")
        return s

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("cluster_min", "window_minutes", "min_tx", "created_after", "ignore_funders", "review_as")}

    @classmethod
    def load(cls, store: Store) -> "SybilSettings":
        import json

        raw = store.get_setting(cls.KEY)
        return cls.from_dict(json.loads(raw)) if raw else cls()

    def save(self, store: Store) -> None:
        import json

        store.set_setting(self.KEY, json.dumps(self.to_dict()))


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


class _Clusters:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def parse_addresses(text: str) -> tuple[list[str], list[dict], list[str]]:
    """Addresses from a pasted list or CSV (first column). Returns (valid, invalid rows, problems)."""
    valid, invalid = [], []
    for n, line in enumerate(text.splitlines(), 1):
        first = line.replace("\t", ",").replace(";", ",").split(",")[0].strip().split(" ")[0]
        if not first or first.startswith("#") or (n == 1 and first.lower() in ("address", "wallet", "地址")):
            continue
        (valid if ADDRESS_RE.match(first) else invalid).append(first if ADDRESS_RE.match(first) else {"line": n, "text": first[:60]})
    return [normalize_address(a) for a in valid], invalid, []


def analyze(addresses: Iterable[str], profiles: Mapping[str, Profile], settings: SybilSettings,
            risk: Iterable[str] = (), invalid: Iterable[dict] = ()) -> dict:
    s = settings
    order: list[str] = []
    dupes: dict[str, int] = defaultdict(int)
    for a in addresses:
        a = normalize_address(a)
        if a in dupes:
            dupes[a] += 1
            continue
        dupes[a] = 0
        order.append(a)
    in_list = set(order)
    risk_set = {normalize_address(a) for a in risk}
    ignore = set(s.ignore_funders)
    reasons: dict[str, list[dict]] = {a: [] for a in order}
    clusters = _Clusters()
    cluster_kind: dict[str, set[str]] = defaultdict(set)
    hit = lambda a, code, sev, text: reasons[a].append({"code": code, "severity": sev, "text": text})  # noqa: E731

    # 1. shared first funder
    by_funder: dict[str, list[str]] = defaultdict(list)
    for a in order:
        p = profiles.get(a)
        if p and p.funder and p.funder not in ignore and p.funder != a:
            by_funder[p.funder].append(a)
    for funder, group in by_funder.items():
        if len(group) >= s.cluster_min:
            for a in group:
                hit(a, "SHARED_FUNDER", HIGH, f"和另外 {len(group) - 1} 个地址的第一笔钱都来自 {_short(funder)}")
                clusters.union(a, "funder:" + funder)
            cluster_kind[clusters.find(group[0])].add("SHARED_FUNDER")

    # 2. wallets in the list funding each other
    edges = [(a, profiles[a].funder) for a in order if profiles.get(a) and profiles[a].funder in in_list]
    link = _Clusters()
    for a, f in edges:
        link.union(a, f)
    comp: dict[str, list[str]] = defaultdict(list)
    for a in {x for e in edges for x in e}:
        comp[link.find(a)].append(a)
    for members in comp.values():
        sev = HIGH if len(members) >= 3 else MEDIUM
        for a in members:
            hit(a, "FUNDING_CHAIN", sev, f"和名单里另外 {len(members) - 1} 个地址之间有资金往来（第一笔钱互相转）")
            if sev == HIGH:
                clusters.union(a, members[0])
        if sev == HIGH:
            cluster_kind[clusters.find(members[0])].add("FUNDING_CHAIN")

    # 3. created in a burst with the same funding amount
    by_amount: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for a in order:
        p = profiles.get(a)
        if p and p.first_seen and p.fund_amount:
            by_amount[p.fund_amount].append((p.first_seen, a))
    window = s.window_minutes * 60
    for amount, group in by_amount.items():
        group.sort()
        start, flagged = 0, set()
        for end in range(len(group)):
            while group[end][0] - group[start][0] > window:
                start += 1
            if end - start + 1 >= s.cluster_min:
                flagged.update(a for _, a in group[start:end + 1])
        if flagged:
            first = min(flagged)
            for a in flagged:
                hit(a, "BATCH_CREATED", HIGH, f"和另外 {len(flagged) - 1} 个地址在 {s.window_minutes} 分钟内先后创建，第一笔都收到 {amount} BNB")
                clusters.union(a, first)
            cluster_kind[clusters.find(first)].add("BATCH_CREATED")

    # 4. the same few transactions in the same order
    by_pattern: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for a in order:
        p = profiles.get(a)
        if p and len(p.pattern) >= 2 and p.tx_count <= 20:
            by_pattern[p.pattern].append(a)
    for pattern, group in by_pattern.items():
        if len(group) >= s.cluster_min:
            for a in group:
                hit(a, "SAME_PATTERN", HIGH, f"和另外 {len(group) - 1} 个地址做了完全相同的 {len(pattern)} 步操作")
                clusters.union(a, group[0])
            cluster_kind[clusters.find(group[0])].add("SAME_PATTERN")
        elif len(group) >= 2:
            for a in group:
                hit(a, "SAME_PATTERN", MEDIUM, f"和另外 {len(group) - 1} 个地址的操作完全相同")

    # 5. single-wallet signals
    cutoff = datetime.fromisoformat(s.created_after).replace(tzinfo=timezone.utc).timestamp() if s.created_after else None
    for a in order:
        p = profiles.get(a)
        if a in risk_set:
            hit(a, "RISK_LIST", HIGH, "在老板的风险名单里")
        if dupes[a]:
            hit(a, "DUPLICATE", INFO, f"名单里重复出现 {dupes[a] + 1} 次，只保留一次")
        if p is None:
            hit(a, "NO_DATA", INFO, "没有查到链上数据")
            continue
        if p.tx_count < s.min_tx:
            hit(a, "LOW_ACTIVITY", MEDIUM, f"只有 {p.tx_count} 笔交易（少于 {s.min_tx}）")
        if cutoff and p.first_seen and p.first_seen > cutoff:
            hit(a, "NEW_WALLET", MEDIUM, f"{datetime.fromtimestamp(p.first_seen, timezone.utc):%Y-%m-%d} 才开始用，晚于 {s.created_after}")

    rows, cluster_ids = [], {}
    for a in order:
        sevs = {r["severity"] for r in reasons[a]}
        verdict = EXCLUDED if HIGH in sevs else REVIEW if MEDIUM in sevs else CLEAN
        cid = None
        if verdict == EXCLUDED and a in clusters.parent:
            root = clusters.find(a)
            cid = cluster_ids.setdefault(root, len(cluster_ids) + 1)
        p = profiles.get(a)
        rows.append({"address": a, "verdict": verdict, "cluster": cid, "reasons": reasons[a],
                     "profile": p.to_dict() if p else None})
    sizes: dict[int, int] = defaultdict(int)
    for r in rows:
        if r["cluster"]:
            sizes[r["cluster"]] += 1
    root_of = {v: k for k, v in cluster_ids.items()}
    cluster_list = sorted(
        ({"id": cid, "size": n, "kinds": sorted(cluster_kind.get(root_of[cid], set())),
          "funder": root_of[cid][7:] if root_of[cid].startswith("funder:") else None} for cid, n in sizes.items()),
        key=lambda c: -c["size"])
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in (CLEAN, REVIEW, EXCLUDED)}
    by_rule: dict[str, int] = defaultdict(int)
    for r in rows:
        for code in {x["code"] for x in r["reasons"]}:
            by_rule[code] += 1
    keep = (CLEAN,) if s.review_as == "exclude" else (CLEAN, REVIEW)
    return {
        "addresses": rows,
        "clusters": cluster_list,
        "counts": counts,
        "total": len(order),
        "duplicates": sum(dupes.values()),
        "invalid": list(invalid),
        "by_rule": dict(by_rule),
        "clean": [r["address"] for r in rows if r["verdict"] in keep],
        "with_data": sum(1 for a in order if a in profiles),
    }


class SybilChecker:
    def __init__(self, store: Store, source: ProfileSource | None = None, risk: Callable[[], Iterable[str]] | None = None) -> None:
        self.store = store
        self.source = source
        self.risk = risk or (lambda: ())

    @property
    def ready(self) -> bool:
        return self.source is not None

    def settings(self) -> SybilSettings:
        return SybilSettings.load(self.store)

    def save_settings(self, data: Mapping, by: str) -> SybilSettings:
        s = SybilSettings.from_dict(data)
        s.save(self.store)
        self.store.log_event("audit", f"修改反女巫设置（{s.cluster_min} 个算团伙，窗口 {s.window_minutes} 分钟）", by, employee=ROLE)
        return s

    def start(self, name: str, text: str, by: str, background: bool = True) -> int:
        addresses, invalid, _ = parse_addresses(text)
        if not addresses:
            raise ValueError("名单里没有有效地址：每行一个 0x 开头的地址")
        if len(addresses) > MAX_ADDRESSES:
            raise ValueError(f"一次最多检查 {MAX_ADDRESSES} 个地址，请分批")
        name = name.strip() or f"空投名单 {datetime.now(timezone.utc):%m-%d %H:%M}"
        s = self.settings()
        cid = self.store.add_sybil_check(name, {**s.to_dict(), "invalid": invalid}, addresses, by)
        self.store.log_event("audit", f"开始反女巫检查 #{cid}「{name}」：{len(addresses)} 个地址", by, {"check": cid}, employee=ROLE)
        if background:  # pragma: no cover - exercised through run() in tests
            threading.Thread(target=self.run, args=(cid,), daemon=True).start()
        else:
            self.run(cid)
        return cid

    def run(self, cid: int) -> dict | None:
        c = self.store.sybil_check(cid)
        settings = SybilSettings.from_dict(c["settings"])
        profiles: dict[str, Profile] = {}
        errors = 0
        try:
            for n, a in enumerate(c["addresses"], 1):
                cached = self.store.profile(a, PROFILE_MAX_AGE)
                if cached:
                    profiles[a] = Profile.from_dict(cached)
                elif self.source:
                    try:
                        p = self.source.profile(a)
                    except Exception:  # noqa: BLE001 - one bad lookup must not sink the check
                        errors += 1
                        if errors >= 20 and errors == n:
                            raise  # every lookup so far failed: the key or the API is broken
                    else:
                        self.store.save_profile(a, p.to_dict())
                        profiles[a] = p
                if n % 10 == 0 or n == len(c["addresses"]):
                    self.store.sybil_progress(cid, n)
            result = analyze(c["addresses"], profiles, settings, self.risk(), c["settings"].get("invalid", []))
            result["lookup_errors"] = errors
            result["source"] = bool(self.source)
        except Exception as e:  # noqa: BLE001
            self.store.finish_sybil_check(cid, None, f"{type(e).__name__}: {e}"[:300])
            self.store.log_event("alert", f"反女巫检查 #{cid} 失败：{e}"[:300], "sybil", {"check": cid}, employee=ROLE)
            return None
        self.store.finish_sybil_check(cid, result)
        k = result["counts"]
        self.store.log_event("audit", f"反女巫检查 #{cid} 完成：干净 {k['clean']}，待复核 {k['review']}，剔除 {k['excluded']}（{len(result['clusters'])} 个团伙）",
                             "sybil", {"check": cid}, employee=ROLE)
        return result

    def import_profiles(self, text: str, by: str) -> int:
        """CSV: address,funder,first_seen,tx_count[,fund_amount] for teams that already have the data."""
        rows, _ = parse_rows(text)
        n = 0
        for r in rows:
            parts = [r.address, r.value] + (r.note.split(",") if r.note else [])
            if not ADDRESS_RE.match(parts[0].strip()):
                continue
            funder = parts[1].strip().lower() if ADDRESS_RE.match(parts[1].strip()) else None
            seen = parts[2].strip() if len(parts) > 2 else ""
            try:
                ts = int(seen) if seen.isdigit() else int(datetime.fromisoformat(seen).replace(tzinfo=timezone.utc).timestamp()) if seen else None
                txc = int(parts[3]) if len(parts) > 3 and parts[3].strip() else 0
            except ValueError:
                continue
            amount = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
            self.store.save_profile(parts[0], Profile(normalize_address(parts[0]), funder, amount, ts, txc).to_dict())
            n += 1
        self.store.log_event("audit", f"导入 {n} 个地址的链上资料", by, employee=ROLE)
        return n

    def check(self, cid: int) -> dict:
        c = self.store.sybil_check(cid)
        if not c:
            raise ValueError(f"没有检查 #{cid}")
        return c

    def export(self, cid: int, which: str) -> tuple[str, str]:
        c = self.check(cid)
        if not c["result"]:
            raise ValueError("检查还没完成")
        rows = c["result"]["addresses"]
        if which == "clean":
            return f"airdrop-clean-{cid}.csv", "address\n" + "".join(a + "\n" for a in c["result"]["clean"])
        lines = ["address,verdict,cluster,reasons"]
        for r in rows:
            if which == "all" or r["verdict"] == which:
                why = "；".join(x["text"] for x in r["reasons"]).replace('"', "'")
                lines.append(f'{r["address"]},{VERDICT_NAMES[r["verdict"]]},{r["cluster"] or ""},"{why}"')
        return f"airdrop-{which}-{cid}.csv", "\n".join(lines) + "\n"

    def overview(self) -> dict:
        checks = self.store.sybil_checks()
        return {
            "checks": [{**{k: v for k, v in c.items() if k not in ("addresses", "result")}, "total": len(c["addresses"]),
                        "counts": (c["result"] or {}).get("counts"), "clusters": len((c["result"] or {}).get("clusters", []))} for c in checks],
            "settings": self.settings().to_dict(),
            "ready": self.ready,
        }

    def sybil_text(self) -> str:
        checks = [c for c in self.store.sybil_checks(limit=3)]
        if not checks:
            return "还没有做过反女巫检查。老板在控制台「空投反女巫」里粘贴地址名单就能开始。"
        lines = ["最近的反女巫检查："]
        for c in checks:
            if c["status"] == "running":
                lines.append(f"#{c['id']} {c['name']} · 进行中 {c['progress']}/{len(c['addresses'])}")
            elif c["status"] == "failed":
                lines.append(f"#{c['id']} {c['name']} · 失败：{c['error']}")
            else:
                k = c["result"]["counts"]
                lines.append(f"#{c['id']} {c['name']} · {len(c['addresses'])} 个地址：干净 {k['clean']}，待复核 {k['review']}，剔除 {k['excluded']}")
        return "\n".join(lines)
