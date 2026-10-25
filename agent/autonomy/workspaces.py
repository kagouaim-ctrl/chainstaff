"""Workspace resources: what a workspace hands its agents to look after.

A workspace is a project: GitHub repositories (or local repositories under an allowed folder, for tests and
self-hosting), BSC contract and token addresses, wallets, documents, API endpoints and other connected services.
Agents only see the resources of the workspace a task belongs to, and only if they were given that workspace.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
KINDS = ("repos", "contracts", "tokens", "wallets", "docs", "endpoints", "services")
MAX_ITEMS = 50


class ResourceError(ValueError):
    pass


def local_roots(env: dict | None = None) -> list[Path]:
    """KAGOU_LOCAL_REPO_ROOTS: folders (os.pathsep-separated) a local repository may live under. Empty: none allowed."""
    raw = (env if env is not None else os.environ).get("KAGOU_LOCAL_REPO_ROOTS", "")
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def _s(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def _url(v: Any, what: str, allow_http_local: bool = True) -> str:
    u = _s(v, 500)
    p = urlsplit(u)
    if p.scheme not in ("https", "http") or not p.netloc:
        raise ResourceError(f"{what} 不是 http(s) 地址：{u}")
    return u


def clean(resources: Any, env: dict | None = None) -> dict:
    if resources is None:
        return {}
    if not isinstance(resources, dict):
        raise ResourceError("resources 必须是对象")
    out: dict[str, list] = {}
    for kind in KINDS:
        items = resources.get(kind) or []
        if not isinstance(items, list):
            raise ResourceError(f"{kind} 必须是列表")
        if len(items) > MAX_ITEMS:
            raise ResourceError(f"{kind} 最多 {MAX_ITEMS} 项")
        out[kind] = [_item(kind, x, env) for x in items]
    return out


def _item(kind: str, x: Any, env: dict | None) -> dict:
    if not isinstance(x, dict):
        raise ResourceError(f"{kind} 的每一项必须是对象")
    if kind == "repos":
        name = _s(x.get("name"), 80)
        provider = x.get("provider") or ("github" if x.get("url") else "local")
        branch = _s(x.get("branch"), 100) or "main"
        test = _s(x.get("test_command"), 300)
        if test:
            try:
                argv = shlex.split(test)
            except ValueError as e:
                raise ResourceError(f"测试命令格式不对：{e}") from None
            if not any(argv):
                raise ResourceError("测试命令不能为空")
        if provider == "github":
            url = _s(x.get("url"), 300)
            m = GITHUB.match(url)
            if not m:
                raise ResourceError(f"GitHub 仓库地址应该像 https://github.com/owner/repo：{url}")
            return {"name": name or f"{m.group(1)}/{m.group(2)}", "provider": "github", "url": f"https://github.com/{m.group(1)}/{m.group(2)}",
                    "owner": m.group(1), "repo": m.group(2), "branch": branch, "test_command": test}
        if provider == "local":
            path = Path(_s(x.get("path"), 500)).expanduser()
            roots = local_roots(env)
            if not roots:
                raise ResourceError("这个服务器不允许本地仓库（KAGOU_LOCAL_REPO_ROOTS 没有设置）")
            rp = path.resolve()
            if not any(rp == r or r in rp.parents for r in roots):
                raise ResourceError(f"本地仓库必须在 KAGOU_LOCAL_REPO_ROOTS 允许的目录下：{path}")
            if not (rp / ".git").exists():
                raise ResourceError(f"{path} 不是 git 仓库")
            return {"name": name or rp.name, "provider": "local", "path": str(rp), "branch": branch, "test_command": test}
        raise ResourceError("仓库 provider 只能是 github 或 local")
    if kind in ("contracts", "tokens", "wallets"):
        a = _s(x.get("address"), 42)
        if not ADDR.match(a):
            raise ResourceError(f"地址格式不对：{a}")
        d = {"address": a.lower(), "label": _s(x.get("label"), 80), "network": _s(x.get("network"), 40) or "bsc"}
        if kind == "tokens":
            d["symbol"] = _s(x.get("symbol"), 20)
        return d
    if kind == "docs":
        return {"title": _s(x.get("title"), 120), "url": _url(x.get("url"), "文档"), "note": _s(x.get("note"), 500)}
    if kind == "endpoints":
        return {"name": _s(x.get("name"), 80) or "endpoint", "url": _url(x.get("url"), "API 地址")}
    return {"name": _s(x.get("name"), 80), "kind": _s(x.get("kind"), 40), "note": _s(x.get("note"), 500)}


def repo(ws: dict, name: str | None = None) -> dict | None:
    repos = (ws.get("resources") or {}).get("repos") or []
    if not repos:
        return None
    if not name:
        return repos[0]
    return next((r for r in repos if r["name"] == name), None)


def summary(ws: dict) -> str:
    """What the workspace contains, for an agent's prompt. Addresses and URLs, no secrets (there are none here)."""
    r = ws.get("resources") or {}
    lines = [f"Workspace: {ws['name']} ({ws['id']})" + (f" — {ws['description']}" if ws.get("description") else "")]
    for x in r.get("repos") or []:
        lines.append(f"- repository {x['name']} ({x['provider']}, branch {x['branch']})" + (f", tests: `{x['test_command']}`" if x.get("test_command") else ", no test command"))
    for k, label in (("contracts", "contract"), ("tokens", "token"), ("wallets", "wallet")):
        for x in r.get(k) or []:
            lines.append(f"- {label} {x['address']}" + (f" ({x.get('symbol') or x['label']})" if x.get("symbol") or x.get("label") else ""))
    for x in r.get("endpoints") or []:
        lines.append(f"- API endpoint {x['name']}: {x['url']}")
    for x in r.get("docs") or []:
        lines.append(f"- document {x['title']}: {x['url']}")
    for x in r.get("services") or []:
        lines.append(f"- service {x['name']} ({x['kind']})")
    return "\n".join(lines)
