"""Desktop → messenger outbound mirror for origin_channel sessions."""

from __future__ import annotations

import pytest

from remedy.gateway.session_bridge import mirror_desktop_reply_to_messenger
from remedy.models import ChannelKind, ChatSession


class _FakeGateway:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_to(self, kind, message, target=None):
        self.sent.append((kind, message, target))
        return True


@pytest.mark.asyncio
async def test_mirror_telegram_session():
    gw = _FakeGateway()
    session = ChatSession(
        id="msg:telegram:99",
        title="Telegram",
        origin_channel="telegram",
        external_chat_id="99",
    )
    ok = await mirror_desktop_reply_to_messenger(gw, session, "hello from desktop")
    assert ok is True
    assert len(gw.sent) == 1
    assert gw.sent[0][0] == ChannelKind.TELEGRAM
    assert gw.sent[0][1] == "hello from desktop"
    assert gw.sent[0][2] == "99"


@pytest.mark.asyncio
async def test_mirror_skips_plain_desktop_session():
    gw = _FakeGateway()
    session = ChatSession(id="local-1", title="Chat")
    ok = await mirror_desktop_reply_to_messenger(gw, session, "nope")
    assert ok is False
    assert gw.sent == []


@pytest.mark.asyncio
async def test_mirror_chunks_long_discord():
    gw = _FakeGateway()
    session = ChatSession(
        id="msg:discord:1",
        title="D",
        origin_channel="discord",
        external_chat_id="chan",
    )
    long = "x" * 4500
    ok = await mirror_desktop_reply_to_messenger(gw, session, long)
    assert ok is True
    assert len(gw.sent) >= 2
    assert all(s[0] == ChannelKind.DISCORD for s in gw.sent)
