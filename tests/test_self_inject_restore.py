"""self-inject rollback must never destroy work it did not create.

Regression: the idle self-improve loop snapshotted the tree, ran a round, then
on rollback deleted *every* untracked file that appeared since the snapshot.
Files created by the owner or a concurrent session during the round looked
identical to round debris and were silently deleted.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from remedy.core.self_inject import git_capture, git_restore


def _run(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _run(r, "init", "-q")
    _run(r, "config", "user.email", "t@example.com")
    _run(r, "config", "user.name", "T")
    (r / "tracked.py").write_text("original\n", encoding="utf-8")
    _run(r, "add", "-A")
    _run(r, "commit", "-qm", "base")
    return r


def test_untracked_file_survives_when_round_paths_unknown(repo) -> None:
    """The exact bug: no write set → nothing untracked may be deleted."""
    snap = asyncio.run(git_capture(repo))
    # Someone else creates a file while the round is in flight.
    victim = repo / "my_new_work.py"
    victim.write_text("hours of work\n", encoding="utf-8")

    asyncio.run(git_restore(repo, snap))

    assert victim.exists(), "concurrent work was deleted by a rollback"
    assert victim.read_text(encoding="utf-8") == "hours of work\n"


def test_round_debris_is_still_cleaned_when_declared(repo) -> None:
    """Capability preserved: with a write set, the round's own files still go."""
    snap = asyncio.run(git_capture(repo))
    debris = repo / "round_scratch.py"
    debris.write_text("generated\n", encoding="utf-8")
    other = repo / "someone_elses.py"
    other.write_text("keep me\n", encoding="utf-8")

    asyncio.run(git_restore(repo, snap, round_paths=["round_scratch.py"]))

    assert not debris.exists(), "the round's own debris should be cleaned"
    assert other.exists(), "a file outside the write set must be left alone"


def test_preexisting_untracked_file_is_never_touched(repo) -> None:
    keep = repo / "already_here.py"
    keep.write_text("pre-existing\n", encoding="utf-8")
    snap = asyncio.run(git_capture(repo))

    asyncio.run(git_restore(repo, snap, round_paths=["already_here.py"]))

    assert keep.exists(), "a file that predates the round is not round debris"


def test_tracked_changes_roll_back_to_snapshot(repo) -> None:
    """Rollback still does its job on tracked files."""
    snap = asyncio.run(git_capture(repo))
    (repo / "tracked.py").write_text("round edit\n", encoding="utf-8")

    asyncio.run(git_restore(repo, snap))

    assert (repo / "tracked.py").read_text(encoding="utf-8") == "original\n"


def test_preexisting_dirty_work_is_restored(repo) -> None:
    """Dirt that existed before the round comes back, not wiped forever."""
    (repo / "tracked.py").write_text("my uncommitted edit\n", encoding="utf-8")
    snap = asyncio.run(git_capture(repo))
    (repo / "tracked.py").write_text("round stomped it\n", encoding="utf-8")

    asyncio.run(git_restore(repo, snap))

    assert (repo / "tracked.py").read_text(encoding="utf-8") == "my uncommitted edit\n"


def test_live_session_claim_protects_a_file(repo, tmp_path, monkeypatch) -> None:
    """A file a live session holds is never deleted, even if declared debris."""
    import remedy.core.self_inject as SI

    held = repo / "held_by_other_session.py"
    snap = asyncio.run(git_capture(repo))
    held.write_text("another muscle is editing this\n", encoding="utf-8")
    monkeypatch.setattr(
        SI, "_live_claimed_paths", lambda: {str(held.resolve()).replace("\\", "/").lower()}
    )

    asyncio.run(
        git_restore(repo, snap, round_paths=["held_by_other_session.py"])
    )

    assert held.exists(), "a live session's claimed file must survive rollback"


def test_concurrent_tracked_edit_survives_scoped_rollback(repo) -> None:
    """The remaining hole: `reset --hard` wiped tracked edits made during the
    round. A scoped restore must revert only the round's own files."""
    snap = asyncio.run(git_capture(repo))  # clean tree, as the loop requires
    # Round edits its own file...
    (repo / "tracked.py").write_text("round edit\n", encoding="utf-8")
    # ...while the owner edits a different tracked file.
    mine = repo / "owner_file.py"
    mine.write_text("owner work\n", encoding="utf-8")
    _run(repo, "add", "owner_file.py")
    _run(repo, "commit", "-qm", "owner file")
    mine.write_text("owner work IN PROGRESS\n", encoding="utf-8")

    asyncio.run(git_restore(repo, snap, round_paths=["tracked.py"]))

    assert (repo / "tracked.py").read_text(encoding="utf-8") == "original\n"
    assert mine.read_text(encoding="utf-8") == "owner work IN PROGRESS\n"


def test_gate_commands_are_not_over_quoted() -> None:
    """Every gate failed on Windows because quotes reached the tool literally."""
    from pathlib import Path as _P

    from remedy.core.self_inject_draft import _gate_cmds
    from remedy.execution.host.runner import prepare_host_command

    class T:
        test_id = "tests/test_x.py::test_y"

    cmds = _gate_cmds(_P("."), T(), ["src/remedy/core/x.py"])
    for cmd in cmds:
        argv = prepare_host_command(cmd, project_path=_P(".")).argv
        assert not any(a.startswith('"') for a in argv), f"literal quotes in {argv}"
