"""Telegram TestClient stub — Go ``native/go/gateway`` owns send + long-poll."""

from __future__ import annotations

import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class TelegramChannel(ChannelAdapter):
    """Catalog/TestClient stub. Production outbound+inbound is Go remedy-runtime."""

    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        chat_ids: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.TELEGRAM, gateway)
        self.bot_token = (bot_token or "").strip()
        self.chat_ids: list[str] = [str(c).strip() for c in (chat_ids or []) if str(c).strip()]
        self._allowed = frozenset(self.chat_ids)
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Telegram channel: stub mode (no token)")
            return
        logger.info(
            "Telegram TestClient stub (allowlist=%d); Go remedy-runtime owns network",
            len(self.chat_ids),
        )

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not self.bot_token:
            return True
        chat_id = target or (self.chat_ids[0] if self.chat_ids else None)
        return chat_id is not None

    async def send_typing(self, chat_id: str) -> None:
        _ = chat_id
