"""Telegram bot channel — long-poll inbound + sendMessage outbound.

Optimizations:
- One shared aiohttp session (no per-request TLS handshake)
- 409 (multi-poller) backoff without tight error loops
- deleteWebhook on start so getUpdates is the sole delivery path
- typing indicator for snappier feel while the agent thinks
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind, EventKind, GatewayEvent

logger = logging.getLogger(__name__)


def _safe_err(msg: object) -> str:
    """Log-safe fragment: never echo bot token from Telegram API URLs."""
    from remedy.gateway.messengers import redact_messenger_secrets

    return redact_messenger_secrets(str(msg) if msg is not None else "")[:200]


class TelegramChannel(ChannelAdapter):
    """Telegram bot with long-poll getUpdates + sendMessage."""

    def __init__(
        self,
        gateway,
        *,
        bot_token: str = "",
        chat_ids: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.TELEGRAM, gateway)
        self.bot_token = (bot_token or "").strip()
        self.chat_ids: list[str] = [str(c).strip() for c in (chat_ids or []) if str(c).strip()]
        self._allowed = frozenset(self.chat_ids)
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir
        self._poll_task: asyncio.Task | None = None
        self._lock_retry_task: asyncio.Task | None = None
        self._poll_lock = None  # MessengerPollLock
        self._last_update_id: int = 0
        self._session = None  # aiohttp.ClientSession
        self._api_base = (
            f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else ""
        )
        self._conflict_until = 0.0
        self._last_err_log = 0.0
        self._last_heartbeat = 0.0
        self._typing_tasks: set[asyncio.Task] = set()

    async def _ensure_session(self):
        import aiohttp

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, sock_connect=10),
                headers={"Connection": "keep-alive"},
            )
        return self._session

    async def _close_session(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def start(self) -> None:
        await super().start()
        if not self.bot_token:
            logger.info("Telegram channel: stub mode (no token)")
            return
        logger.info(
            "Telegram channel active (allowlist=%d, allow_all=%s)",
            len(self.chat_ids),
            self.allow_all,
        )
        started = await self._try_start_poller()
        if not started:
            # Owner may be a dead PID that Windows still "OpenProcess"s, or a
            # temporary second instance — keep retrying so messenger recovers.
            logger.error(
                "Telegram long-poll deferred — another process holds the bot lock "
                "(or a stale lock). Will retry every 20s until acquired."
            )
            self._lock_retry_task = asyncio.create_task(self._lock_retry_loop())

    async def _try_start_poller(self) -> bool:
        """Acquire exclusive lock and start getUpdates. Return False if locked out."""
        if self._poll_task is not None and not self._poll_task.done():
            return True
        from remedy.gateway.poll_lock import (
            MessengerPollLock,
            load_update_offset,
        )

        if self._poll_lock is not None and getattr(self._poll_lock, "held", False):
            pass
        else:
            if self._poll_lock is not None:
                with contextlib.suppress(Exception):
                    self._poll_lock.release()
            self._poll_lock = MessengerPollLock(self._home_dir, "telegram")
            if not self._poll_lock.try_acquire():
                self._poll_lock = None
                return False

        # Resume offset so restarts do not re-process a huge backlog ("catch-up flood").
        self._last_update_id = load_update_offset(self._home_dir, "telegram")
        if self._last_update_id:
            logger.info("Telegram resume offset update_id=%s", self._last_update_id)
        # Prefer getUpdates; drop any leftover webhook so we don't fight webhooks.
        try:
            session = await self._ensure_session()
            async with session.post(
                f"{self._api_base}/deleteWebhook",
                json={"drop_pending_updates": False},
            ) as resp:
                if resp.status == 200:
                    logger.debug("Telegram deleteWebhook ok")
        except Exception:
            logger.debug("Telegram deleteWebhook failed", exc_info=True)
        # No saved offset: drain backlog without replaying into sessions (avoids
        # "stuck catching up" after first install / wipe of offset file).
        if self._last_update_id <= 0:
            with contextlib.suppress(Exception):
                await self._drain_backlog_without_handling()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram long-poll task scheduled")
        return True

    async def _lock_retry_loop(self) -> None:
        """Retry exclusive poll ownership until we get it or channel stops."""
        while self._running:
            await asyncio.sleep(20.0)
            if not self._running:
                return
            if self._poll_task is not None and not self._poll_task.done():
                return
            try:
                if await self._try_start_poller():
                    logger.info("Telegram long-poll acquired after retry")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram poll lock retry failed")

    async def stop(self) -> None:
        for t in self._typing_tasks:
            t.cancel()
        self._typing_tasks.clear()
        if self._lock_retry_task is not None:
            self._lock_retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._lock_retry_task
            self._lock_retry_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._poll_lock is not None:
            with contextlib.suppress(Exception):
                self._poll_lock.release()
            self._poll_lock = None
        await self._close_session()
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.bot_token:
            logger.debug("Telegram stub: %s", message[:50])
            return True
        chat_id = target or (self.chat_ids[0] if self.chat_ids else None)
        if chat_id is None:
            return False
        try:
            session = await self._ensure_session()
            async with session.post(
                f"{self._api_base}/sendMessage",
                json={"chat_id": chat_id, "text": (message or "")[:4096]},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram send %s: %s", resp.status, _safe_err(body[:160]))
                return resp.status == 200
        except Exception as e:
            logger.error("Telegram send failed: %s", _safe_err(e))
            return False

    async def send_typing(self, chat_id: str) -> None:
        """Best-effort typing indicator (non-blocking UX polish)."""
        if not self.bot_token or not chat_id:
            return
        try:
            session = await self._ensure_session()
            async with session.post(
                f"{self._api_base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            ) as resp:
                _ = resp.status
        except Exception:
            pass

    async def _drain_backlog_without_handling(self) -> None:
        """Advance offset past pending updates without creating sessions/replies."""
        from remedy.gateway.poll_lock import save_update_offset

        drained = 0
        for _ in range(50):  # max 50 * 100 = 5000 updates
            batch = await self._get_updates(timeout=0)
            if not batch:
                break
            for update in batch:
                uid = update.get("update_id")
                if isinstance(uid, int):
                    self._last_update_id = max(self._last_update_id, uid)
                    drained += 1
            save_update_offset(self._home_dir, self._last_update_id, "telegram")
        if drained:
            logger.info(
                "Telegram drained %d backlog update(s); resume at update_id=%s",
                drained,
                self._last_update_id,
            )

    async def _poll_loop(self) -> None:
        async def _heartbeat_tick() -> None:
            while self._running:
                await asyncio.sleep(20.0)
                if self._poll_lock is not None:
                    with contextlib.suppress(Exception):
                        self._poll_lock.heartbeat()
                    self._last_heartbeat = time.monotonic()

        hb = asyncio.create_task(_heartbeat_tick())
        try:
            while self._running:
                try:
                    now = time.monotonic()
                    if self._poll_lock is not None and now - self._last_heartbeat > 20.0:
                        with contextlib.suppress(Exception):
                            self._poll_lock.heartbeat()
                        self._last_heartbeat = now
                    if now < self._conflict_until:
                        await asyncio.sleep(min(5.0, self._conflict_until - now))
                        continue
                    updates = await self._get_updates(timeout=25)
                    for update in updates:
                        await self._handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Telegram poll error")
                    await asyncio.sleep(2.0)
        finally:
            hb.cancel()
            with contextlib.suppress(Exception):
                await hb

    async def _get_updates(self, timeout: int = 25) -> list[dict]:
        session = await self._ensure_session()
        params = {
            "timeout": timeout,
            "offset": self._last_update_id + 1 if self._last_update_id else 0,
            "allowed_updates": json.dumps(["message", "edited_message"]),
        }
        try:
            async with session.get(
                f"{self._api_base}/getUpdates",
                params=params,
                timeout=aiohttp_timeout(timeout + 10),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await self._handle_poll_error(resp.status, text)
                    return []
                data = await resp.json()
                if not data.get("ok"):
                    return []
                return list(data.get("result") or [])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Network blips — don't spam
            now = time.monotonic()
            if now - self._last_err_log > 30:
                logger.warning("Telegram getUpdates network: %s", _safe_err(e))
                self._last_err_log = now
            await asyncio.sleep(2.0)
            return []

    async def _handle_poll_error(self, status: int, text: str) -> None:
        now = time.monotonic()
        if status == 404:
            if now - self._last_err_log > 60:
                logger.error(
                    "Telegram getUpdates 404 — bot token invalid/revoked. "
                    "Re-paste from @BotFather in Settings → Messengers. %s",
                    _safe_err(text[:120]),
                )
                self._last_err_log = now
            await asyncio.sleep(30.0)
            return
        if status == 409:
            # Another process is polling this bot — back off hard.
            self._conflict_until = now + 25.0
            if now - self._last_err_log > 20:
                logger.warning(
                    "Telegram getUpdates 409 (another poller). "
                    "Only one Remedy instance should own the bot "
                    "(or a leftover serve / other machine). Backing off 25s."
                )
                self._last_err_log = now
            await asyncio.sleep(5.0)
            return
        if now - self._last_err_log > 15:
            logger.warning("Telegram getUpdates %s: %s", status, _safe_err(text[:160]))
            self._last_err_log = now
        await asyncio.sleep(2.0)

    async def _handle_update(self, update: dict) -> None:
        uid = update.get("update_id")
        if isinstance(uid, int):
            self._last_update_id = max(self._last_update_id, uid)
            with contextlib.suppress(Exception):
                from remedy.gateway.poll_lock import save_update_offset

                save_update_offset(self._home_dir, self._last_update_id, "telegram")

        msg = update.get("message") or update.get("edited_message") or {}
        # Ignore service messages and bot echoes (no text). Strip first, the
        # way discord and slack already do: a message of nothing but spaces
        # carries no instruction, and it used to start a whole agent turn.
        text = (msg.get("text") or "").strip()
        if not text:
            return
        # Never re-process bot's own outbound (safety if Telegram ever delivers them).
        from_user = msg.get("from") or {}
        if from_user.get("is_bot"):
            return

        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        user_id = str(from_user.get("id") or "")
        source_id = user_id or chat_id

        env_allow = str(os.environ.get("REMEDY_TELEGRAM_ALLOW_ALL", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        allow_all = bool(self.allow_all) or env_allow
        if not self._allowed and not allow_all:
            logger.info(
                "Telegram ignore chat_id=%s (empty allowlist)",
                chat_id,
            )
            return
        if self._allowed and chat_id not in self._allowed and user_id not in self._allowed:
            logger.info(
                "Telegram ignore chat_id=%s user_id=%s (not allowlisted)",
                chat_id,
                user_id,
            )
            return

        # UX: show typing while agent works
        task = asyncio.create_task(self.send_typing(chat_id))
        self._typing_tasks.add(task)
        task.add_done_callback(self._typing_tasks.discard)

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
            raw=str(update)[:1200],
        )
        logger.info(
            "Telegram inbound chat_id=%s user=%s len=%d",
            chat_id,
            from_user.get("username") or user_id,
            len(text),
        )
        await self.gateway.emit(event)


def aiohttp_timeout(total: float):
    import aiohttp

    return aiohttp.ClientTimeout(total=total)
