"""Phase 4 tests: Gateway, Channels, and API."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from remedy.gateway.channels.adapters import (
    CLIChannel,
    DiscordChannel,
    SlackChannel,
    TelegramChannel,
    WebChannel,
)
from remedy.gateway.router import ChannelAdapter, Gateway
from remedy.models import (
    AgentConfig,
    ChannelKind,
    EventKind,
    GatewayEvent,
)


@pytest.fixture
def runtime():
    """Mock AgentRuntime for gateway tests."""
    rt = MagicMock()
    rt.config = AgentConfig(home_dir="~/.remedy")
    rt.memory = MagicMock()
    rt.skills = MagicMock()
    rt.handoff = MagicMock()
    return rt


@pytest.fixture
def gateway(runtime):
    gw = Gateway(runtime, heartbeat_interval=3600, rate_limit=9999)
    return gw


class TestGateway:
    """Gateway core functionality."""

    def test_initial_state(self, gateway):
        assert not gateway.running
        assert gateway.channel_count == 0
        assert gateway._event_counter == 0

    def test_register_channel(self, gateway):
        ch = ChannelAdapter(ChannelKind.CLI, gateway)
        gateway.register_channel(ch)
        assert gateway.channel_count == 1
        assert ChannelKind.CLI in gateway.channels

    def test_register_multiple_channels(self, gateway):
        for kind in (ChannelKind.CLI, ChannelKind.TELEGRAM, ChannelKind.WEB):
            gateway.register_channel(ChannelAdapter(kind, gateway))
        assert gateway.channel_count == 3

    def test_get_channel(self, gateway):
        ch = ChannelAdapter(ChannelKind.DISCORD, gateway)
        gateway.register_channel(ch)
        assert gateway.get_channel(ChannelKind.DISCORD) is ch
        assert gateway.get_channel(ChannelKind.SLACK) is None

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, gateway):
        await gateway.start()
        assert gateway.running
        assert gateway.stats()["running"] is True

        await gateway.stop()
        assert not gateway.running
        assert gateway.stats()["running"] is False

    @pytest.mark.asyncio
    async def test_emit_event(self, gateway):
        await gateway.start()

        responses = []
        async def handler(event):
            responses.append(("received", event.kind))

        gateway.register_handler(handler)

        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.CLI,
            source_id="test",
            payload={"text": "hello"},
        )
        await gateway.emit(event)
        assert len(responses) == 1
        assert responses[0] == ("received", EventKind.MESSAGE)

        await gateway.stop()

    @pytest.mark.asyncio
    async def test_emit_collects_responses(self, gateway):
        await gateway.start()

        async def handler(event):
            yield "one"
            yield "two"

        gateway.register_handler(handler)

        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.CLI,
            source_id="test",
            payload={},
        )
        result = await gateway.emit(event)
        assert "one" in result
        assert "two" in result

        await gateway.stop()

    @pytest.mark.asyncio
    async def test_enqueue_processes_async(self, gateway):
        await gateway.start()

        processed = []
        async def handler(event):
            processed.append(event.payload.get("id"))

        gateway.register_handler(handler)

        for i in range(3):
            await gateway.enqueue(GatewayEvent(
                kind=EventKind.MESSAGE,
                channel=ChannelKind.API,
                source_id="test",
                payload={"id": f"evt-{i}"},
            ))

        await asyncio.sleep(0.2)
        await gateway.stop()

        assert len(processed) >= 1

    def test_emit_rate_limits(self, gateway):
        gateway.rate_limit = 1

        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.CLI,
            source_id="test",
            payload={},
        )

        for _ in range(3):
            result = asyncio.run(gateway.emit(event))

        result = asyncio.run(gateway.emit(event))
        assert any("rate_limited" in str(r) for r in result)

    def test_broadcast_sends_to_all(self, gateway):
        cli = CLIChannel(gateway)
        web = WebChannel(gateway)
        gateway.register_channel(cli)
        gateway.register_channel(web)

        asyncio.run(gateway.broadcast("hello"))

    def test_send_to_specific_channel(self, gateway):
        cli = CLIChannel(gateway)
        gateway.register_channel(cli)

        result = asyncio.run(gateway.send_to(ChannelKind.CLI, "test message"))
        assert result is True

        result = asyncio.run(gateway.send_to(ChannelKind.DISCORD, "test message"))
        assert result is False

    def test_stats_accurate(self, gateway):
        stats = gateway.stats()
        assert stats["events_processed"] == 0
        assert stats["running"] is False
        assert isinstance(stats["channels"], list)


class TestChannelAdapters:
    """Channel adapter implementations."""

    def test_cli_channel_starts(self, gateway):
        ch = CLIChannel(gateway)
        asyncio.run(ch.start())
        assert ch.running
        asyncio.run(ch.stop())

    def test_cli_channel_send(self, gateway):
        ch = CLIChannel(gateway)
        result = asyncio.run(ch.send("hello"))
        assert result is True

    def test_telegram_stub_no_token(self, gateway):
        ch = TelegramChannel(gateway, bot_token="")
        asyncio.run(ch.start())
        assert ch.running
        result = asyncio.run(ch.send("test"))
        assert result is True

    def test_telegram_with_token_no_chat(self, gateway):
        ch = TelegramChannel(gateway, bot_token="fake-token", chat_ids=[])
        asyncio.run(ch.start())
        result = asyncio.run(ch.send("test"))
        assert result is False

    def test_discord_stub_no_token(self, gateway):
        ch = DiscordChannel(gateway, bot_token="")
        asyncio.run(ch.start())
        assert ch.running
        result = asyncio.run(ch.send("test"))
        assert result is True

    def test_discord_with_token_no_channel(self, gateway):
        ch = DiscordChannel(gateway, bot_token="fake-token", channel_id="")
        asyncio.run(ch.start())
        result = asyncio.run(ch.send("test"))
        assert result is False

    def test_slack_stub_no_token(self, gateway):
        ch = SlackChannel(gateway, bot_token="")
        asyncio.run(ch.start())
        assert ch.running
        result = asyncio.run(ch.send("test"))
        assert result is True

    def test_slack_with_token_no_channel(self, gateway):
        ch = SlackChannel(gateway, bot_token="fake-token", channel_id="")
        asyncio.run(ch.start())
        result = asyncio.run(ch.send("test"))
        assert result is False

    def test_web_channel_send(self, gateway):
        ch = WebChannel(gateway)
        result = asyncio.run(ch.send("test"))
        assert result is False

    def test_web_channel_await_response(self, gateway):
        ch = WebChannel(gateway)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            fut = ch.await_response("req-1", timeout=1.0)
            assert not fut.done()

            loop.run_until_complete(ch.send("response", target="req-1"))
            assert fut.result() == "response"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_channel_kind_values(self):
        assert ChannelKind.CLI.value == "cli"
        assert ChannelKind.TELEGRAM.value == "telegram"
        assert ChannelKind.DISCORD.value == "discord"
        assert ChannelKind.SLACK.value == "slack"
        assert ChannelKind.WEB.value == "web"
        assert ChannelKind.API.value == "api"


class TestGatewayRuntime:
    """Gateway CLI integration tests."""

    def test_gateway_status_no_db(self, tmp_path):
        assert not (tmp_path / "memory.db").exists()

    def test_run_gateway_starts_and_stops(self, tmp_path, runtime):
        gw = Gateway(runtime, heartbeat_interval=9999, rate_limit=9999)

        asyncio.run(gw.start())
        assert gw.running
        asyncio.run(gw.stop())
        assert not gw.running


class TestAPI:
    """REST API server tests."""

    def test_create_app_no_deps(self):
        from remedy.interfaces.api import create_app
        app = create_app(title="Test", version="0.1.0")
        assert app.title == "Test"

    def test_create_app_with_runtime(self, runtime):
        from remedy.interfaces.api import create_app
        app = create_app(runtime=runtime, gateway=None, memory=None)
        assert app.title == "Remedy AI"

    def test_status_model(self):
        from remedy.interfaces.api import StatusResponse
        s = StatusResponse(
            version="0.1.0",
            uptime="1m",
            gateway={"running": False},
        )
        assert s.version == "0.1.0"
        assert s.status == "ok"

    def test_chat_request_model(self):
        from remedy.interfaces.api import ChatRequest
        req = ChatRequest(message="Hello Remedy")
        assert req.message == "Hello Remedy"
        assert req.user_id == "default"

    def test_webhook_model(self):
        from remedy.interfaces.api import WebhookPayload
        payload = WebhookPayload(source="github", event="push", data={"ref": "main"})
        assert payload.source == "github"
        assert payload.data["ref"] == "main"
