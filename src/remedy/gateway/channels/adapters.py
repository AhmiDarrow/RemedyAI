"""Channel adapters for Remedy's multi-channel gateway.

Each adapter bridges an external platform (Telegram, Discord, CLI, Web/API)
into the gateway's event model. Adapters handle:
- Inbound: receive messages → emit GatewayEvent
- Outbound: send responses back through the channel

Stub adapters for services requiring API keys (Telegram, Discord).
Full implementations can be swapped in for production use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from remedy.models import ChannelKind
from remedy.gateway.router import ChannelAdapter

logger = logging.getLogger(__name__)


class CLIChannel(ChannelAdapter):
    """Real-time interactive CLI channel.

    Reads stdin line-by-line and sends responses to stdout via the console.
    """

    def __init__(self, gateway, *, prompt: str = "remedy> ") -> None:
        super().__init__(ChannelKind.CLI, gateway)
        self.prompt = prompt
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await super().start()
        logger.info("CLI channel active (prompt: %r)", self.prompt)

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        print(f"\n{message}")
        return True

    async def read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """Read a single line from stdin (async-compatible wrapper)."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, input, self.prompt)
        except (EOFError, KeyboardInterrupt):
            return None


class TelegramChannel(ChannelAdapter):
    """Telegram bot channel adapter (stub).

    Requires a bot token (TELEGRAM_BOT_TOKEN). Uses long-polling
    to receive messages and sends replies via the Telegram Bot API.
    """

    def __init__(self, gateway, *, bot_token: str = "", chat_ids: Optional[list[str]] = None) -> None:
        super().__init__(ChannelKind.TELEGRAM, gateway)
        self.bot_token = bot_token
        self.chat_ids: list[str] = chat_ids or []
        self._poll_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info("Telegram channel active (chats=%d)", len(self.chat_ids))
        else:
            logger.info("Telegram channel: stub mode (no token)")

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        if not self.bot_token:
            logger.debug("Telegram stub: %s", message[:50])
            return True

        import aiohttp
        chat_id = target or (self.chat_ids[0] if self.chat_ids else None)
        if chat_id is None:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={
                    "chat_id": chat_id,
                    "text": message[:4096],
                    "parse_mode": "Markdown",
                }) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Telegram send failed: %s", e)
                return False


class DiscordChannel(ChannelAdapter):
    """Discord bot channel adapter (stub).

    Requires a bot token (DISCORD_BOT_TOKEN) and optionally a guild/channel ID.
    """

    def __init__(self, gateway, *, bot_token: str = "", channel_id: str = "") -> None:
        super().__init__(ChannelKind.DISCORD, gateway)
        self.bot_token = bot_token
        self.channel_id = channel_id
        self._ws_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info("Discord channel active (channel=%s)", self.channel_id)
        else:
            logger.info("Discord channel: stub mode (no token)")

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        if not self.bot_token:
            logger.debug("Discord stub: %s", message[:50])
            return True

        ch_id = target or self.channel_id
        if not ch_id:
            return False

        import aiohttp
        url = f"https://discord.com/api/v10/channels/{ch_id}/messages"
        headers = {"Authorization": f"Bot {self.bot_token}"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json={"content": message[:2000]}) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Discord send failed: %s", e)
                return False


class SlackChannel(ChannelAdapter):
    """Slack channel adapter (stub).

    Requires a bot token (SLACK_BOT_TOKEN) and a channel ID.
    """

    def __init__(self, gateway, *, bot_token: str = "", channel_id: str = "") -> None:
        super().__init__(ChannelKind.SLACK, gateway)
        self.bot_token = bot_token
        self.channel_id = channel_id

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info("Slack channel active (channel=%s)", self.channel_id)
        else:
            logger.info("Slack channel: stub mode (no token)")

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        if not self.bot_token:
            logger.debug("Slack stub: %s", message[:50])
            return True

        import aiohttp
        ch_id = target or self.channel_id
        if not ch_id:
            return False

        url = "https://slack.com/api/chat.postMessage"
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json={
                    "channel": ch_id,
                    "text": message[:3000],
                }) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Slack send failed: %s", e)
                return False


class WebChannel(ChannelAdapter):
    """REST API / web channel adapter.

    Used by the FastAPI server to relay HTTP requests as gateway events.
    Messages are queued for async processing and responses are returned
    via the API response mechanism.
    """

    def __init__(self, gateway) -> None:
        super().__init__(ChannelKind.WEB, gateway)
        self._pending_responses: dict[str, asyncio.Future] = {}

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        if target and target in self._pending_responses:
            fut = self._pending_responses.pop(target)
            if not fut.done():
                fut.set_result(message)
            return True
        return False

    def await_response(self, request_id: str, timeout: float = 30.0) -> asyncio.Future:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_responses[request_id] = fut
        return fut
