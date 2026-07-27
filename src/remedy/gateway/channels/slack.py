"""Slack bot channel — outbound chat.postMessage (Socket Mode planned)."""

from __future__ import annotations

import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class SlackChannel(ChannelAdapter):
    """Slack bot — outbound Web API only for now."""

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
                async with session.post(
                    url,
                    headers=headers,
                    json={"channel": ch_id, "text": message[:3000]},
                ) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Slack send failed: %s", e)
                return False
