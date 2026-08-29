"""Per-process stream locks: sibling processes and crashes must not deadlock
the desktop self-inject poller (see remedy/core/stream_lock.py)."""

from __future__ import annotations

import os
import time

from remedy.core.stream_lock import (
    STALE_AFTER_S,
    acquire_stream_lock,
    active_session_ids,
    clear_stale_stream_locks,
    release_stream_lock,
)


def _lock_file(home):
    return home / "locks" / f"stream_active.{os.getpid()}"


def test_acquire_release_owns_one_pid_file(tmp_path):
    acquire_stream_lock(tmp_path, "sid-a")
    assert _lock_file(tmp_path).is_file()
    assert "sid-a" in active_session_ids()
    release_stream_lock(tmp_path, "sid-a")
    assert not _lock_file(tmp_path).exists()
    assert "sid-a" not in active_session_ids()


def test_release_keeps_lock_while_other_session_streams(tmp_path):
    acquire_stream_lock(tmp_path, "sid-a")
    acquire_stream_lock(tmp_path, "sid-b")
    release_stream_lock(tmp_path, "sid-a")
    assert _lock_file(tmp_path).is_file()
    release_stream_lock(tmp_path, "sid-b")
    assert not _lock_file(tmp_path).exists()


def test_release_never_touches_other_process_lock(tmp_path):
    """The gateway-vs-serve race: another process's lock survives our release."""
    locks = tmp_path / "locks"
    locks.mkdir(parents=True)
    other = locks / "stream_active.99999"
    other.write_text("99999", encoding="utf-8")
    acquire_stream_lock(tmp_path, "sid-a")
    release_stream_lock(tmp_path, "sid-a")
    assert other.is_file()
    assert not _lock_file(tmp_path).exists()


def test_clear_stale_removes_legacy_and_old_keeps_fresh(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir(parents=True)
    legacy = locks / "stream_active"
    legacy.write_text("1", encoding="utf-8")
    stale = locks / "stream_active.11111"
    stale.write_text("11111", encoding="utf-8")
    old = time.time() - STALE_AFTER_S - 60
    os.utime(stale, (old, old))
    fresh = locks / "stream_active.22222"
    fresh.write_text("22222", encoding="utf-8")

    clear_stale_stream_locks(tmp_path)

    assert not legacy.exists()
    assert not stale.exists()
    assert fresh.is_file()
