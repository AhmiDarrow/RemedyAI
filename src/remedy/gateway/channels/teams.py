"""Teams TestClient stub — Go owns Bot Framework webhook inbound + connector send."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class TeamsChannel(ChannelAdapter):
    """No OAuth/HTTP from Python — conversation refs kept for TestClient shape only."""

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
        self._last_service_url: str = ""
        self._last_conversation_id: str = ""

    async def start(self) -> None:
        await super().start()
        if self.app_id and self.app_password:
            logger.info("Teams TestClient stub; Go remedy-runtime owns network")
        else:
            logger.info("Teams channel: stub mode (missing app_id/password)")

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not (self.app_id and self.app_password):
            return True
        conv = (target or self._last_conversation_id or "").strip()
        service = self._last_service_url.rstrip("/")
        if not conv or not service:
            logger.warning("Teams send: no conversation reference yet")
            return False
        return True

    async def send_typing(self, target: str | None = None) -> None:
        _ = target
