"""Discord REST outbound — Gateway WS inbound owned by Go."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

API = "https://discord.com/api/v10"


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
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.DISCORD, gateway)
        self.bot_token = (bot_token or "").strip()
        self.channel_id = str(channel_id or "").strip()
        self.guild_id = str(guild_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Discord channel: stub mode (no token)")
            return
        logger.info(
            "Discord outbound-ready (default_channel=%s; "
            "Go remedy-runtime owns inbound gateway)",
            self.channel_id,
        )

    async def stop(self) -> None:
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
