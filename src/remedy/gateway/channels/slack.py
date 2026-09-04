"""Slack chat.postMessage outbound — Socket Mode inbound owned by Go."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class SlackChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        app_token: str = "",
        channel_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.SLACK, gateway)
        self.bot_token = (bot_token or "").strip()
        self.app_token = (app_token or "").strip()
        self.channel_id = str(channel_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Slack channel: stub mode (no bot token)")
            return
        logger.info(
            "Slack outbound-ready (channel=%s; Go remedy-runtime owns Socket Mode)",
            self.channel_id,
        )

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token:
            return True
        ch = target or self.channel_id
        if not ch:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": ch, "text": (message or "")[:3000]},
            ) as resp:
                data = await resp.json()
                return bool(data.get("ok"))
        except Exception as e:
            logger.error("Slack send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        ch = target or self.channel_id
        if not self.bot_token or not ch:
            return
        try:
            session = await self.ensure_http()
            async with session.post(
                "https://slack.com/api/conversations.mark",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": ch},
            ) as resp:
                _ = resp.status
        except Exception:
            pass
