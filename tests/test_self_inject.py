"""Self-inject loop controller tests (ledger + git snapshot/rollback helpers)."""

from __future__ import annotations

import json

import pytest

from remedy.core.self_inject import (
    SelfInjectRound,
    append_ledger,
    ledger_path,
    read_ledger,
    request_sidecar_restart,
)


def test_ledger_roundtrip(tmp_path):
    r = SelfInjectRound(tree="python", status="applied", outcome="applied")
    r.gate_cmds = ["pytest -q"]
    r.gate_exit_codes["pytest -q"] = 0
    r.summary = "ok"
    r.detail["head"] = "abc123"
    path = append_ledger(r, home=tmp_path)
    assert path.exists()
    assert path.name == "self_inject_ledger.jsonl"
    assert ledger_path(tmp_path) == path

    rows = read_ledger(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["round_id"] == r.round_id
    assert row["tree"] == "python"
    assert row["outcome"] == "applied"
    assert row["gate_exit_codes"] == {"pytest -q": 0}
    assert row["detail"]["head"] == "abc123"


def test_ledger_append_multiple_keeps_order(tmp_path):
    a = SelfInjectRound(status="green", tree="python")
    b = SelfInjectRound(status="red", tree="desktop", outcome="rolled_back")
    append_ledger(a, home=tmp_path)
    append_ledger(b, home=tmp_path)
    rows = read_ledger(tmp_path)
    assert [r["status"] for r in rows] == ["green", "red"]
    assert rows[0]["round_id"] == a.round_id


def test_read_ledger_missing_file(tmp_path):
    assert read_ledger(tmp_path) == []


def test_ledger_preserves_timestamps():
    r = SelfInjectRound()
    assert r.started_utc
    assert r.finished_utc == ""


def test_request_sidecar_restart_writes_rollback_payload(tmp_path):
    snapshot = {
        "head": "deadbeef",
        "changed": ["src/remedy/core/x.py"],
        "untracked": ["src/remedy/core/new.py"],
        "diff": "",
    }
    ok = request_sidecar_restart(
        home=tmp_path,
        repo="C:/repo",
        snapshot=snapshot,
        round_id="abc123",
    )
    assert ok is True
    marker = tmp_path / "locks" / "self_inject_apply"
    assert marker.exists()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["kind"] == "sidecar_restart"
    assert payload["round_id"] == "abc123"
    assert payload["repo"] == "C:/repo"
    assert payload["head"] == "deadbeef"
    assert payload["changed"] == ["src/remedy/core/x.py"]
    assert payload["untracked"] == ["src/remedy/core/new.py"]


def test_request_sidecar_restart_no_snapshot(tmp_path):
    ok = request_sidecar_restart(home=tmp_path)
    assert ok is True
    payload = json.loads(
        (tmp_path / "locks" / "self_inject_apply").read_text(encoding="utf-8")
    )
    assert payload["changed"] == []
    assert payload["untracked"] == []
    assert payload["head"] == ""


@pytest.mark.asyncio
async def test_git_restore_preserves_pre_round_dirty(tmp_path):
    """Rollback must re-apply the snapshot diff (not wipe unrelated dirt)."""
    import subprocess

    from remedy.core.self_inject import git_capture, git_restore

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
    tracked = repo / "keep.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Pre-round unrelated dirty work
    tracked.write_text("base\nowner-wip\n", encoding="utf-8")
    snap = await git_capture(repo)
    assert "owner-wip" in snap["diff"]
    # Round mutates further + adds noise untracked
    tracked.write_text("base\nowner-wip\nround-bad\n", encoding="utf-8")
    (repo / "round_noise.txt").write_text("tmp\n", encoding="utf-8")
    err = await git_restore(repo, snap)
    assert err == "" or "error" not in err.lower()
    text = tracked.read_text(encoding="utf-8")
    assert "owner-wip" in text
    assert "round-bad" not in text
    assert not (repo / "round_noise.txt").exists()
