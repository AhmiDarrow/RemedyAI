"""Exclusive messenger poll lock + update offset persistence."""

from __future__ import annotations

import os
import time

import pytest

from remedy.gateway.poll_lock import (
    MessengerPollLock,
    _parse_lock_payload,
    _pid_alive,
    load_update_offset,
    save_update_offset,
)


def test_pid_alive_self():
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def test_parse_lock_payload():
    assert _parse_lock_payload("123 456.0\n") == (123, 456.0)
    assert _parse_lock_payload("99") == (99, 0.0)
    assert _parse_lock_payload("") is None


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


def test_leftover_live_pid_file_without_flock_is_reclaimed(tmp_path, monkeypatch):
    """Restart reclaim: leftover file, PID looks live, but no OS lock → take over.

    Windows STILL_ACTIVE / PID reuse used to return False here and leave the
    new serve silent. The OS exclusive lock is the source of truth.
    """
    path = tmp_path / "locks" / "telegram_getupdates.lock"
    path.parent.mkdir(parents=True)
    path.write_text(f"1 {time.time() - 500:.0f}\n", encoding="utf-8")
    monkeypatch.setattr(
        "remedy.gateway.poll_lock._pid_alive",
        lambda pid: pid == 1,
    )
    lock = MessengerPollLock(tmp_path, "telegram")
    assert lock.try_acquire() is True
    assert lock.reclaimed is True
    lock.release()


def test_live_holder_with_stale_heartbeat_is_not_stolen(tmp_path, monkeypatch):
    """A live owner (OS lock held) must not be displaced — no dual getUpdates."""
    a = MessengerPollLock(tmp_path, "telegram")
    assert a.try_acquire() is True
    monkeypatch.setattr("remedy.gateway.poll_lock._pid_alive", lambda pid: True)
    b = MessengerPollLock(tmp_path, "telegram")
    assert b.try_acquire() is False
    a.release()


def test_dead_pid_lock_is_reclaimed(tmp_path, monkeypatch):
    path = tmp_path / "locks" / "telegram_getupdates.lock"
    path.parent.mkdir(parents=True)
    path.write_text(f"1 {time.time() - 500:.0f}\n", encoding="utf-8")
    monkeypatch.setattr("remedy.gateway.poll_lock._pid_alive", lambda pid: False)
    lock = MessengerPollLock(tmp_path, "telegram")
    assert lock.try_acquire() is True
    lock.release()


def test_poll_lock_mkdir_fail_closed(tmp_path, monkeypatch):
    lock = MessengerPollLock(tmp_path, "telegram")
    monkeypatch.setattr(
        "pathlib.Path.mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("denied")),
    )
    assert lock.try_acquire() is False


def test_inprocess_dual_acquire_refused(tmp_path):
    """Second MessengerPollLock in the same process must not dual-poll."""
    a = MessengerPollLock(tmp_path, "telegram")
    b = MessengerPollLock(tmp_path, "telegram")
    assert a.try_acquire() is True
    assert b.try_acquire() is False
    a.release()
    assert b.try_acquire() is True
    b.release()


def test_discord_second_instance_does_not_start_gateway(tmp_path):
    import asyncio

    from remedy.gateway.channels.discord import DiscordChannel

    class _GW:
        async def emit(self, event):
            return None

    async def _run():
        a = DiscordChannel(_GW(), bot_token="tok", home_dir=str(tmp_path))
        b = DiscordChannel(_GW(), bot_token="tok", home_dir=str(tmp_path))
        await a.start()
        try:
            assert a._ws_task is not None
            await b.start()
            try:
                assert b._ws_task is None
                assert b._lock_retry_task is not None
            finally:
                await b.stop()
        finally:
            await a.stop()

    asyncio.run(_run())


def test_try_acquire_idempotent_when_held(tmp_path):
    lock = MessengerPollLock(tmp_path, "discord")
    assert lock.try_acquire() is True
    assert lock.try_acquire() is True  # same object re-entry
    lock.release()


def test_reclaim_keeps_update_offset(tmp_path, monkeypatch):
    """Reclaiming the poll lock must not drop the persisted getUpdates offset."""
    save_update_offset(tmp_path, 497067471, "telegram")
    path = tmp_path / "locks" / "telegram_getupdates.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1 {time.time() - 50:.0f}\n", encoding="utf-8")
    monkeypatch.setattr("remedy.gateway.poll_lock._pid_alive", lambda pid: False)
    lock = MessengerPollLock(tmp_path, "telegram")
    assert lock.try_acquire() is True
    assert lock.reclaimed is True
    assert load_update_offset(tmp_path, "telegram") == 497067471
    lock.release()


def test_same_pid_leftover_file_is_acquired(tmp_path):
    """Crash in this PID that left a file (no flock) must not lock us out."""
    path = tmp_path / "locks" / "telegram_getupdates.lock"
    path.parent.mkdir(parents=True)
    path.write_text(f"{os.getpid()} {time.time() - 10:.0f}\n", encoding="utf-8")
    lock = MessengerPollLock(tmp_path, "telegram")
    assert lock.try_acquire() is True
    lock.release()


def test_dead_pid_sets_reclaimed(tmp_path, monkeypatch):
    path = tmp_path / "locks" / "telegram_getupdates.lock"
    path.parent.mkdir(parents=True)
    path.write_text("999999 1\n", encoding="utf-8")
    monkeypatch.setattr("remedy.gateway.poll_lock._pid_alive", lambda pid: False)
    lock = MessengerPollLock(tmp_path, "telegram")
    assert lock.try_acquire() is True
    assert lock.reclaimed is True
    lock.release()


def test_telegram_409_backoff_insists_after_start():
    from remedy.gateway.channels.telegram import telegram_409_backoff

    wait, insist = telegram_409_backoff(10.0, 9.0)  # 1s after start
    assert insist is True
    assert 2.0 <= wait < 4.0
    wait, insist = telegram_409_backoff(200.0, 10.0)  # well after window
    assert insist is False
    assert wait == 25.0
    wait, insist = telegram_409_backoff(5.0, 0.0)  # never started
    assert insist is False
    assert wait == 25.0


@pytest.mark.asyncio
async def test_matrix_sync_defers_when_lock_held(tmp_path, monkeypatch):
    """Two Matrix /sync pollers race the since cursor and drop or duplicate rooms."""
    import asyncio

    from remedy.gateway.channels.matrix import MatrixChannel
    from remedy.gateway.poll_lock import MessengerPollLock

    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(MatrixChannel, "_sync_loop", idle)
    held = MessengerPollLock(tmp_path, "matrix")
    assert held.try_acquire() is True
    ch = MatrixChannel(
        object(),
        access_token="tok",
        homeserver="https://example.invalid",
        home_dir=str(tmp_path),
    )
    ch._running = True
    assert await ch._try_start_sync() is False
    assert ch._sync_task is None
    held.release()
    assert await ch._try_start_sync() is True
    assert ch._sync_task is not None
    await ch.stop()


@pytest.mark.asyncio
async def test_mattermost_socket_defers_when_lock_held(tmp_path, monkeypatch):
    import asyncio

    from remedy.gateway.channels.mattermost import MattermostChannel
    from remedy.gateway.poll_lock import MessengerPollLock

    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(MattermostChannel, "_ws_loop", idle)
    held = MessengerPollLock(tmp_path, "mattermost")
    assert held.try_acquire() is True
    ch = MattermostChannel(
        object(),
        bot_token="tok",
        base_url="https://mm.example.invalid",
        home_dir=str(tmp_path),
    )
    ch._running = True
    assert await ch._try_start_socket() is False
    assert ch._ws_task is None
    held.release()
    assert await ch._try_start_socket() is True
    assert ch._ws_task is not None
    await ch.stop()


@pytest.mark.asyncio
async def test_signal_receive_defers_when_lock_held(tmp_path, monkeypatch):
    """Two Signal receive pollers race signal-cli envelopes and drop unread DMs."""
    import asyncio

    from remedy.gateway.channels.signal_cli import SignalChannel
    from remedy.gateway.poll_lock import MessengerPollLock

    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(SignalChannel, "_receive_loop", idle)
    held = MessengerPollLock(tmp_path, "signal")
    assert held.try_acquire() is True
    ch = SignalChannel(
        object(),
        cli_path="signal-cli",
        account="+15550100",
        home_dir=str(tmp_path),
    )
    ch._running = True
    assert await ch._try_start_receive() is False
    assert ch._poll_task is None
    held.release()
    assert await ch._try_start_receive() is True
    assert ch._poll_task is not None
    await ch.stop()

@pytest.mark.asyncio
async def test_signal_second_instance_does_not_start_receive(tmp_path, monkeypatch):
    """A second serve must not call signal-cli receive (that steals unread envelopes)."""
    import asyncio

    from remedy.gateway.channels.signal_cli import SignalChannel

    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(SignalChannel, "_receive_loop", idle)
    binary = tmp_path / "signal-cli"
    binary.write_text("", encoding="utf-8")

    class _GW:
        async def emit(self, event):
            return None

    a = SignalChannel(
        _GW(),
        cli_path=str(binary),
        account="+15550100",
        home_dir=str(tmp_path),
    )
    b = SignalChannel(
        _GW(),
        cli_path=str(binary),
        account="+15550100",
        home_dir=str(tmp_path),
    )
    await a.start()
    try:
        assert a._poll_task is not None
        await b.start()
        try:
            assert b._poll_task is None
            assert b._lock_retry_task is not None
        finally:
            await b.stop()
    finally:
        await a.stop()
