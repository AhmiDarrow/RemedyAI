"""WhatsApp Cloud API: Graph outbound + webhook inbound (needs public URL)."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
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
                "WhatsApp channel active (phone_number_id=%s, inbound=webhook)",
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

    def verify_webhook_challenge(
        self, mode: str, token: str, challenge: str
    ) -> str | None:
        if mode == "subscribe" and token and token == self.verify_token:
            return challenge
        return None

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        if not self.app_secret:
            return True  # optional
        if not signature_header.startswith("sha256="):
            return False
        expected = signature_header.split("=", 1)[1]
        digest = hmac.new(
            self.app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(digest, expected)

    async def handle_webhook_payload(self, data: dict[str, Any]) -> int:
        """Parse Cloud API webhook; emit messages. Returns count handled."""
        n = 0
        for entry in data.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for msg in value.get("messages") or []:
                    if msg.get("type") != "text":
                        continue
                    text = ((msg.get("text") or {}).get("body") or "").strip()
                    if not text:
                        continue
                    sender = str(msg.get("from") or "")
                    if not is_allowed(
                        allowlist=self._allowed,
                        allow_all=self.allow_all,
                        candidates=[sender],
                        channel="whatsapp",
                    ):
                        continue
                    await emit_message(
                        self.gateway,
                        ChannelKind.WHATSAPP,
                        message=text,
                        chat_id=sender,
                        source_id=sender,
                        username=sender,
                        extra={"user_id": sender},
                    )
                    n += 1
        return n
