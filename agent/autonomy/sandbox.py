"""Isolated working copies for agents that read, change and test code.

Every task gets its own copy of the workspace repository under the data folder. An agent's code tools only ever
touch that copy: paths are resolved inside it (no `..`, no symlinks out, no writes into .git), and the original
repository is never written by an agent. A human approval is what applies a patch to the original.

Tests run the command the workspace configured (never a command the model made up), as an argument list without a
shell, in the copy, with an emptied environment (no API keys, no tokens), CPU / memory / file-size / process limits
and a timeout. When the kernel allows unprivileged user namespaces the command also runs without network access
(`unshare -rn`); the runtime reports whether that is the case, so the console never claims more isolation than
there is. This is process-level isolation, not a container or a VM.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

MAX_READ = 60_000
MAX_WRITE = 400_000
MAX_OUTPUT = 20_000
MAX_SEARCH_HITS = 80


class SandboxError(Exception):
    pass


def _can_unshare() -> bool:
    if not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _limits(cpu_s: int, mem_mb: int):  # pragma: no cover - runs in the child process
    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        except (ValueError, OSError):
            pass
        os.setsid()

    return apply


class Sandbox:
    def __init__(self, root: str | Path, *, github_token: str | None = None, network_isolation: bool | None = None,
                 test_timeout_s: int = 300, mem_mb: int = 2048) -> None:
        self.root = Path(root)  # created on the first copy
        self.github_token = github_token
        self.netns = _can_unshare() if network_isolation is None else network_isolation
        self.test_timeout_s = test_timeout_s
        self.mem_mb = mem_mb

    def info(self) -> dict:
        return {"kind": "process", "network_isolated": self.netns, "env_scrubbed": True, "root": self.root.name,
                "limits": {"test_timeout_s": self.test_timeout_s, "memory_mb": self.mem_mb, "file_size_mb": 50, "processes": 256}}

    def copy(self, task_id: str, repo: dict) -> "RepoCopy":
        """The working copy for this task, cloned on first use and kept across the task's runs (wait, resume, crash)."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)
        dest = self.root / safe / "repo"
        if not (dest / ".git").exists():
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._clone(repo, dest)
        return RepoCopy(self, dest, repo)

    def _git_env(self) -> dict:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root), "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"}
        if self.github_token:
            # The token goes in an environment-provided config entry, never in a URL or an argument list.
            basic = base64.b64encode(f"x-access-token:{self.github_token}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
        return env

    def git(self, args: list[str], cwd: Path | None = None, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
        p = subprocess.run(["git", "-c", "protocol.file.allow=always", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, env=self._git_env(),
                           capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise SandboxError(f"git {args[0]} 失败：{(p.stderr or p.stdout).strip()[:400]}")
        return p

    def _clone(self, repo: dict, dest: Path) -> None:
        branch = repo.get("branch") or "main"
        if repo["provider"] == "local":
            src = repo["path"]
            p = self.git(["clone", "--no-hardlinks", "--branch", branch, src, str(dest)], check=False)
            if p.returncode != 0:
                self.git(["clone", "--no-hardlinks", src, str(dest)])
        else:
            url = f"https://github.com/{repo['owner']}/{repo['repo']}.git"
            self.git(["clone", "--depth", "50", "--branch", branch, url, str(dest)], timeout=300)
        self.git(["config", "user.name", "KAGOU Agent"], cwd=dest)
        self.git(["config", "user.email", "agent@kagou.invalid"], cwd=dest)

    def prune(self, keep: set[str], older_than_s: float = 7 * 86400) -> int:
        n = 0
        now = time.time()
        for d in self.root.iterdir() if self.root.exists() else []:
            if d.is_dir() and d.name not in keep and now - d.stat().st_mtime > older_than_s:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n


class RepoCopy:
    def __init__(self, sb: Sandbox, path: Path, repo: dict) -> None:
        self.sb = sb
        self.path = path.resolve()
        self.repo = repo
        r = sb.git(["rev-parse", "HEAD"], cwd=self.path, check=False)
        self.head = r.stdout.strip() if r.returncode == 0 else ""
        base = sb.git(["rev-list", "--max-parents=0", "HEAD"], cwd=self.path, check=False)
        self._root_commit = base.stdout.split()[0] if base.returncode == 0 and base.stdout.split() else ""

    # ---- paths ----

    def _p(self, rel: str, for_write: bool = False) -> Path:
        rel = (rel or ".").strip().lstrip("/")
        p = (self.path / rel).resolve()
        if p != self.path and self.path not in p.parents:
            raise SandboxError(f"路径不在仓库副本里：{rel}")
        if for_write and (".git" in p.relative_to(self.path).parts):
            raise SandboxError("不能修改 .git")
        return p

    # ---- read ----

    def list(self, rel: str = ".", depth: int = 2) -> list[str]:
        base = self._p(rel)
        if not base.is_dir():
            raise SandboxError(f"不是目录：{rel}")
        out: list[str] = []
        for p in sorted(base.rglob("*")):
            r = p.relative_to(self.path)
            if ".git" in r.parts or any(x in r.parts for x in ("node_modules", "__pycache__", ".venv")):
                continue
            if len(p.relative_to(base).parts) > depth:
                continue
            out.append(str(r) + ("/" if p.is_dir() else ""))
            if len(out) >= 400:
                out.append("…（只列出前 400 项）")
                break
        return out

    def read(self, rel: str, start: int | None = None, end: int | None = None) -> dict:
        p = self._p(rel)
        if not p.is_file():
            raise SandboxError(f"没有文件：{rel}")
        text = p.read_text(errors="replace")
        lines = text.splitlines()
        a = max(1, int(start or 1))
        b = min(len(lines), int(end or len(lines)))
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(a, b + 1))
        if len(body) > MAX_READ:
            body = body[:MAX_READ] + "\n…（已截断，请用 start / end 读取后面的行）"
        return {"path": rel, "lines": len(lines), "from": a, "to": b, "content": body}

    def search(self, pattern: str, rel: str = ".") -> list[dict]:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise SandboxError(f"正则表达式不对：{e}") from None
        hits: list[dict] = []
        base = self._p(rel)
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for p in files:
            r = p.relative_to(self.path)
            if not p.is_file() or ".git" in r.parts or p.stat().st_size > 1_000_000:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="strict").splitlines(), 1):
                    if rx.search(line):
                        hits.append({"path": str(r), "line": i, "text": line.strip()[:300]})
                        if len(hits) >= MAX_SEARCH_HITS:
                            return hits
            except UnicodeDecodeError:
                continue
        return hits

    # ---- write (the copy only) ----

    def write(self, rel: str, content: str) -> dict:
        if len(content) > MAX_WRITE:
            raise SandboxError(f"文件太大（{len(content)} 字符，上限 {MAX_WRITE}）")
        p = self._p(rel, for_write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        return {"path": rel, "created": not existed, "chars": len(content)}

    def replace(self, rel: str, old: str, new: str) -> dict:
        p = self._p(rel, for_write=True)
        if not p.is_file():
            raise SandboxError(f"没有文件：{rel}")
        text = p.read_text()
        n = text.count(old)
        if n != 1:
            raise SandboxError(f"要替换的文字在 {rel} 里出现了 {n} 次，必须正好 1 次（多带几行上下文）")
        p.write_text(text.replace(old, new))
        return {"path": rel, "replaced": 1}

    # ---- tests ----

    def run_tests(self, timeout_s: int | None = None) -> dict:
        cmd = (self.repo.get("test_command") or "").strip()
        if not cmd:
            raise SandboxError("这个仓库没有配置测试命令（Workspace → 仓库 → test_command）")
        argv = shlex.split(cmd)
        if self.sb.netns:
            argv = ["unshare", "-rn", "--", *argv]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.path.parent / "home"), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "TMPDIR": str(self.path.parent / "tmp")}
        for d in ("home", "tmp"):
            (self.path.parent / d).mkdir(exist_ok=True)
        t = timeout_s or self.sb.test_timeout_s
        started = time.monotonic()
        try:
            p = subprocess.run(argv, cwd=self.path, env=env, capture_output=True, text=True, timeout=t,
                               preexec_fn=_limits(t + 5, self.sb.mem_mb), stdin=subprocess.DEVNULL)
            code, out = p.returncode, (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
            timed_out = False
        except subprocess.TimeoutExpired as e:
            code, out, timed_out = -1, ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")), True
        secs = round(time.monotonic() - started, 2)
        tail = out[-MAX_OUTPUT:] if len(out) > MAX_OUTPUT else out
        return {"command": cmd, "exit_code": code, "passed": code == 0, "timed_out": timed_out, "secs": secs,
                "network_isolated": self.sb.netns, "output": tail}

    # ---- result ----

    def diff(self) -> dict:
        self.sb.git(["add", "-A"], cwd=self.path)
        names = self.sb.git(["diff", "--cached", "--name-only", self.head or "HEAD"], cwd=self.path).stdout.split()
        d = self.sb.git(["diff", "--cached", "--no-ext-diff", "--no-color", self.head or "HEAD"], cwd=self.path).stdout
        stat = self.sb.git(["diff", "--cached", "--stat", self.head or "HEAD"], cwd=self.path).stdout.strip().splitlines()
        return {"files": names, "diff": d, "stat": stat[-1].strip() if stat else "", "base": self.head}

    def commit_branch(self, branch: str, message: str) -> str:
        self.sb.git(["checkout", "-B", branch], cwd=self.path)
        self.sb.git(["add", "-A"], cwd=self.path)
        self.sb.git(["-c", "user.name=KAGOU Agent", "-c", "user.email=agent@kagou.invalid", "commit", "-m", message], cwd=self.path)
        return self.sb.git(["rev-parse", "HEAD"], cwd=self.path).stdout.strip()

    def push(self, branch: str) -> None:
        self.sb.git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=self.path, timeout=300)


def apply_to_local(sb: Sandbox, repo: dict, diff: str, message: str) -> str:
    """After a human approved it: apply the patch to the original local repository and commit it. Returns the commit."""
    if repo["provider"] != "local":
        raise SandboxError("只有本地仓库可以由 KAGOU 直接应用补丁；GitHub 仓库请在 GitHub 上合并 Draft PR")
    path = Path(repo["path"])
    status = sb.git(["status", "--porcelain"], cwd=path).stdout.strip()
    if status:
        raise SandboxError("原仓库有未提交的修改，先处理再应用补丁")
    tmp = path / ".git" / "kagou-approved.patch"
    tmp.write_text(diff if diff.endswith("\n") else diff + "\n")
    try:
        sb.git(["apply", "--index", str(tmp)], cwd=path)
        sb.git(["-c", "user.name=KAGOU", "-c", "user.email=agent@kagou.invalid", "commit", "-m", message], cwd=path)
    finally:
        tmp.unlink(missing_ok=True)
    return sb.git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
