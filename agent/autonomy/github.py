"""GitHub: open a draft pull request for an approved-to-propose patch, and check webhook signatures.

KAGOU only ever opens *draft* pull requests on a new branch. It never merges, never pushes to the default branch
and never changes branch protection: merging is a human's decision on GitHub. The token (KAGOU_GITHUB_TOKEN) needs
contents:write and pull_requests:write on the repositories in the workspace, and nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable

API = "https://api.github.com"


class GitHubError(Exception):
    pass


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex hmac of the raw body>."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.removeprefix("sha256="))


class GitHub:
    def __init__(self, token: str, post: Callable[[str, dict, dict], tuple[int, Any]] | None = None) -> None:
        if not token:
            raise GitHubError("没有配置 KAGOU_GITHUB_TOKEN")
        self.token = token
        self._post = post or _http_post

    def draft_pr(self, owner: str, repo: str, *, head: str, base: str, title: str, body: str) -> dict:
        status, data = self._post(f"{API}/repos/{owner}/{repo}/pulls",
                                  {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                                  {"title": title[:240], "head": head, "base": base, "body": body[:60000], "draft": True})
        if status not in (200, 201) or not isinstance(data, dict) or "number" not in data:
            msg = data.get("message") if isinstance(data, dict) else data
            raise GitHubError(f"GitHub 返回 HTTP {status}：{str(msg)[:300]}")
        return {"number": data["number"], "url": data.get("html_url"), "draft": data.get("draft", True)}


def _http_post(url: str, headers: dict, body: dict) -> tuple[int, Any]:  # pragma: no cover - real network
    import httpx

    r = httpx.post(url, headers=headers, json=body, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"message": r.text[:300]}
