"""Unattended draft loop + client-update policy (origin wins, no merge)."""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from remedy.core.self_inject import note_user_activity
from remedy.core.self_inject_draft import (
    DraftTarget,
    _jail_violation,
    client_update_policy,
    infer_source_from_test,
    internal_improve_context,
    internal_improve_shell_ok,
    is_source_checkout,
    origin_wins_if_dirty,
    pick_draft_target,
    read_pending_ship,
    record_red,
    red_blocked,
    write_pending_ship,
)


def _git_repo(tmp_path, *, remedy: bool = True):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if remedy:
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "remedy-ai"\n', encoding="utf-8"
        )
        core = repo / "src" / "remedy" / "core"
        core.mkdir(parents=True)
        (core / "self_inject.py").write_text("x = 1\n", encoding="utf-8")
        tests = repo / "tests"
        tests.mkdir()
        (tests / "test_self_inject.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    return repo


def test_packaged_install_is_not_source(tmp_path):
    inst = tmp_path / "site-packages" / "remedy"
    inst.mkdir(parents=True)
    (inst / "__init__.py").write_text("", encoding="utf-8")
    assert is_source_checkout(inst) is False
    pol = client_update_policy(inst)
    assert pol["mode"] == "replace"
    assert pol["self_improve_code"] is False
    assert pol["on_conflict"] == "origin_wins"
    assert pol["ship_from_idle"] is False


def test_source_checkout_may_draft(tmp_path):
    repo = _git_repo(tmp_path)
    assert is_source_checkout(repo) is True
    pol = client_update_policy(repo)
    assert pol["mode"] == "source_ship"
    assert pol["self_improve_code"] is True


def test_origin_wins_on_dirty_source(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "src" / "remedy" / "core" / "self_inject.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    decision = origin_wins_if_dirty(repo)
    assert decision["action"] == "abort_dirty"
    assert decision["merge"] is False


def test_origin_wins_clean_source_may_pull(tmp_path):
    repo = _git_repo(tmp_path)
    decision = origin_wins_if_dirty(repo)
    assert decision["action"] == "pull"
    assert decision["merge"] is False


def test_packaged_update_is_replace(tmp_path):
    assert origin_wins_if_dirty(tmp_path / "nope")["action"] == "replace"


def test_infer_source_from_test(tmp_path):
    repo = _git_repo(tmp_path)
    assert (
        infer_source_from_test(repo, "tests/test_self_inject.py::test_ok")
        == "src/remedy/core/self_inject.py"
    )


def test_pick_lastfailed(tmp_path, monkeypatch):
    # Speculative pickers are opt-in now: faults Remedy actually hit are the
    # default trigger (a stale lastfailed cache once targeted a network flake).
    monkeypatch.setenv("REMEDY_SELF_INJECT_SPECULATIVE", "1")
    repo = _git_repo(tmp_path)
    cache = repo / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(
        json.dumps({"tests/test_self_inject.py::test_ok": True}),
        encoding="utf-8",
    )
    tgt = pick_draft_target(repo, home=tmp_path)
    assert tgt is not None
    assert tgt.kind == "pytest_lastfailed"
    assert tgt.path == "src/remedy/core/self_inject.py"
    assert "src/remedy/core/self_inject.py" in tgt.allowed


def test_pick_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_SELF_INJECT_SPECULATIVE", "1")
    repo = _git_repo(tmp_path)
    log = tmp_path / "debug.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        f'  File "{repo / "src" / "remedy" / "core" / "self_inject.py"}", line 9, in x\n'
        "    boom\nNameError: boom\n",
        encoding="utf-8",
    )
    tgt = pick_draft_target(repo, home=tmp_path)
    assert tgt is not None
    assert tgt.kind == "traceback"
    assert tgt.path == "src/remedy/core/self_inject.py"


def test_no_evidence_returns_none(tmp_path):
    repo = _git_repo(tmp_path)
    assert pick_draft_target(repo, home=tmp_path) is None


def test_jail_rejects_extra_files():
    err = _jail_violation(
        ["src/remedy/core/self_inject.py", "src/remedy/interfaces/api.py"],
        ["src/remedy/core/self_inject.py"],
    )
    assert err and "outside_allowed" in err
    assert _jail_violation(["src/remedy/core/self_inject.py"], ["src/remedy/core/self_inject.py"]) is None


def test_red_cooldown(tmp_path):
    key = "pytest_lastfailed:src/remedy/core/self_inject.py:tests/x.py::t"
    assert red_blocked(tmp_path, key) is False
    record_red(tmp_path, key)
    assert red_blocked(tmp_path, key) is True
    assert red_blocked(tmp_path, "other") is False


def test_pending_ship_does_not_claim_shipped(tmp_path):
    write_pending_ship(
        tmp_path,
        round_id="abc",
        summary="fix",
        changed=["src/remedy/core/self_inject.py"],
    )
    pending = read_pending_ship(tmp_path)
    assert pending is not None
    assert pending["ship"] is False
    assert "Local only" in pending["note"]


def test_internal_turn_does_not_reset_idle():
    import remedy.core.self_inject as si

    si._last_user_activity = time.time() - 400
    before = si._idle_seconds()
    with internal_improve_context():
        note_user_activity()
    assert si._idle_seconds() >= before - 1
    note_user_activity()
    assert si._idle_seconds() < 2


def test_internal_shell_allowlist():
    assert internal_improve_shell_ok("uv run pytest -q tests/test_self_inject.py")
    assert internal_improve_shell_ok("uv run ruff check src/remedy/core/self_inject.py")
    assert internal_improve_shell_ok("python -m py_compile src/remedy/core/x.py")
    assert not internal_improve_shell_ok("git push origin master")
    assert not internal_improve_shell_ok("gh release create v1")
    assert not internal_improve_shell_ok("uv publish")
    # Adversarial: prefix match + chaining used to be enough to run anything.
    assert not internal_improve_shell_ok("python -c \"import os; os.system('git push')\"")
    assert not internal_improve_shell_ok("uv run pytest && git push origin master")
    assert not internal_improve_shell_ok("uv run pytest; gh release create v1")
    assert not internal_improve_shell_ok("pytest | python -c 'evil()'")
    assert not internal_improve_shell_ok("uv run pytest > out.txt")


@pytest.mark.asyncio
async def test_draft_skips_dirty_and_packaged(tmp_path):
    from remedy.core.self_inject_draft import run_unattended_draft

    packaged = tmp_path / "pkg"
    packaged.mkdir()
    out = await run_unattended_draft(None, repo=packaged, home=tmp_path)
    assert out["skipped"] == "not_source_checkout"

    repo = _git_repo(tmp_path)
    (repo / "src" / "remedy" / "core" / "self_inject.py").write_text(
        "x = 9\n", encoding="utf-8"
    )
    out2 = await run_unattended_draft(None, repo=repo, home=tmp_path)
    assert out2["skipped"] == "dirty_tree"


@pytest.mark.asyncio
async def test_draft_skips_when_any_stream_claimed(tmp_path):
    """Any stream claim (including __self_improve__) must skip drafts."""
    from remedy.core.self_inject_draft import run_unattended_draft
    from remedy.core.turn_context import (
        release_session_stream_claim,
        try_claim_session_stream,
    )

    repo = _git_repo(tmp_path)
    sid = "__self_improve__"
    assert try_claim_session_stream(sid) is True
    try:
        out = await run_unattended_draft(None, repo=repo, home=tmp_path)
        assert out.get("skipped") == "user_streaming"
    finally:
        release_session_stream_claim(sid)


@pytest.mark.asyncio
async def test_draft_jail_rolls_back(tmp_path):
    from remedy.core.self_inject_draft import run_unattended_draft

    repo = _git_repo(tmp_path)
    target = DraftTarget(
        kind="traceback",
        path="src/remedy/core/self_inject.py",
        evidence="boom",
        allowed=["src/remedy/core/self_inject.py"],
        why="test",
    )

    class _Rt:
        _llm_api_key = "sk-test"
        _max_react_steps = 64
        _streaming_sessions: set[str] = set()

        async def stream_response(self, *a, **k):
            # A rogue draft writing outside its jail. In production every draft
            # write goes through file_write, which records the path for exact
            # attribution — mirror that here so the jail check sees it as the
            # round's own work rather than a concurrent edit.
            from remedy.core.self_inject_draft import note_internal_write

            sneak = repo / "tests" / "test_self_inject.py"
            sneak.write_text("def test_ok():\n    assert False\n", encoding="utf-8")
            note_internal_write("tests/test_self_inject.py")
            if False:
                yield ""

    out = await run_unattended_draft(_Rt(), repo=repo, home=tmp_path, target=target)
    assert out.get("outcome") == "rolled_back"
    assert "outside_allowed" in (out.get("reason") or "")
    text = (repo / "tests" / "test_self_inject.py").read_text(encoding="utf-8")
    assert "assert True" in text
    assert "assert False" not in text
