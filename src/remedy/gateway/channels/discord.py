"""Discord bot channel — outbound REST (inbound Gateway WS planned)."""

from __future__ import annotations

import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class DiscordChannel(ChannelAdapter):
    """Discord bot — outbound REST only for now."""

    def __init__(self, gateway, *, bot_token: str = "", channel_id: str = "") -> None:
        super().__init__(ChannelKind.DISCORD, gateway)
        self.bot_token = bot_token
        self.channel_id = channel_id

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
                async with session.post(
                    url, headers=headers, json={"content": message[:2000]}
                ) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Discord send failed: %s", e)
                return False
