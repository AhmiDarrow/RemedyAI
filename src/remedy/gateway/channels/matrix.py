"""Matrix room send outbound — /sync inbound owned by Go."""

from __future__ import annotations

import logging

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MatrixChannel(HttpSessionMixin, ChannelAdapter):
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
        self._http_timeout_s = 90.0

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def start(self) -> None:
        await super().start()
        if not (self.access_token and self.homeserver):
            logger.info("Matrix channel: stub mode (missing token or homeserver)")
            return
        if not self.user_id:
            try:
                session = await self.ensure_http()
                async with session.get(
                    f"{self.homeserver}/_matrix/client/v3/account/whoami",
                    headers=self._headers(),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.user_id = str(data.get("user_id") or "").strip()
            except Exception:
                logger.exception("Matrix whoami failed")
        logger.info(
            "Matrix outbound-ready (room=%s; Go remedy-runtime owns inbound sync)",
            self.room_id,
        )

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.access_token or not self.homeserver:
            return True
        room = target or self.room_id
        if not room:
            return False
        try:
            import uuid

            session = await self.ensure_http()
            txn = uuid.uuid4().hex
            url = (
                f"{self.homeserver}/_matrix/client/v3/rooms/"
                f"{room}/send/m.room.message/{txn}"
            )
            async with session.put(
                url,
                headers=self._headers(),
                json={"msgtype": "m.text", "body": (message or "")[:4000]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Matrix send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        room = target or self.room_id
        if not room or not self.access_token or not self.user_id:
            return
        try:
            session = await self.ensure_http()
            url = f"{self.homeserver}/_matrix/client/v3/rooms/{room}/typing/{self.user_id}"
            async with session.put(
                url,
                headers=self._headers(),
                json={"typing": True, "timeout": 10000},
            ) as resp:
                _ = resp.status
        except Exception:
            pass
