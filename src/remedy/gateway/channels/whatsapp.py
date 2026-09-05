"""WhatsApp TestClient stub — Go httpapi owns webhook inbound + Graph send."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class WhatsAppChannel(ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        access_token: str = "",
        phone_number_id: str = "",
        verify_token: str = "",
        app_secret: str = "",
        allow_from: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.WHATSAPP, gateway)
        self.access_token = (access_token or "").strip()
        self.phone_number_id = str(phone_number_id or "").strip()
        self.verify_token = (verify_token or "").strip()
        self.app_secret = (app_secret or "").strip()
        self._allowed = parse_ids(allow_from)
        self.allow_all = bool(allow_all)

    async def start(self) -> None:
        await super().start()
        if self.access_token and self.phone_number_id:
            logger.info(
                "WhatsApp TestClient stub (phone_number_id=%s); Go owns network",
                self.phone_number_id,
            )
        else:
            logger.info("WhatsApp channel: stub mode (missing token or phone_number_id)")

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not self.access_token or not self.phone_number_id:
            return True
        to = (target or "").lstrip("+")
        return bool(to)

    async def send_typing(self, target: str | None = None) -> None:
        _ = target
