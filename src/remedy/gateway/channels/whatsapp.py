"""WhatsApp Cloud API Graph outbound — webhook inbound owned by Go httpapi."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v19.0"


class WhatsAppChannel(HttpSessionMixin, ChannelAdapter):
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
                "WhatsApp outbound-ready (phone_number_id=%s; "
                "Go remedy-runtime owns webhook inbound)",
                self.phone_number_id,
            )
        else:
            logger.info("WhatsApp channel: stub mode (missing token or phone_number_id)")

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.access_token or not self.phone_number_id:
            return True
        to = (target or "").lstrip("+")
        if not to:
            return False
        try:
            session = await self.ensure_http()
            url = f"{GRAPH}/{self.phone_number_id}/messages"
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": (message or "")[:4096]},
                },
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("WhatsApp send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        return
