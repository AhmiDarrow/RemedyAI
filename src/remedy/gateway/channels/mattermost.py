"""Mattermost TestClient stub — Go owns WebSocket inbound + REST posts."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MattermostChannel(ChannelAdapter):
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
            "Mattermost TestClient stub (channel=%s); Go owns network",
            self.channel_id,
        )

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not self.bot_token or not self.base_url:
            return True
        return bool(target or self.channel_id)

    async def send_typing(self, target: str | None = None) -> None:
        _ = target
