"""Microsoft Teams Bot Framework connector outbound — webhook inbound owned by Go."""

from __future__ import annotations

import logging
import time

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class TeamsChannel(HttpSessionMixin, ChannelAdapter):
    """Outbound uses Bot Framework connector. Inbound JWT/webhook is Go-owned."""

    def __init__(
        self,
        gateway,
        *,
        app_id: str = "",
        app_password: str = "",
        tenant_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.TEAMS, gateway)
        self.app_id = (app_id or "").strip()
        self.app_password = (app_password or "").strip()
        self.tenant_id = (tenant_id or "").strip() or "botframework.com"
        self._allowed = parse_ids(allow_ids)
        self.allow_all = bool(allow_all)
        self._token: str = ""
        self._token_exp: float = 0.0
        # Conversation reference for replies (set by tests or callers if needed).
        self._last_service_url: str = ""
        self._last_conversation_id: str = ""

    async def start(self) -> None:
        await super().start()
        if self.app_id and self.app_password:
            logger.info(
                "Teams outbound-ready (Go remedy-runtime owns webhook inbound)"
            )
        else:
            logger.info("Teams channel: stub mode (missing app_id/password)")

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def _bearer(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        session = await self.ensure_http()
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.app_id,
            "client_secret": self.app_password,
            "scope": "https://api.botframework.com/.default",
        }
        async with session.post(url, data=data) as resp:
            body = await resp.json()
        tok = str(body.get("access_token") or "")
        if not tok:
            logger.warning("Teams token failed: %s", str(body)[:160])
            return ""
        self._token = tok
        self._token_exp = now + float(body.get("expires_in") or 3600)
        return tok

    async def send(self, message: str, target: str | None = None) -> bool:
        """Send to conversation id (target) or last conversation reference."""
        if not (self.app_id and self.app_password):
            return True
        conv = (target or self._last_conversation_id or "").strip()
        service = self._last_service_url.rstrip("/")
        if not conv or not service:
            logger.warning("Teams send: no conversation reference yet")
            return False
        token = await self._bearer()
        if not token:
            return False
        try:
            session = await self.ensure_http()
            url = f"{service}/v3/conversations/{conv}/activities"
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"type": "message", "text": (message or "")[:28000]},
            ) as resp:
                return resp.status in (200, 201, 202)
        except Exception as e:
            logger.error("Teams send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        conv = (target or self._last_conversation_id or "").strip()
        service = self._last_service_url.rstrip("/")
        if not conv or not service:
            return
        token = await self._bearer()
        if not token:
            return
        try:
            session = await self.ensure_http()
            url = f"{service}/v3/conversations/{conv}/activities"
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"type": "typing"},
            ) as resp:
                _ = resp.status
        except Exception:
            pass
