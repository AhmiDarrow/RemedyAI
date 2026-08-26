"""Self-inject loop controller tests (ledger + git snapshot/rollback helpers)."""

from __future__ import annotations

import json

import pytest

from remedy.core.self_inject import (
    SelfInjectRound,
    activity_snapshot,
    append_ledger,
    is_enabled,
    last_tick_path,
    ledger_path,
    note_user_activity,
    read_last_tick,
    read_ledger,
    request_sidecar_restart,
    should_run_now,
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


def test_request_sidecar_restart_skipped_when_frozen(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ok = request_sidecar_restart(home=tmp_path)
    assert ok is False
    assert not (tmp_path / "locks" / "self_inject_apply").exists()


def test_request_sidecar_restart_dev_desktop_still_requests(tmp_path, monkeypatch):
    """Dev-checkout Desktop sets the sidecar env vars but is not frozen —
    its serve runs this checkout, so the restart request must be written."""
    monkeypatch.setenv("REMEDY_DESKTOP_SIDECAR", "1")
    ok = request_sidecar_restart(home=tmp_path)
    assert ok is True
    assert (tmp_path / "locks" / "self_inject_apply").exists()


def test_self_inject_rounds_endpoint_reports_live_state(tmp_path, monkeypatch):
    """Ledger surface: live vs awaiting-restart vs not-loaded, newest first."""
    from fastapi.testclient import TestClient

    from remedy.core.self_inject import SelfInjectRound, append_ledger
    from remedy.interfaces.api import create_app

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))

    live = SelfInjectRound(status="applied", tree="python", summary="old edit")
    live.finished_utc = "2000-01-01T00:00:00+00:00"
    live.detail["sidecar_restart_requested"] = True
    append_ledger(live, tmp_path)

    pending = SelfInjectRound(status="applied", tree="python", summary="fresh edit")
    pending.finished_utc = "2999-01-01T00:00:00+00:00"
    pending.detail["sidecar_restart_requested"] = True
    append_ledger(pending, tmp_path)

    frozen_skip = SelfInjectRound(status="applied", tree="python", summary="frozen edit")
    frozen_skip.finished_utc = "2999-01-01T00:00:00+00:00"
    frozen_skip.detail["sidecar_restart_requested"] = False
    append_ledger(frozen_skip, tmp_path)

    red = SelfInjectRound(status="rolled_back", tree="python", summary="bad edit")
    red.finished_utc = "2999-01-01T00:00:00+00:00"
    append_ledger(red, tmp_path)

    client = TestClient(create_app())
    data = client.get("/api/self-inject/rounds").json()
    by_id = {r["round_id"]: r for r in data["rounds"]}
    assert by_id[live.round_id]["live_state"] == "live"
    assert by_id[pending.round_id]["live_state"] == "awaiting_restart"
    assert by_id[frozen_skip.round_id]["live_state"] == "not_loaded"
    assert by_id[red.round_id]["live_state"] == ""
    # Newest first, diff stripped from the payload.
    assert data["rounds"][0]["round_id"] == red.round_id
    assert all("diff" not in r for r in data["rounds"])


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
    # The round declares what IT wrote; anything else untracked is someone
    # else's work and is never deleted (see tests/test_self_inject_restore.py).
    err = await git_restore(repo, snap, round_paths=["round_noise.txt"])
    assert err == "" or "error" not in err.lower()
    text = tracked.read_text(encoding="utf-8")
    assert "owner-wip" in text
    assert "round-bad" not in text
    assert not (repo / "round_noise.txt").exists()


def _reset_clock(*, started: float | None = None) -> None:
    import remedy.core.self_inject as si

    now = started if started is not None else __import__("time").time()
    si._last_user_activity = 0.0
    si._process_started = now
    si._last_unattended_code = 0.0


def test_idle_clock_ignores_debug_log_mtime(tmp_path, monkeypatch):
    """Status-bar / health writes to debug.log must not reset idle."""
    import time

    import remedy.core.self_inject as si

    _reset_clock(started=time.time() - 120)
    before = si._idle_seconds()
    log = tmp_path / "debug.log"
    log.write_text("status ping\n", encoding="utf-8")
    # Touching a log is what the old clock used — new clock must ignore it.
    log.write_text("status ping\nagain\n", encoding="utf-8")
    after = si._idle_seconds()
    assert after >= before - 0.5
    assert after >= 100


def test_note_user_activity_resets_idle(monkeypatch):
    import time

    import remedy.core.self_inject as si

    _reset_clock(started=time.time() - 400)
    assert si._idle_seconds() >= 300
    note_user_activity()
    assert si._idle_seconds() < 2


def test_should_run_now_force_bypasses_idle(monkeypatch, tmp_path):
    import time

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    _reset_clock(started=time.time())
    monkeypatch.delenv("REMEDY_SELF_INJECT_FORCE", raising=False)
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    # Fresh process: not idle yet
    assert should_run_now() is False
    monkeypatch.setenv("REMEDY_SELF_INJECT_FORCE", "1")
    assert should_run_now() is True


def test_should_run_now_after_user_idle(monkeypatch, tmp_path):
    import time

    import remedy.core.self_inject as si

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.delenv("REMEDY_SELF_INJECT_FORCE", raising=False)
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    _reset_clock(started=time.time() - 400)
    assert should_run_now() is True
    note_user_activity()
    assert should_run_now() is False
    si._last_user_activity = time.time() - 400
    assert should_run_now() is True


def test_is_enabled_respects_off_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_SELF_INJECT", "0")
    assert is_enabled() is False
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    assert is_enabled() is True


def test_is_enabled_packaged_default_off(monkeypatch, tmp_path):
    """Packaged / desktop sidecar must not self-inject unless explicitly opted in."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.delenv("REMEDY_SELF_INJECT", raising=False)
    monkeypatch.setenv("REMEDY_DESKTOP_SIDECAR", "1")
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {},
    )
    assert is_enabled() is False
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    assert is_enabled() is True
    monkeypatch.delenv("REMEDY_SELF_INJECT", raising=False)
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {"self_inject": {"enabled": True}},
    )
    assert is_enabled() is True
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {"self_inject": {"enabled": False}},
    )
    assert is_enabled() is False


def test_is_enabled_source_checkout_default_on(monkeypatch, tmp_path):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.delenv("REMEDY_SELF_INJECT", raising=False)
    monkeypatch.delenv("REMEDY_DESKTOP_SIDECAR", raising=False)
    monkeypatch.delenv("REMEDY_DESKTOP", raising=False)
    import sys

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {},
    )
    assert is_enabled() is True


def test_activity_snapshot_includes_last_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    monkeypatch.delenv("REMEDY_SELF_INJECT_FORCE", raising=False)
    _reset_clock()
    snap = activity_snapshot(tmp_path)
    assert snap["enabled"] is True
    assert "idle_s" in snap
    assert snap["last_tick"] is None
    last_tick_path(tmp_path).write_text(
        '{"kind":"unattended","ts":"2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    snap2 = activity_snapshot(tmp_path)
    assert snap2["last_tick"]["kind"] == "unattended"


@pytest.mark.asyncio
async def test_unattended_improve_fires_without_user_prompt(tmp_path, monkeypatch):
    """Organism tick runs with no chat message and records last_tick."""
    from remedy.core.self_inject import run_unattended_improve

    monkeypatch.delenv("REMEDY_SELF_INJECT_FORCE", raising=False)
    monkeypatch.setenv("REMEDY_SELF_INJECT", "1")
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    _reset_clock()

    calls: list[str] = []

    class _Loop:
        def tick_learned_skills(self):
            calls.append("tick")
            return [{"name": "demo", "action": "promote", "from": "validated", "to": "active"}]

    class _Rt:
        home_dir = tmp_path
        learning_loop = _Loop()

        def _get_learning_loop(self):
            return self.learning_loop

    # No pyproject in tmp_path → code path skipped; organism still runs.
    result = await run_unattended_improve(_Rt(), home=tmp_path, repo=tmp_path)
    assert result["kind"] == "unattended"
    assert result["organism"]["skills_refined"] == 1
    assert calls == ["tick"]
    saved = read_last_tick(tmp_path)
    assert saved is not None
    assert saved["organism"]["skills_refined"] == 1
    # Fresh process is not idle, so no ruff attempt
    assert (result.get("code") or {}).get("skipped") == "not_idle"


@pytest.mark.asyncio
async def test_ruff_self_heal_skips_dirty_tree(tmp_path):
    import subprocess

    from remedy.core.self_inject import _maybe_ruff_self_heal

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
    (repo / "keep.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "keep.txt").write_text("base\ndirty\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "remedy-ai"\n', encoding="utf-8")
    (repo / "src" / "remedy").mkdir(parents=True)
    out = await _maybe_ruff_self_heal(repo, home=tmp_path)
    assert out is None
