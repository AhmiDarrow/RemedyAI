"""Mattermost: WebSocket inbound + REST posts outbound."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from urllib.parse import urlparse

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class MattermostChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        base_url: str = "",
        channel_id: str = "",
        team_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.MATTERMOST, gateway)
        self.bot_token = (bot_token or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.channel_id = str(channel_id or "").strip()
        self.team_id = str(team_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._ws_task: asyncio.Task | None = None
        self._seq = 1

    def _ws_url(self) -> str:
        p = urlparse(self.base_url)
        scheme = "wss" if p.scheme == "https" else "ws"
        host = p.netloc or p.path
        return f"{scheme}://{host}/api/v4/websocket"

    async def start(self) -> None:
        await super().start()
        if not (self.bot_token and self.base_url):
            logger.info("Mattermost channel: stub mode (missing token or base_url)")
            return
        logger.info("Mattermost channel active (channel=%s)", self.channel_id)
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token or not self.base_url:
            return True
        ch_id = target or self.channel_id
        if not ch_id:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{self.base_url}/api/v4/posts",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel_id": ch_id, "message": (message or "")[:4000]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Mattermost send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        return  # Mattermost has no simple typing REST for bots

    async def _ws_loop(self) -> None:
        import aiohttp

        while self._running:
            try:
                session = await self.ensure_http()
                async with session.ws_connect(self._ws_url()) as ws:
                    await ws.send_json(
                        {
                            "seq": self._seq,
                            "action": "authentication_challenge",
                            "data": {"token": self.bot_token},
                        }
                    )
                    self._seq += 1
                    logger.info("Mattermost WebSocket connected")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                            continue
                        await self._on_event(json.loads(msg.data))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Mattermost WS error")
                await asyncio.sleep(3.0)

    async def _on_event(self, data: dict) -> None:
        if data.get("event") != "posted":
            return
        try:
            post = json.loads((data.get("data") or {}).get("post") or "{}")
        except Exception:
            return
        if post.get("props", {}).get("from_bot"):
            return
        text = (post.get("message") or "").strip()
        if not text:
            return
        ch = str(post.get("channel_id") or "")
        user = str(post.get("user_id") or "")
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[ch, user, self.team_id],
            channel="mattermost",
        ):
            return
        await emit_message(
            self.gateway,
            ChannelKind.MATTERMOST,
            message=text,
            chat_id=ch,
            source_id=user or ch,
            username=user,
            extra={"user_id": user},
        )
