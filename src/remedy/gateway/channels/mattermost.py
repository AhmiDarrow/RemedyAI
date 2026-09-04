"""Mattermost REST outbound — WebSocket inbound owned by Go."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MattermostChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        base_url: str = "",
        channel_id: str = "",
        team_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.MATTERMOST, gateway)
        self.bot_token = (bot_token or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.channel_id = str(channel_id or "").strip()
        self.team_id = str(team_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir

    async def start(self) -> None:
        await super().start()
        if not (self.bot_token and self.base_url):
            logger.info("Mattermost channel: stub mode (missing token or base_url)")
            return
        logger.info(
            "Mattermost outbound-ready (channel=%s; Go remedy-runtime owns inbound)",
            self.channel_id,
        )

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token or not self.base_url:
            return True
        ch_id = target or self.channel_id
        if not ch_id:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{self.base_url}/api/v4/posts",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel_id": ch_id, "message": (message or "")[:4000]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Mattermost send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        return
