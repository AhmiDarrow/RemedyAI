"""Google Chat spaces.messages outbound — webhook inbound owned by Go httpapi."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

API = "https://chat.googleapis.com/v1"


class GoogleChatChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        access_token: str = "",
        space_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.GOOGLE_CHAT, gateway)
        self.access_token = (access_token or "").strip()
        self.space_id = str(space_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.space_id:
            self._allowed = self._allowed | frozenset({self.space_id})
        self.allow_all = bool(allow_all)

    async def start(self) -> None:
        await super().start()
        if self.access_token:
            logger.info(
                "Google Chat outbound-ready (space=%s; "
                "Go remedy-runtime owns webhook inbound)",
                self.space_id or "(any)",
            )
        else:
            logger.info("Google Chat channel: stub mode (no access_token)")

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    def _space_name(self, space: str) -> str:
        s = (space or self.space_id or "").strip()
        if s and not s.startswith("spaces/"):
            s = f"spaces/{s}"
        return s

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.access_token:
            return True
        space = self._space_name(target or self.space_id)
        if not space:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{API}/{space}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={"text": (message or "")[:4096]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Google Chat send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        return
