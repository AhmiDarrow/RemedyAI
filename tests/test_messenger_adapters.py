"""Slim unit tests for multi-messenger outbound adapters (no live network)."""

from __future__ import annotations

import pytest

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.discord import DiscordChannel
from remedy.gateway.channels.google_chat import GoogleChatChannel
from remedy.gateway.channels.matrix import MatrixChannel
from remedy.gateway.channels.mattermost import MattermostChannel
from remedy.gateway.channels.signal_cli import SignalChannel
from remedy.gateway.channels.slack import SlackChannel
from remedy.gateway.channels.teams import TeamsChannel
from remedy.gateway.channels.whatsapp import WhatsAppChannel
from remedy.gateway.messengers import get_messenger, list_messenger_definitions
from remedy.models import ChannelKind


class _GW:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def test_catalog_ready_channels():
    ready = {m.id for m in list_messenger_definitions() if m.status == "ready"}
    for mid in ("telegram", "discord", "slack", "mattermost", "matrix"):
        assert mid in ready
        m = get_messenger(mid)
        assert m and m.inbound and m.outbound


def test_allowlist_secure_default():
    assert not is_allowed(allowlist=frozenset(), allow_all=False, candidates=["1"])
    assert is_allowed(allowlist=frozenset({"1"}), allow_all=False, candidates=["1", "2"])
    assert is_allowed(allowlist=frozenset(), allow_all=True, candidates=["x"])


def test_parse_ids():
    assert parse_ids("a, b ;c") == frozenset({"a", "b", "c"})


@pytest.mark.asyncio
async def test_discord_stub_send():
    gw = _GW()
    ch = DiscordChannel(gw, bot_token="")
    await ch.start()
    assert await ch.send("hi") is True
    await ch.stop()


@pytest.mark.asyncio
async def test_slack_and_whatsapp_stub_send():
    gw = _GW()
    sl = SlackChannel(gw, bot_token="")
    await sl.start()
    assert await sl.send("x") is True
    await sl.stop()

    wa = WhatsAppChannel(gw, verify_token="secret")
    await wa.start()
    assert await wa.send("hi", target="15551234567") is True
    await wa.stop()


@pytest.mark.asyncio
async def test_mattermost_matrix_stub_start():
    gw = _GW()
    for ch in (
        MattermostChannel(gw, bot_token="", base_url=""),
        MatrixChannel(gw, access_token="", homeserver=""),
    ):
        assert not ch.running
        await ch.start()
        assert ch.running, f"{ch.kind} did not come up"
        assert await ch.send("hello") is True
        await ch.stop()
        assert not ch.running, f"{ch.kind} did not come back down"
        await ch.stop()
        assert not ch.running


def test_channel_kinds_exist():
    for v in (
        "discord",
        "slack",
        "mattermost",
        "matrix",
        "whatsapp",
        "teams",
        "google_chat",
        "signal",
    ):
        assert ChannelKind(v).value == v


@pytest.mark.asyncio
async def test_teams_and_google_chat_stub_start():
    gw = _GW()
    teams = TeamsChannel(gw, app_id="id", app_password="pw", allow_all=True)
    await teams.start()
    # No conversation reference yet — outbound fails closed without crashing.
    assert await teams.send("hi") is False
    await teams.stop()

    gchat = GoogleChatChannel(gw, access_token="", allow_all=True)
    await gchat.start()
    assert await gchat.send("hi") is True
    await gchat.stop()


@pytest.mark.asyncio
async def test_signal_stub_without_cli():
    gw = _GW()
    sig = SignalChannel(gw, cli_path="signal-cli-not-installed", account="")
    await sig.start()
    assert await sig.send("x", target="+100") is False
    await sig.stop()


def test_catalog_all_have_fields():
    for m in list_messenger_definitions():
        assert m.id and m.name
        assert m.status in ("ready", "partial", "planned")
        assert m.fields
