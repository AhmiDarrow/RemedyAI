"""Single-instance serve lock."""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces.instance_lock import (
    lock_path,
    release_serve_lock,
    try_acquire_serve_lock,
)


def test_serve_lock_exclusive(tmp_path: Path, monkeypatch):
    # Isolate lock file under tmp_path
    home = tmp_path / "remedy-home"
    home.mkdir()
    ok1, msg1 = try_acquire_serve_lock(home)
    assert ok1 is True, msg1
    assert lock_path(home).is_file()

    # Simulate another process: lock file still held by open fh in this process
    # Second acquire in same process is allowed (same pid).
    ok_same, _ = try_acquire_serve_lock(home)
    assert ok_same is True

    release_serve_lock()
    # After release, file should be gone or reclaimable
    ok2, msg2 = try_acquire_serve_lock(home)
    assert ok2 is True, msg2
    release_serve_lock()


def test_stale_lock_reclaimed(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    p = lock_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Dead pid
    p.write_text("99999999 1.0\n", encoding="utf-8")
    ok, msg = try_acquire_serve_lock(home)
    assert ok is True, msg
    release_serve_lock()
