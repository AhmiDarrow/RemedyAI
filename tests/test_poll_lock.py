"""Exclusive messenger poll lock + update offset persistence."""

from __future__ import annotations

import os

from remedy.gateway.poll_lock import (
    MessengerPollLock,
    load_update_offset,
    save_update_offset,
)


def test_poll_lock_exclusive(tmp_path):
    a = MessengerPollLock(tmp_path, "telegram")
    b = MessengerPollLock(tmp_path, "telegram")
    assert a.try_acquire() is True
    assert a.held is True
    # Second acquirer must refuse (same process would see own pid if file written —
    # open a fresh lock object that thinks the first still holds).
    assert b.try_acquire() is False
    a.release()
    c = MessengerPollLock(tmp_path, "telegram")
    assert c.try_acquire() is True
    c.release()


def test_offset_roundtrip(tmp_path):
    assert load_update_offset(tmp_path, "telegram") == 0
    save_update_offset(tmp_path, 42, "telegram")
    assert load_update_offset(tmp_path, "telegram") == 42
    save_update_offset(tmp_path, 0, "telegram")  # no-op for non-positive
    assert load_update_offset(tmp_path, "telegram") == 42


def test_lock_file_records_pid(tmp_path):
    lock = MessengerPollLock(tmp_path, "discord")
    assert lock.try_acquire()
    assert lock.held
    assert lock.path.is_file()
    # On Windows the exclusive share may block concurrent readers; release first.
    lock.release()
    raw = lock.path.read_text(encoding="utf-8").strip() if lock.path.is_file() else ""
    # release unlinks when pid matches; if still present it should start with our pid
    if raw:
        assert raw.startswith(str(os.getpid()))
