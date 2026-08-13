"""Self-improve PR submit + inbound security bot (two-pass guard)."""

from __future__ import annotations

import pytest

from remedy.core.approvals import APPROVALS, ApprovalQueue
from remedy.core.self_inject_draft import write_pending_ship
from remedy.core.self_inject_guard import (
    normalize_rel,
    run_both_passes,
    scan_added_behavior,
    scan_diff_secrets_and_size,
    scan_paths,
)
from remedy.core.self_inject_pr import (
    TOOL_NAME,
    approval_required_for_submit,
    format_inbox_comment,
    payload_fingerprint,
    submit_self_improve_issue,
)


def test_path_jail_blocks_github_and_signing():
    r = scan_paths(
        [
            ".github/workflows/ci.yml",
            "desktop/src-tauri/tauri.conf.json",
            "src/remedy/core/self_inject.py",
        ]
    )
    assert r.ok is False
    msgs = " ".join(f.message + f.path for f in r.findings)
    assert ".github/workflows/ci.yml" in msgs or "forbidden" in msgs


def test_path_jail_allows_src_and_tests():
    r = scan_paths(
        ["src/remedy/core/self_inject.py", "tests/test_self_inject.py"]
    )
    assert r.ok is True


def test_path_jail_blocks_random_root_file():
    r = scan_paths(["README.md"])
    assert r.ok is False


def test_path_jail_blocks_dotdot_escape():
    assert normalize_rel("src/remedy/../../.github/workflows/ci.yml") is None
    r = scan_paths(["src/remedy/../../.github/workflows/ci.yml"])
    assert r.ok is False
    r2 = scan_paths(["src/remedy/foo.py", "../secrets.env"])
    assert r2.ok is False


def test_path_jail_blocks_absolute_and_newline():
    assert normalize_rel("C:/Windows/system32/cmd.exe") is None
    assert normalize_rel("src/remedy/x.py\n.github/x.yml") is None
    assert scan_paths(["/etc/passwd"]).ok is False


def test_secrets_scan_blocks_token_and_private_key():
    diff = (
        "--- a/src/remedy/core/x.py\n"
        "+++ b/src/remedy/core/x.py\n"
        "+TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz012345'\n"
    )
    r = scan_diff_secrets_and_size(diff)
    assert r.ok is False
    diff2 = (
        "+++ b/src/remedy/core/x.py\n"
        "+-----BEGIN RSA PRIVATE KEY-----\n+MIIE\n"
    )
    assert scan_diff_secrets_and_size(diff2).ok is False


def test_behavior_scan_is_independent_of_path_pass():
    """Pass 2 must catch malice even if the path is allowed."""
    diff = (
        "+++ b/src/remedy/core/harmless.py\n"
        "+eval(base64.b64decode(blob))\n"
    )
    assert scan_paths(["src/remedy/core/harmless.py"]).ok is True
    assert scan_added_behavior(diff).ok is False


def test_both_passes_clean_small_diff():
    diff = (
        "--- a/src/remedy/core/self_inject.py\n"
        "+++ b/src/remedy/core/self_inject.py\n"
        "+# note\n"
    )
    out = run_both_passes(["src/remedy/core/self_inject.py"], diff)
    assert out["ok"] is True
    assert out["passes"]["path_jail"]["ok"] is True
    assert out["passes"]["behavior"]["ok"] is True


def test_fork_size_cap_stricter():
    added = "\n".join(f"+x{i}" for i in range(250))
    diff = "+++ b/src/remedy/core/x.py\n" + added
    assert scan_diff_secrets_and_size(diff, from_fork=False).ok is True
    assert scan_diff_secrets_and_size(diff, from_fork=True).ok is False


def test_submit_always_asks_even_in_auto():
    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("auto")
        banner = approval_required_for_submit(
            "gh issue comment <inbox>",
            "sess-1",
            reason="Post inbox comment",
        )
        assert banner is not None
        assert "APPROVAL_REQUIRED" in banner
        assert TOOL_NAME in banner or "GitHub" in banner
    finally:
        APPROVALS.set_mode(prev)


def test_auto_toggle_does_not_approve_submit_pr():
    q = ApprovalQueue()
    q.set_mode("ask")
    item = q.create(
        tool_name=TOOL_NAME,
        command="gh issue comment <inbox>",
        reason="Post inbox comment",
        session_id="s",
    )
    other = q.create(
        tool_name="bash_exec",
        command="echo hi",
        reason="shell",
        session_id="s",
    )
    q.set_mode("auto")
    assert item.status == "pending"
    assert other.status == "approved"


def test_inbox_comment_is_not_a_branch():
    body = format_inbox_comment(
        {
            "round_id": "abc",
            "summary": "tiny fix",
            "changed": ["src/remedy/core/self_inject.py"],
        },
        "--- a/x\n+++ b/x\n+# note\n",
        {"ok": True},
    )
    assert "abc" in body
    assert "```diff" in body
    assert "not a PR" in body.lower() or "not a branch" in body.lower()
    assert "remedy/self-improve/" not in body


@pytest.mark.asyncio
async def test_submit_refuses_packaged_and_empty(tmp_path):
    out = await submit_self_improve_issue(None, home=tmp_path, repo=tmp_path)
    assert out["ok"] is False
    assert out["error"] == "not_source_checkout"

    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "remedy-ai"\n', encoding="utf-8"
    )
    out2 = await submit_self_improve_issue(None, home=tmp_path, repo=repo)
    assert out2["ok"] is False
    assert out2["error"] in ("not_source_checkout", "no_pending_ship")


@pytest.mark.asyncio
async def test_submit_does_not_create_a_branch(tmp_path):
    from tests.test_self_inject_draft import _git_repo

    repo = _git_repo(tmp_path)
    write_pending_ship(
        tmp_path,
        round_id="abc123",
        summary="fix",
        changed=["src/remedy/core/self_inject.py"],
    )
    (repo / "src" / "remedy" / "core" / "self_inject.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    out = await submit_self_improve_issue(None, home=tmp_path, repo=repo)
    assert out["ok"] is False
    # Stops at write-permission or approval — never git checkout -b
    assert out.get("error") in ("no_repo_write", "approval_required", "local_guard_failed")
    assert not out.get("branch")
    import subprocess

    br = subprocess.run(
        ["git", "-C", str(repo), "branch"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "self-improve" not in (br.stdout or "")


def test_approve_is_bound_to_patch_bytes():
    a = payload_fingerprint(
        {"round_id": "r1", "changed": ["src/remedy/core/a.py"]},
        "+good\n",
    )
    b = payload_fingerprint(
        {"round_id": "r1", "changed": ["src/remedy/core/a.py"]},
        "+evil\n",
    )
    assert a != b


def test_comment_strips_fence_breakout_from_summary():
    body = format_inbox_comment(
        {
            "round_id": "ab`c\n## pwn",
            "summary": "ok\n```\n# hijack",
            "changed": ["src/remedy/core/self_inject.py"],
        },
        "+x\n",
        {"ok": True},
    )
    assert "```\n# hijack" not in body
    assert "\n## pwn" not in body


def test_comment_neutralizes_diff_fence_breakout():
    body = format_inbox_comment(
        {
            "round_id": "r",
            "summary": "x",
            "changed": ["src/remedy/core/self_inject.py"],
        },
        "```\n# not a patch\n```\n+evil",
        {"ok": True},
    )
    assert "```\n# not a patch" not in body


@pytest.mark.asyncio
async def test_submit_rejects_dotdot_pending(tmp_path):
    from tests.test_self_inject_draft import _git_repo

    repo = _git_repo(tmp_path)
    write_pending_ship(
        tmp_path,
        round_id="pwn",
        summary="escape",
        changed=["src/remedy/../../.github/workflows/ci.yml"],
    )
    out = await submit_self_improve_issue(None, home=tmp_path, repo=repo)
    assert out["ok"] is False
    assert out["error"] == "unsafe_pending_path"


@pytest.mark.asyncio
async def test_hijacked_inbox_cache_does_not_comment(tmp_path, monkeypatch):
    """A planted issue number must be verified before gh issue comment."""
    from remedy.core import self_inject_pr as mod
    from tests.test_self_inject_draft import _git_repo

    repo = _git_repo(tmp_path)
    write_pending_ship(
        tmp_path,
        round_id="abc",
        summary="fix",
        changed=["src/remedy/core/self_inject.py"],
    )
    (repo / "src" / "remedy" / "core" / "self_inject.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    from remedy.core.self_inject_pr import write_inbox_state

    write_inbox_state(tmp_path, {"number": 1, "url": "https://github.com/x/y/issues/1"})

    comments: list[list[str]] = []

    async def fake_exec(repo, argv, *, timeout):
        if argv[:3] == ["gh", "repo", "view"]:
            return 0, "ADMIN\n", ""
        if argv[:3] == ["gh", "issue", "view"]:
            num = argv[3]
            if str(num) == "1":
                return (
                    0,
                    '{"title":"Unrelated","state":"OPEN","number":1,"url":"u"}',
                    "",
                )
            if str(num) == "99":
                return (
                    0,
                    '{"title":"Remedy self-improve inbox","state":"OPEN",'
                    '"number":99,"url":"https://github.com/AhmiDarrow/RemedyAI/issues/99"}',
                    "",
                )
            return 1, "", "unknown issue"
        if argv[:3] == ["gh", "issue", "list"]:
            return 0, "[]", ""
        if argv[:3] == ["gh", "issue", "create"]:
            return 0, "https://github.com/AhmiDarrow/RemedyAI/issues/99\n", ""
        if argv[:3] == ["gh", "issue", "comment"]:
            comments.append(list(argv))
            return 0, "ok", ""
        if argv[:3] == ["gh", "label", "create"]:
            return 0, "", ""
        return 1, "", "unexpected " + " ".join(argv[:4])

    monkeypatch.setattr(mod, "_run_exec", fake_exec)
    # Skip ask so we reach inbox verify.
    monkeypatch.setattr(mod, "approval_required_for_submit", lambda *a, **k: None)
    out = await submit_self_improve_issue(None, home=tmp_path, repo=repo)
    # Must not comment on issue 1. Either creates 99 or comments 99 after verify.
    for argv in comments:
        assert "1" not in argv[3:4]
    if out.get("ok"):
        assert out.get("issue") == 99
    else:
        # create parsed 99 then verify must accept title — fake view only
        # handles the hijacked id; create+verify of 99 also hits issue view.
        assert out.get("error") in ("inbox_failed", "comment_failed", "no_repo_write")
