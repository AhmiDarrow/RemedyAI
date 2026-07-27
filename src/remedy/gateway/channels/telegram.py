"""Telegram bot channel — long-poll inbound + sendMessage outbound."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind, EventKind, GatewayEvent

logger = logging.getLogger(__name__)


class TelegramChannel(ChannelAdapter):
    """Telegram bot with long-poll getUpdates + sendMessage."""

    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        chat_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.TELEGRAM, gateway)
        self.bot_token = bot_token
        self.chat_ids: list[str] = [str(c) for c in (chat_ids or [])]
        self.allow_all = bool(allow_all)
        self._poll_task: asyncio.Task | None = None
        self._last_update_id: int = 0

    async def start(self) -> None:
        await super().start()
        if self.bot_token:
            logger.info(
                "Telegram channel active (allowlist=%d, allow_all=%s)",
                len(self.chat_ids),
                self.allow_all,
            )
            # Ensure we are on a live loop (uvicorn lifespan); cancelled loops drop polls.
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("Telegram long-poll task scheduled")
        else:
            logger.info("Telegram channel: stub mode (no token)")

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token:
            logger.debug("Telegram stub: %s", message[:50])
            return True

        import aiohttp

        chat_id = target or (self.chat_ids[0] if self.chat_ids else None)
        if chat_id is None:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    url,
                    json={"chat_id": chat_id, "text": message[:4096]},
                ) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error("Telegram send failed: %s", e)
                return False

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = await self._get_updates(timeout=25)
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram poll error")
                await asyncio.sleep(2.0)

    async def _get_updates(self, timeout: int = 25) -> list[dict]:
        import aiohttp

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {
            "timeout": timeout,
            "offset": self._last_update_id + 1 if self._last_update_id else 0,
            "allowed_updates": json.dumps(["message", "edited_message"]),
        }
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout + 10),
            ) as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                logger.warning("Telegram getUpdates %s: %s", resp.status, text[:200])
                await asyncio.sleep(1.0)
                return []
            data = await resp.json()
            if not data.get("ok"):
                return []
            return list(data.get("result") or [])

    async def _handle_update(self, update: dict) -> None:
        uid = update.get("update_id")
        if isinstance(uid, int):
            self._last_update_id = max(self._last_update_id, uid)

        msg = update.get("message") or update.get("edited_message") or {}
        text = msg.get("text")
        if not text:
            return

        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        from_user = msg.get("from") or {}
        user_id = str(from_user.get("id") or "")
        source_id = user_id or chat_id

        env_allow = str(os.environ.get("REMEDY_TELEGRAM_ALLOW_ALL", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        allow_all = bool(self.allow_all) or env_allow
        if not self.chat_ids and not allow_all:
            logger.info(
                "Telegram ignore chat_id=%s user_id=%s (allowlist empty; set allow_chat_ids or allow_all)",
                chat_id,
                user_id,
            )
            return
        # Accept either chat id (DM chat id == user id) or explicit user id.
        if self.chat_ids:
            allowed = {str(x).strip() for x in self.chat_ids if str(x).strip()}
            if chat_id not in allowed and user_id not in allowed:
                logger.info(
                    "Telegram ignore chat_id=%s user_id=%s (not in allowlist %s)",
                    chat_id,
                    user_id,
                    sorted(allowed),
                )
                return

        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.TELEGRAM,
            source_id=source_id,
            session_id=chat_id or None,
            payload={
                "message": text,
                "chat_id": chat_id,
                "user_id": user_id,
                "username": from_user.get("username"),
            },
            raw=str(update)[:2000],
        )
        logger.info(
            "Telegram inbound chat_id=%s user=%s len=%d",
            chat_id,
            from_user.get("username") or user_id,
            len(text),
        )
        await self.gateway.emit(event)
