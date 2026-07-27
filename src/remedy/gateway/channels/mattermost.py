"""Mattermost bot — outbound REST create-post (inbound planned)."""

from __future__ import annotations

import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MattermostChannel(ChannelAdapter):
    """Mattermost bot via REST API v4 posts."""

    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        base_url: str = "",
        channel_id: str = "",
    ) -> None:
        super().__init__(ChannelKind.MATTERMOST, gateway)
        self.bot_token = bot_token
        self.base_url = (base_url or "").rstrip("/")
        self.channel_id = channel_id

    async def start(self) -> None:
        await super().start()
        if self.bot_token and self.base_url:
            logger.info("Mattermost channel active (channel=%s)", self.channel_id)
        else:
            logger.info("Mattermost channel: stub mode (missing token or base_url)")

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token or not self.base_url:
            logger.debug("Mattermost stub: %s", message[:50])
            return True
        ch_id = target or self.channel_id
        if not ch_id:
            return False
        import aiohttp

        url = f"{self.base_url}/api/v4/posts"
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    url,
                    headers=headers,
                    json={"channel_id": ch_id, "message": message[:4000]},
                ) as resp:
                    return resp.status in (200, 201)
            except Exception as e:
                logger.error("Mattermost send failed: %s", e)
                return False
