"""Telegram outbound sendMessage — inbound long-poll owned by Go gateway."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


def _safe_err(msg: object) -> str:
    """Log-safe fragment: never echo bot token from Telegram API URLs."""
    from remedy.gateway.messengers import redact_messenger_secrets

    return redact_messenger_secrets(str(msg) if msg is not None else "")[:200]


class TelegramChannel(ChannelAdapter):
    """Telegram bot outbound (sendMessage / typing). Inbound is Go-owned."""

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
        self._session: aiohttp.ClientSession | None = None
        self._api_base = (
            f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else ""
        )

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
            "Telegram outbound-ready (allowlist=%d, allow_all=%s; "
            "Go remedy-runtime owns inbound poll)",
            len(self.chat_ids),
            self.allow_all,
        )

    async def stop(self) -> None:
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
