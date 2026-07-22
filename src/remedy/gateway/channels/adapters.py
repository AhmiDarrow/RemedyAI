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
import json
import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind, EventKind, GatewayEvent

logger = logging.getLogger(__name__)


class CLIChannel(ChannelAdapter):
    """Real-time interactive CLI channel.

    Reads stdin line-by-line and sends responses to stdout via the console.
    """

    def __init__(self, gateway, *, prompt: str = "remedy> ") -> None:
        super().__init__(ChannelKind.CLI, gateway)
        self.prompt = prompt
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        logger.info("CLI channel active (prompt: %r)", self.prompt)

    async def send(self, message: str, target: str | None = None) -> bool:
        print(f"\n{message}")
        return True

    async def read_line(self, timeout: float | None = None) -> str | None:
        """Read a single line from stdin (async-compatible wrapper).

        When ``timeout`` is set, returns ``None`` if no line arrives in time
        (does not cancel the underlying read — next call may get a stale line
        on some platforms; acceptable for the gateway poll loop).
        """
        try:
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, input, self.prompt)
            if timeout is None:
                return await fut
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            except TimeoutError:
                return None
        except (EOFError, KeyboardInterrupt):
            return None


class TelegramChannel(ChannelAdapter):
    """Telegram bot channel adapter with long-poll inbound + send outbound.

    Requires a bot token (TELEGRAM_BOT_TOKEN). When started with a token,
    long-polls getUpdates and emits GatewayEvents for text messages.
    """

    def __init__(self, gateway, *, bot_token: str = "", chat_ids: list[str] | None = None) -> None:
        super().__init__(ChannelKind.TELEGRAM, gateway)
        self.bot_token = bot_token
        self.chat_ids: list[str] = [str(c) for c in (chat_ids or [])]
        self._poll_task: asyncio.Task | None = None
        self._last_update_id: int = 0

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info("Telegram channel active (chats=%d)", len(self.chat_ids))
            self._poll_task = asyncio.create_task(self._poll_loop())
        else:
            logger.info("Telegram channel: stub mode (no token)")

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
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
                }) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Telegram send failed: %s", e)
                return False

    async def _poll_loop(self) -> None:
        """Long-poll Telegram getUpdates until the channel stops."""
        while self._running:
            try:
                updates = await self._get_updates(timeout=25)
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram poll error")
                await asyncio.sleep(2.0)

    async def _get_updates(self, timeout: int = 25) -> list[dict]:
        import aiohttp
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {
            "timeout": timeout,
            "offset": self._last_update_id + 1 if self._last_update_id else 0,
            "allowed_updates": json.dumps(["message", "edited_message"]),
        }
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout + 10),
            ) as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                logger.warning("Telegram getUpdates %s: %s", resp.status, text[:200])
                await asyncio.sleep(1.0)
                return []
            data = await resp.json()
            if not data.get("ok"):
                return []
            return list(data.get("result") or [])

    async def _handle_update(self, update: dict) -> None:
        uid = update.get("update_id")
        if isinstance(uid, int):
            self._last_update_id = max(self._last_update_id, uid)

        msg = update.get("message") or update.get("edited_message") or {}
        text = msg.get("text")
        if not text:
            return

        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if self.chat_ids and chat_id not in self.chat_ids:
            return

        from_user = msg.get("from") or {}
        source_id = str(from_user.get("id") or chat_id)
        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.TELEGRAM,
            source_id=source_id,
            session_id=chat_id or None,
            payload={
                "message": text,
                "chat_id": chat_id,
                "username": from_user.get("username"),
            },
            raw=str(update)[:2000],
        )
        # Outbound replies go through gateway handlers → send_to / channel.send
        await self.gateway.emit(event)


class DiscordChannel(ChannelAdapter):
    """Discord bot channel — outbound REST only.

    Inbound Gateway WebSocket is not implemented yet. Outbound send works
    when DISCORD_BOT_TOKEN is set.
    """

    def __init__(self, gateway, *, bot_token: str = "", channel_id: str = "") -> None:
        super().__init__(ChannelKind.DISCORD, gateway)
        self.bot_token = bot_token
        self.channel_id = channel_id
        self._ws_task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info("Discord channel active (channel=%s)", self.channel_id)
        else:
            logger.info("Discord channel: stub mode (no token)")

    async def send(self, message: str, target: str | None = None) -> bool:
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
    """Slack bot channel — outbound Web API only.

    Inbound Events API / Socket Mode is not implemented yet. Outbound
    chat.postMessage works when SLACK_BOT_TOKEN is set.
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

    async def send(self, message: str, target: str | None = None) -> bool:
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

    async def send(self, message: str, target: str | None = None) -> bool:
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
