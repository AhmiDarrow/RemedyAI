"""Discord: Gateway WS inbound + REST outbound (desktop-friendly single shard)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import zlib

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


class DiscordChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        channel_id: str = "",
        guild_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.DISCORD, gateway)
        self.bot_token = (bot_token or "").strip()
        self.channel_id = str(channel_id or "").strip()
        self.guild_id = str(guild_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._ws_task: asyncio.Task | None = None
        self._seq: int | None = None
        self._heartbeat_ms = 41250
        self._heartbeat_task: asyncio.Task | None = None
        self._session_id: str | None = None
        self._typing_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Discord channel: stub mode (no token)")
            return
        logger.info("Discord channel active (default_channel=%s)", self.channel_id)
        self._ws_task = asyncio.create_task(self._gateway_loop())

    async def stop(self) -> None:
        for t in self._typing_tasks:
            t.cancel()
        self._typing_tasks.clear()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token:
            return True
        ch_id = target or self.channel_id
        if not ch_id:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{API}/channels/{ch_id}/messages",
                headers={"Authorization": f"Bot {self.bot_token}"},
                json={"content": (message or "")[:2000]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Discord send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        ch_id = target or self.channel_id
        if not self.bot_token or not ch_id:
            return
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{API}/channels/{ch_id}/typing",
                headers={"Authorization": f"Bot {self.bot_token}"},
            ) as resp:
                _ = resp.status
        except Exception:
            pass

    async def _gateway_loop(self) -> None:
        import aiohttp

        while self._running:
            try:
                session = await self.ensure_http()
                async with session.ws_connect(GATEWAY_URL, heartbeat=None) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._on_payload(ws, json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            raw = zlib.decompress(msg.data)
                            await self._on_payload(ws, json.loads(raw))
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Discord gateway error")
                await asyncio.sleep(3.0)

    async def _on_payload(self, ws, data: dict) -> None:
        op = data.get("op")
        t = data.get("t")
        s = data.get("s")
        if s is not None:
            self._seq = s
        d = data.get("d") or {}

        if op == 10:  # Hello
            self._heartbeat_ms = int(d.get("heartbeat_interval") or 41250)
            await ws.send_json(
                {
                    "op": 2,
                    "d": {
                        "token": self.bot_token,
                        "intents": INTENTS,
                        "properties": {
                            "os": "windows",
                            "browser": "remedy",
                            "device": "remedy",
                        },
                    },
                }
            )
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat(ws))
        elif op == 0 and t == "MESSAGE_CREATE":
            await self._on_message(d)
        elif op == 0 and t == "READY":
            self._session_id = d.get("session_id")
            logger.info("Discord gateway READY")

    async def _heartbeat(self, ws) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._heartbeat_ms / 1000.0)
                await ws.send_json({"op": 1, "d": self._seq})
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _on_message(self, d: dict) -> None:
        if d.get("author", {}).get("bot"):
            return
        content = (d.get("content") or "").strip()
        if not content:
            return
        ch_id = str(d.get("channel_id") or "")
        author = d.get("author") or {}
        user_id = str(author.get("id") or "")
        guild_id = str(d.get("guild_id") or "")
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[ch_id, user_id, guild_id],
            channel="discord",
        ):
            return
        task = asyncio.create_task(self.send_typing(ch_id))
        self._typing_tasks.add(task)
        task.add_done_callback(self._typing_tasks.discard)
        await emit_message(
            self.gateway,
            ChannelKind.DISCORD,
            message=content,
            chat_id=ch_id,
            source_id=user_id or ch_id,
            username=author.get("username"),
            extra={"user_id": user_id, "guild_id": guild_id},
        )
