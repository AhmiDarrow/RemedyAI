"""Python messenger inbound gate — production always refuses dual-poll."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.gateway.poll_lock import python_may_poll_messengers


def test_python_may_poll_default_false(monkeypatch):
    """Production: Python must not take poll locks (no dual getUpdates with Go)."""
    monkeypatch.delenv("REMEDY_PYTHON_MESSENGER_POLL", raising=False)
    monkeypatch.delenv("REMEDY_TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert python_may_poll_messengers() is False


def test_python_may_poll_flag_alone_refused_outside_pytest(monkeypatch):
    """REMEDY_PYTHON_MESSENGER_POLL=1 without a test marker must not dual-poll."""
    monkeypatch.setenv("REMEDY_PYTHON_MESSENGER_POLL", "1")
    monkeypatch.delenv("REMEDY_TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert python_may_poll_messengers() is False


def test_python_may_poll_always_false_even_under_pytest(monkeypatch):
    """Inbound implementations were removed — flag + pytest still refuses."""
    monkeypatch.setenv("REMEDY_PYTHON_MESSENGER_POLL", "1")
    monkeypatch.setenv("REMEDY_TESTING", "1")
    assert python_may_poll_messengers() is False


@pytest.mark.asyncio
async def test_telegram_start_is_outbound_only(tmp_path: Path, monkeypatch):
    """Default cutover: Python must not schedule getUpdates or lock-retry."""
    monkeypatch.delenv("REMEDY_PYTHON_MESSENGER_POLL", raising=False)
    from remedy.gateway.channels.telegram import TelegramChannel

    class _GW:
        pass

    ch = TelegramChannel(
        _GW(),
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        home_dir=str(tmp_path),
    )
    await ch.start()
    assert not hasattr(ch, "_poll_task") or getattr(ch, "_poll_task", None) is None
    assert not hasattr(ch, "_lock_retry_task") or getattr(ch, "_lock_retry_task", None) is None
    await ch.stop()


@pytest.mark.asyncio
async def test_discord_start_is_outbound_only(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REMEDY_PYTHON_MESSENGER_POLL", raising=False)
    from remedy.gateway.channels.discord import DiscordChannel

    class _GW:
        pass

    ch = DiscordChannel(_GW(), bot_token="tok", home_dir=str(tmp_path))
    await ch.start()
    assert not hasattr(ch, "_ws_task") or getattr(ch, "_ws_task", None) is None
    await ch.stop()
