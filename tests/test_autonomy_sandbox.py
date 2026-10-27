"""Isolated working copies, the test runner, patches, draft pull requests and workspace resources.

Agents only ever change their own copy; the original repository changes only when a human approves a patch."""

from __future__ import annotations

import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from autonomy_kit import Kit, git_repo

from agent.autonomy import sandbox as sbmod
from agent.autonomy.github import GitHub, GitHubError, verify_signature
from agent.autonomy.sandbox import RepoCopy, Sandbox, SandboxError, apply_to_local
from agent.autonomy.service import AutonomyError
from agent.autonomy.workspaces import ResourceError, clean, local_roots, repo, summary


def _sb(tmp_path, **kw) -> Sandbox:
    return Sandbox(tmp_path / "sb", network_isolation=False, **kw)


def _local(path, **kw) -> dict:
    return {"name": "r", "provider": "local", "path": str(path), "branch": "main", "test_command": "python -m unittest -q", **kw}


def test_copy_is_isolated_and_reused(tmp_path):
    src = git_repo(tmp_path / "src")
    (src / "a.txt").write_text("hello\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "x", "--allow-empty"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a"], cwd=src, check=True)
    sb = _sb(tmp_path)
    c = sb.copy("job/1", _local(src))
    assert c.path.parent.name == "job_1" and c.head
    c.write("a.txt", "changed\n")
    assert (src / "a.txt").read_text() == "hello\n"
    again = sb.copy("job/1", _local(src))
    assert (again.path / "a.txt").read_text() == "changed\n"  # kept across runs of the task
    (tmp_path / "sb" / "job_2" / "repo").mkdir(parents=True)  # a broken earlier copy is replaced
    assert (sb.copy("job_2", _local(src)).path / "a.txt").read_text() == "hello\n"
    # A branch that does not exist falls back to the default branch.
    assert (sb.copy("job_3", _local(src, branch="nope")).path / "a.txt").exists()
    old = time.time() - 30 * 86400
    os.utime(tmp_path / "sb" / "job_3", (old, old))
    assert sb.prune(keep={"job_1"}) == 1 and not (tmp_path / "sb" / "job_3").exists()
    assert Sandbox(tmp_path / "missing", network_isolation=False).prune(set()) == 0
    with pytest.raises(SandboxError, match="git clone"):
        sb.copy("job_4", _local(tmp_path / "not-a-repo"))


def test_paths_reads_searches_and_writes(tmp_path):
    src = git_repo(tmp_path / "src")
    c = _sb(tmp_path).copy("t", _local(src))
    c.write("pkg/m.py", "def f():\n    return 1\n" * 3)
    (c.path / "bin.dat").write_bytes(b"\xff\xfe\x00def f")
    (c.path / "big.txt").write_text("def f\n" * 200_000)
    assert "pkg/" in c.list() and "pkg/m.py" in c.list(depth=3)
    with pytest.raises(SandboxError, match="不是目录"):
        c.list("pkg/m.py")
    with pytest.raises(SandboxError, match="没有文件"):
        c.read("nope.py")
    r = c.read("pkg/m.py", 2, 3)
    assert r["from"] == 2 and r["to"] == 3 and r["content"].startswith("2:")
    hits = c.search(r"def f")
    assert {h["path"] for h in hits} == {"pkg/m.py"}  # binary and huge files are skipped
    assert len(c.search("def f", "pkg/m.py")) == 3
    c.write("many.py", "x = 1\n" * 200)
    assert len(c.search("x = 1")) == sbmod.MAX_SEARCH_HITS
    with pytest.raises(SandboxError, match="太大"):
        c.write("x.txt", "x" * (sbmod.MAX_WRITE + 1))
    with pytest.raises(SandboxError, match="没有文件"):
        c.replace("nope.py", "a", "b")
    os.symlink("/etc", c.path / "escape")
    with pytest.raises(SandboxError, match="不在仓库副本里"):
        c.read("escape/passwd")
    for i in range(450):
        (c.path / f"f{i}.txt").write_text("x")
    assert c.list()[-1].startswith("…")
    long = c.write("long.txt", "y" * 70_000)
    assert long["created"] and c.read("long.txt")["content"].endswith("后面的行）")


def test_tests_run_with_a_scrubbed_environment_and_limits(tmp_path, monkeypatch):
    src = git_repo(tmp_path / "src")
    (src / "t.py").write_text("import os, sys, time\nprint(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))\n"
                              "time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)\nsys.exit(0)\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "t"], cwd=src, check=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    sb = _sb(tmp_path)
    c = sb.copy("t", _local(src, test_command="python t.py"))
    r = c.run_tests()
    assert r["passed"] and r["output"].startswith("[]") and r["network_isolated"] is False
    slow = sb.copy("t2", _local(src, test_command="python t.py 5"))
    r = slow.run_tests(timeout_s=1)
    assert r["timed_out"] and not r["passed"] and r["exit_code"] == -1
    with pytest.raises(SandboxError, match="测试命令"):
        sb.copy("t3", _local(src, test_command="")).run_tests()
    noisy = sb.copy("t4", _local(src, test_command="python -c \"print('x' * 30000)\""))
    assert len(noisy.run_tests()["output"]) == sbmod.MAX_OUTPUT
    sb.netns = True
    seen = {}
    monkeypatch.setattr(sbmod.subprocess, "run", lambda argv, **kw: seen.setdefault("argv", argv) and SimpleNamespace(returncode=0, stdout="ok", stderr="warn"))
    out = c.run_tests()
    assert seen["argv"][:3] == ["unshare", "-rn", "--"] and out["network_isolated"] and out["output"] == "ok\nwarn"


def test_can_unshare_probe(monkeypatch):
    monkeypatch.setattr(sbmod.shutil, "which", lambda n: None)
    assert sbmod._can_unshare() is False
    monkeypatch.setattr(sbmod.shutil, "which", lambda n: "/usr/bin/unshare")
    monkeypatch.setattr(sbmod.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    assert sbmod._can_unshare() is True

    def boom(*a, **kw):
        raise OSError("no")

    monkeypatch.setattr(sbmod.subprocess, "run", boom)
    assert sbmod._can_unshare() is False


def test_apply_to_local_only_after_approval_and_only_clean(tmp_path):
    src = git_repo(tmp_path / "src")
    (src / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a"], cwd=src, check=True)
    sb = _sb(tmp_path)
    c = sb.copy("t", _local(src))
    c.write("a.txt", "two\n")
    d = c.diff()
    assert d["files"] == ["a.txt"] and "1 file changed" in d["stat"] and not d["stat"].startswith(" ")
    with pytest.raises(SandboxError, match="本地仓库"):
        apply_to_local(sb, {"provider": "github"}, d["diff"], "m")
    (src / "a.txt").write_text("edited by hand\n")
    with pytest.raises(SandboxError, match="未提交"):
        apply_to_local(sb, _local(src), d["diff"], "m")
    subprocess.run(["git", "checkout", "--", "a.txt"], cwd=src, check=True)
    (src / "build.log").write_text("untracked output")  # untracked files neither block the patch nor get committed
    commit = apply_to_local(sb, _local(src), d["diff"].rstrip("\n"), "Apply")
    assert (src / "a.txt").read_text() == "two\n" and len(commit) == 40
    assert "build.log" not in subprocess.run(["git", "show", "--name-only", "HEAD"], cwd=src, capture_output=True, text=True).stdout
    (src / "build.log").unlink()
    with pytest.raises(SandboxError, match="git apply"):
        apply_to_local(sb, _local(src), d["diff"], "again")  # already applied


def test_git_credentials_never_go_in_urls_or_arguments(tmp_path):
    sb = Sandbox(tmp_path, github_token="ghp_secret", network_isolation=False)
    env = sb._git_env()
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader" and "ghp_secret" not in env["GIT_CONFIG_VALUE_0"]
    assert "ghp_secret" not in str(sorted(env.items()))
    calls = []
    sb.git = lambda args, cwd=None, timeout=120, check=True: calls.append(args) or SimpleNamespace(returncode=0, stdout="", stderr="")
    sb._clone({"provider": "github", "owner": "o", "repo": "r", "branch": "dev"}, tmp_path / "x")
    assert calls[0] == ["clone", "--depth", "50", "--branch", "dev", "https://github.com/o/r.git", str(tmp_path / "x")]
    assert all("ghp_secret" not in " ".join(a) for a in calls)
    assert sb.info()["kind"] == "process" and sb.info()["env_scrubbed"]


def test_github_draft_pr_and_signatures():
    with pytest.raises(GitHubError):
        GitHub("")
    sent = {}

    def post(url, headers, body):
        sent.update(url=url, headers=headers, body=body)
        return 201, {"number": 5, "html_url": "https://github.com/o/r/pull/5", "draft": True}

    pr = GitHub("tok", post=post).draft_pr("o", "r", head="kagou/job_1", base="main", title="t" * 300, body="b")
    assert pr == {"number": 5, "url": "https://github.com/o/r/pull/5", "draft": True}
    assert sent["body"]["draft"] is True and len(sent["body"]["title"]) == 240 and sent["url"].endswith("/repos/o/r/pulls")
    with pytest.raises(GitHubError, match="422"):
        GitHub("tok", post=lambda *a: (422, {"message": "branch exists"})).draft_pr("o", "r", head="h", base="b", title="t", body="b")
    with pytest.raises(GitHubError, match="500"):
        GitHub("tok", post=lambda *a: (500, "oops")).draft_pr("o", "r", head="h", base="b", title="t", body="b")
    import hashlib
    import hmac

    sig = "sha256=" + hmac.new(b"s", b"body", hashlib.sha256).hexdigest()
    assert verify_signature("s", b"body", sig) and not verify_signature("s", b"other", sig)
    assert not verify_signature("", b"body", sig) and not verify_signature("s", b"body", None) and not verify_signature("s", b"body", "sha1=x")


def test_patch_for_a_github_repo_opens_a_draft_pr_and_never_merges(tmp_path):
    prs = []

    def post(url, headers, body):
        prs.append(body)
        return (201, {"number": 12, "html_url": "https://github.com/o/r/pull/12"}) if len(prs) == 1 else (422, {"message": "nope"})

    k = Kit(tmp_path, env={"KAGOU_GITHUB_TOKEN": "ghp_x"}, github_post=post)
    try:
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(k.repo), str(origin)], check=True)
        ws = k.workspace()
        k.at.db.set_resources(ws["id"], {"repos": [{"name": "o/r", "provider": "github", "url": "https://github.com/o/r", "owner": "o", "repo": "r",
                                                    "branch": "main", "test_command": "python -m unittest -q"}]})
        a = k.agent(None, "Fixer", ws, responsibility="修复代码", permissions=["code:read", "code:write", "code:propose", "github:draft_pr"])
        ghrepo = k.at.db.workspace(ws["id"])["resources"]["repos"][0]

        def ctx_for(tid):
            t, _ = k.at.wakeup.create_task(workspace_id=ws["id"], objective=f"fix {tid}", kind="fix", to_agent_id=a["id"], required=["code:write"])
            dest = tmp_path / "copies" / tid
            subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
            c = RepoCopy(k.at.sandbox, dest, ghrepo)
            c.write("pricing.py", c.read("pricing.py")["content"] and "VALUE = 1\n")
            return SimpleNamespace(task=k.at.db.task(t["id"]), agent=k.at.db.agent(a["id"]), run_id=None, artifacts=[]), c

        ctx, c = ctx_for("one")
        out = k.at.submit_patch(ctx, c, title="Fix it", description="why", diff=c.diff(), tests=None)
        assert out["status"] == "pr_open" and out["pr_url"].endswith("/12") and prs[0]["draft"] is True and prs[0]["base"] == "main"
        branches = subprocess.run(["git", "branch"], cwd=origin, capture_output=True, text=True).stdout
        assert "kagou/" in branches and "* main" in branches  # pushed to a new branch, main untouched
        ap = k.at.db.approval(out["approval_id"])
        assert ap["title"].startswith("审核 Draft PR #12") and k.at.db.activity(types=["pr.created"])
        res = k.at.decide(ap["id"], True, "user")
        assert "不会合并" in res["note"] and k.at.db.artifact(out["artifact_id"])["status"] == "approved"
        ctx, c = ctx_for("two")
        out = k.at.submit_patch(ctx, c, title="Fix again", description="", diff=c.diff(), tests={"command": "x", "exit_code": 1, "passed": False, "secs": 1, "network_isolated": False})
        assert out["status"] == "pending_review" and "422" in out["pr_error"]
        assert "手动应用" in k.at.decide(out["approval_id"], True, "user")["note"]
        k.at.github = None
        ctx, c = ctx_for("three")
        out = k.at.submit_patch(ctx, c, title="No token", description="", diff=c.diff(), tests=None)
        assert "KAGOU_GITHUB_TOKEN" in out["pr_error"]
        assert "手动应用" in k.at.decide(out["approval_id"], True, "user")["note"]
    finally:
        k.close()


def test_approving_a_patch_that_no_longer_applies(tmp_path):
    k = Kit(tmp_path)
    try:
        ws = k.workspace()
        k.trio(ws)
        k.log_error(ws)
        k.drain()
        [ap] = k.at.db.approvals(status="pending")
        (k.repo / "pricing.py").write_text("changed by someone\n")
        with pytest.raises(AutonomyError, match="补丁没有应用"):
            k.at.decide(ap["id"], True, "user")
        assert k.at.db.approval(ap["id"])["status"] == "pending"
    finally:
        k.close()


def test_workspace_resources(tmp_path):
    root = tmp_path / "repos"
    good = git_repo(root / "svc")
    env = {"KAGOU_LOCAL_REPO_ROOTS": str(root)}
    out = clean({"repos": [{"url": "https://github.com/o/r.git", "test_command": "pytest -q"}, {"path": str(good)}],
                 "contracts": [{"address": "0x" + "A" * 40, "label": "Vault"}], "tokens": [{"address": "0x" + "b" * 40, "symbol": "KGA"}],
                 "wallets": [{"address": "0x" + "c" * 40}], "docs": [{"title": "Spec", "url": "https://docs.example.com"}],
                 "endpoints": [{"url": "https://api.example.com/health"}], "services": [{"name": "redis", "kind": "cache"}]}, env)
    assert out["repos"][0] == {"name": "o/r", "provider": "github", "url": "https://github.com/o/r", "owner": "o", "repo": "r", "branch": "main", "test_command": "pytest -q"}
    assert out["repos"][1]["name"] == "svc" and out["contracts"][0]["address"] == "0x" + "a" * 40 and out["endpoints"][0]["name"] == "endpoint"
    assert clean(None) == {} and local_roots({}) == []
    bad = [("x", "对象"), ({"repos": "x"}, "列表"), ({"repos": [{}] * 51}, "最多"), ({"repos": ["x"]}, "对象"),
           ({"repos": [{"url": "https://gitlab.com/o/r"}]}, "GitHub"), ({"repos": [{"provider": "svn"}]}, "provider"),
           ({"repos": [{"url": "https://github.com/o/r", "test_command": "'unterminated"}]}, "测试命令"),
           ({"repos": [{"url": "https://github.com/o/r", "test_command": "''"}]}, "不能为空"),
           ({"repos": [{"path": str(tmp_path)}]}, "KAGOU_LOCAL_REPO_ROOTS 允许"), ({"repos": [{"path": str(root)}]}, "不是 git"),
           ({"wallets": [{"address": "0x1"}]}, "地址"), ({"docs": [{"url": "ftp://x"}]}, "http")]
    for res, msg in bad:
        with pytest.raises(ResourceError, match=msg):
            clean(res, env)
    with pytest.raises(ResourceError, match="不允许本地仓库"):
        clean({"repos": [{"path": str(good)}]}, {})
    ws = {"id": "ws_1", "name": "W", "description": "d", "resources": {**out, "repos": [*out["repos"], {**out["repos"][1], "test_command": ""}]}}
    s = summary(ws)
    for want in ("Workspace: W (ws_1) — d", "tests: `pytest -q`", "no test command", "contract 0x" + "a" * 40 + " (Vault)", "token", "(KGA)",
                 "API endpoint endpoint", "document Spec", "service redis (cache)"):
        assert want in s
    assert repo(ws)["name"] == "o/r" and repo(ws, "svc")["provider"] == "local" and repo({"resources": {}}) is None


def test_service_edges(tmp_path):
    k = Kit(tmp_path)
    try:
        with pytest.raises(AutonomyError):
            k.at.create_workspace({"name": "W", "resources": {"repos": "x"}}, "user")
        ws = k.workspace()
        assert k.at.update_workspace(ws["id"], {"description": "pricing"}, "user")["description"] == "pricing"
        with pytest.raises(AutonomyError):
            k.at.update_workspace(ws["id"], {"resources": {"repos": "x"}}, "user")
        assert k.at.name(None) == "System" and k.at.name("user") == "User" and k.at.name("system") == "System" and k.at.name("wch_gone") == "wch_gone"
        assert k.at.name("agt_gone") == "agt_gone"
        with pytest.raises(AutonomyError, match="操作只能是"):
            k.at.set_agent_status(k.agent("investigator", "I", ws)["id"], "explode", "user")
        with pytest.raises(AutonomyError):
            k.at.create_agent({"name": "", "responsibility": "r"}, "user")
        with pytest.raises(AutonomyError):
            k.at.update_agent(k.at.db.agents()[0]["id"], {"wake_on": "x"}, "user")
        k.at.providers.clear()
        assert k.at._default_provider()[0] == "anthropic"
        k.at.providers["openai_compat"] = SimpleNamespace(model="qwen-plus", name="openai_compat")
        assert k.at._default_provider() == ("openai_compat", "qwen-plus")
        k.at.providers.clear()
        k.at.wakeup.create_task(workspace_id=ws["id"], objective="nobody", required=["github:draft_pr"])
        bad, _ = k.at.events.ingest(workspace_id=ws["id"], source="manual", type_="x")
        k.at.db.update_event(bad["id"], processing_status="failed")
        probs = " ".join(k.at.runtime_health()["problems"])
        assert "没有配置模型" in probs and "1 个任务没有合适的 Agent" in probs and "1 个事件处理失败" in probs
        assert k.at.runtime_health()["level"] == "degraded"
        k.at.watchers.chain = SimpleNamespace(status=lambda: {"listener": True})
        assert k.at.runtime_health()["chain"] == {"listener": True}
        sb = Sandbox(tmp_path / "x")  # probes unshare itself
        assert isinstance(sb.netns, bool)
    finally:
        k.close()
