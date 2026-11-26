"""Self-improve PR submit + inbound security bot (two-pass guard)."""

from __future__ import annotations

import pytest

from remedy.core.self_inject_guard import (
    collect_git_diff,
    normalize_rel,
    run_both_passes,
    scan_added_behavior,
    scan_diff_secrets_and_size,
    scan_paths,
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


def test_collect_git_diff_fails_closed_when_git_errors(tmp_path, monkeypatch):
    def failed_git(*_args, **_kwargs):
        return 128, "", "invalid revision"

    monkeypatch.setattr("remedy.execution.env.run_unattended_git", failed_git)
    with pytest.raises(RuntimeError, match="git file-list failed"):
        collect_git_diff(tmp_path, base="missing-base")


def test_fork_size_cap_stricter():
    added = "\n".join(f"+x{i}" for i in range(250))
    diff = "+++ b/src/remedy/core/x.py\n" + added
    assert scan_diff_secrets_and_size(diff, from_fork=False).ok is True
    assert scan_diff_secrets_and_size(diff, from_fork=True).ok is False


