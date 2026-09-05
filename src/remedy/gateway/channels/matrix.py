"""Matrix TestClient stub — Go owns /sync inbound + room send."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MatrixChannel(ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        access_token: str = "",
        homeserver: str = "",
        user_id: str = "",
        room_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.MATRIX, gateway)
        self._home_dir = home_dir
        self.access_token = (access_token or "").strip()
        self.homeserver = (homeserver or "").rstrip("/")
        self.user_id = str(user_id or "").strip()
        self.room_id = str(room_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.room_id:
            self._allowed = self._allowed | frozenset({self.room_id})
        self.allow_all = bool(allow_all)

    async def start(self) -> None:
        await super().start()
        if not (self.access_token and self.homeserver):
            logger.info("Matrix channel: stub mode (missing token or homeserver)")
            return
        logger.info(
            "Matrix TestClient stub (room=%s); Go owns network",
            self.room_id,
        )

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not self.access_token or not self.homeserver:
            return True
        return bool(target or self.room_id)

    async def send_typing(self, target: str | None = None) -> None:
        _ = target
