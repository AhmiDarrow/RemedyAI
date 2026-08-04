"""Matrix: /sync long-poll inbound + room send outbound."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
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
    ) -> None:
        super().__init__(ChannelKind.MATRIX, gateway)
        self.access_token = (access_token or "").strip()
        self.homeserver = (homeserver or "").rstrip("/")
        self.user_id = str(user_id or "").strip()
        self.room_id = str(room_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.room_id:
            self._allowed = self._allowed | frozenset({self.room_id})
        self.allow_all = bool(allow_all)
        self._sync_task: asyncio.Task | None = None
        self._since: str | None = None
        self._http_timeout_s = 90.0
        self._typing_tasks: set[asyncio.Task] = set()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def start(self) -> None:
        await super().start()
        if not (self.access_token and self.homeserver):
            logger.info("Matrix channel: stub mode (missing token or homeserver)")
            return
        logger.info("Matrix channel active (room=%s)", self.room_id)
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        for t in self._typing_tasks:
            t.cancel()
        self._typing_tasks.clear()
        if self._sync_task:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None
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

    async def _sync_loop(self) -> None:
        while self._running:
            try:
                session = await self.ensure_http()
                params: dict = {"timeout": 30000}
                if self._since:
                    params["since"] = self._since
                async with session.get(
                    f"{self.homeserver}/_matrix/client/v3/sync",
                    headers=self._headers(),
                    params=params,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning("Matrix sync %s: %s", resp.status, text[:160])
                        await asyncio.sleep(3.0)
                        continue
                    data = await resp.json()
                self._since = data.get("next_batch") or self._since
                await self._handle_sync(data)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Matrix sync error")
                await asyncio.sleep(3.0)

    async def _handle_sync(self, data: dict) -> None:
        rooms = ((data.get("rooms") or {}).get("join")) or {}
        for room_id, body in rooms.items():
            timeline = (body.get("timeline") or {}).get("events") or []
            for ev in timeline:
                if ev.get("type") != "m.room.message":
                    continue
                sender = str(ev.get("sender") or "")
                if self.user_id and sender == self.user_id:
                    continue
                content = ev.get("content") or {}
                if content.get("msgtype") != "m.text":
                    continue
                text = (content.get("body") or "").strip()
                if not text:
                    continue
                if not is_allowed(
                    allowlist=self._allowed,
                    allow_all=self.allow_all,
                    candidates=[room_id, sender],
                    channel="matrix",
                ):
                    continue
                task = asyncio.create_task(self.send_typing(room_id))
                self._typing_tasks.add(task)
                task.add_done_callback(self._typing_tasks.discard)
                await emit_message(
                    self.gateway,
                    ChannelKind.MATRIX,
                    message=text,
                    chat_id=room_id,
                    source_id=sender or room_id,
                    username=sender,
                    extra={"user_id": sender, "room_id": room_id},
                )
