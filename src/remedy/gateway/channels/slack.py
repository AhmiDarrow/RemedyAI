"""Slack: Socket Mode inbound + chat.postMessage outbound."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

if TYPE_CHECKING:
    from remedy.gateway.poll_lock import MessengerPollLock

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
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.SLACK, gateway)
        self.bot_token = (bot_token or "").strip()
        self.app_token = (app_token or "").strip()
        self.channel_id = str(channel_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.channel_id:
            self._allowed = self._allowed | frozenset({self.channel_id})
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir
        self._ws_task: asyncio.Task | None = None
        self._lock_retry_task: asyncio.Task | None = None
        self._poll_lock: MessengerPollLock | None = None
        # Ordered dedupe of event ids (oldest dropped first — not set-order).
        self._seen: OrderedDict[str, None] = OrderedDict()

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Slack channel: stub mode (no bot token)")
            return
        logger.info("Slack channel active (channel=%s)", self.channel_id)
        if not self.app_token:
            logger.info("Slack: outbound only (set app_token for Socket Mode inbound)")
            return
        started = await self._try_start_socket()
        if not started:
            logger.error(
                "Slack Socket Mode deferred — another process holds the bot lock "
                "(or a stale lock). Will retry every 20s until acquired."
            )
            self._lock_retry_task = asyncio.create_task(self._lock_retry_loop())

    async def _try_start_socket(self) -> bool:
        """Acquire exclusive lock and start Socket Mode. False if locked out."""
        if self._ws_task is not None and not self._ws_task.done():
            return True
        from remedy.gateway.poll_lock import MessengerPollLock

        if self._poll_lock is not None and getattr(self._poll_lock, "held", False):
            pass
        else:
            if self._poll_lock is not None:
                with contextlib.suppress(Exception):
                    self._poll_lock.release()
            self._poll_lock = MessengerPollLock(self._home_dir, "slack")
            if not self._poll_lock.try_acquire():
                self._poll_lock = None
                return False
        self._ws_task = asyncio.create_task(self._socket_loop())
        logger.info("Slack Socket Mode task scheduled")
        return True

    async def _lock_retry_loop(self) -> None:
        while self._running:
            await asyncio.sleep(20.0)
            if not self._running:
                return
            try:
                if await self._try_start_socket():
                    logger.info("Slack Socket Mode acquired after retry")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Slack poll lock retry failed")

    async def stop(self) -> None:
        if self._lock_retry_task:
            self._lock_retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._lock_retry_task
            self._lock_retry_task = None
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        if self._poll_lock is not None:
            with contextlib.suppress(Exception):
                self._poll_lock.release()
            self._poll_lock = None
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
                if self._poll_lock is not None:
                    with contextlib.suppress(Exception):
                        self._poll_lock.heartbeat()
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
            self._seen[eid] = None
            while len(self._seen) > 500:
                self._seen.popitem(last=False)
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
