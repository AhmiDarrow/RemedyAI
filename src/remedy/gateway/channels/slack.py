"""Slack: Socket Mode inbound + chat.postMessage outbound."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class SlackChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        app_token: str = "",
        channel_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.SLACK, gateway)
        self.bot_token = (bot_token or "").strip()
        self.app_token = (app_token or "").strip()
        self.channel_id = str(channel_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._ws_task: asyncio.Task | None = None
        self._seen: set[str] = set()

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Slack channel: stub mode (no bot token)")
            return
        logger.info("Slack channel active (channel=%s)", self.channel_id)
        if self.app_token:
            self._ws_task = asyncio.create_task(self._socket_loop())
        else:
            logger.info("Slack: outbound only (set app_token for Socket Mode inbound)")

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        await self.close_http()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token:
            return True
        ch = target or self.channel_id
        if not ch:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": ch, "text": (message or "")[:3000]},
            ) as resp:
                data = await resp.json()
                return bool(data.get("ok"))
        except Exception as e:
            logger.error("Slack send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        ch = target or self.channel_id
        if not self.bot_token or not ch:
            return
        try:
            session = await self.ensure_http()
            async with session.post(
                "https://slack.com/api/conversations.mark",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": ch},
            ) as resp:
                _ = resp.status
        except Exception:
            pass

    async def _socket_loop(self) -> None:
        import aiohttp

        while self._running:
            try:
                session = await self.ensure_http()
                async with session.post(
                    "https://slack.com/api/apps.connections.open",
                    headers={"Authorization": f"Bearer {self.app_token}"},
                ) as resp:
                    data = await resp.json()
                if not data.get("ok") or not data.get("url"):
                    logger.warning("Slack Socket Mode open failed: %s", data)
                    await asyncio.sleep(5.0)
                    continue
                url = data["url"]
                async with session.ws_connect(url) as ws:
                    logger.info("Slack Socket Mode connected")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                            continue
                        payload = json.loads(msg.data)
                        await self._on_socket(ws, payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Slack socket error")
                await asyncio.sleep(3.0)

    async def _on_socket(self, ws, payload: dict) -> None:
        ptype = payload.get("type")
        envelope_id = payload.get("envelope_id")
        if ptype == "hello":
            return
        if envelope_id:
            await ws.send_json({"envelope_id": envelope_id})
        if ptype != "events_api":
            return
        event = (payload.get("payload") or {}).get("event") or {}
        if event.get("type") != "message" or event.get("subtype"):
            return
        if event.get("bot_id") or event.get("user") is None:
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        ch = str(event.get("channel") or "")
        user = str(event.get("user") or "")
        eid = str(event.get("client_msg_id") or event.get("ts") or "")
        if eid:
            if eid in self._seen:
                return
            self._seen.add(eid)
            if len(self._seen) > 500:
                self._seen = set(list(self._seen)[-200:])
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[ch, user],
            channel="slack",
        ):
            return
        await emit_message(
            self.gateway,
            ChannelKind.SLACK,
            message=text,
            chat_id=ch,
            source_id=user or ch,
            username=user,
            extra={"user_id": user},
        )
