"""Google Chat: spaces.messages outbound + webhook inbound."""

from __future__ import annotations

import logging
from typing import Any

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

API = "https://chat.googleapis.com/v1"


class GoogleChatChannel(HttpSessionMixin, ChannelAdapter):
    def __init__(
        self,
        gateway,
        *,
        access_token: str = "",
        space_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.GOOGLE_CHAT, gateway)
        self.access_token = (access_token or "").strip()
        self.space_id = str(space_id or "").strip()
        self._allowed = parse_ids(allow_ids)
        if self.space_id:
            self._allowed = self._allowed | frozenset({self.space_id})
        self.allow_all = bool(allow_all)

    async def start(self) -> None:
        await super().start()
        if self.access_token:
            logger.info(
                "Google Chat channel active (space=%s, inbound=webhook)",
                self.space_id or "(any)",
            )
        else:
            logger.info("Google Chat channel: stub mode (no access_token)")

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    def _space_name(self, space: str) -> str:
        s = (space or self.space_id or "").strip()
        if s and not s.startswith("spaces/"):
            s = f"spaces/{s}"
        return s

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self.access_token:
            return True
        space = self._space_name(target or self.space_id)
        if not space:
            return False
        try:
            session = await self.ensure_http()
            async with session.post(
                f"{API}/{space}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={"text": (message or "")[:4096]},
            ) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error("Google Chat send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        return

    def verify_inbound_auth(self, authorization: str | None) -> bool:
        """Require Bearer token matching configured access_token when set.

        Google Chat HTTP push can use app-level bearer verification. When no
        access_token is configured, reject (channel is stub / outbound-only).
        """
        import hmac as _hmac

        if not self.access_token:
            return False
        auth = (authorization or "").strip()
        if not auth.lower().startswith("bearer "):
            # Some Google Chat deployments only use allowlist + private URL.
            # Still require a token when configured — use REMEDY_GCHAT_ALLOW_NO_AUTH=1
            # only for local tunnel debugging.
            import os

            if str(os.environ.get("REMEDY_GCHAT_ALLOW_NO_AUTH", "")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                return True
            logger.warning("Google Chat webhook missing Bearer Authorization")
            return False
        presented = auth[7:].strip()
        # Constant-time; unequal lengths → False (never raise → never 500).
        pe, ee = presented.encode("utf-8"), self.access_token.encode("utf-8")
        if len(pe) != len(ee):
            _hmac.compare_digest(pe, pe)
            return False
        return _hmac.compare_digest(pe, ee)

    async def handle_event(self, data: dict[str, Any]) -> bool:
        """Handle Chat app event (MESSAGE).

        Fail closed when allowlist is empty and allow_all is off (same policy as
        Telegram). Auth is enforced at the webhook route via verify_inbound_auth.
        """
        etype = data.get("type") or data.get("eventType") or ""
        msg = data.get("message") or {}
        if etype and etype not in ("MESSAGE", "message"):
            # Some payloads only include message
            if not msg:
                return False
        text = (msg.get("text") or msg.get("argumentText") or "").strip()
        if not text:
            return False
        space = (data.get("space") or msg.get("space") or {}) or {}
        space_name = str(space.get("name") or self.space_id or "")
        # normalize spaces/xxx
        space_id = space_name.replace("spaces/", "") if space_name else ""
        sender = (msg.get("sender") or data.get("user") or {}) or {}
        user_name = str(sender.get("name") or sender.get("displayName") or "")
        if sender.get("type") == "BOT":
            return False
        # Empty allowlist + not allow_all → ignore (do not open the agent to the world)
        if not self._allowed and not self.allow_all:
            logger.info(
                "Google Chat ignore (empty allowlist, allow_all=false) space=%s",
                space_id or space_name,
            )
            return False
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[space_name, space_id, user_name],
            channel="google_chat",
        ):
            return False
        chat_id = space_name or space_id or "default"
        await emit_message(
            self.gateway,
            ChannelKind.GOOGLE_CHAT,
            message=text,
            chat_id=chat_id,
            source_id=user_name or chat_id,
            username=sender.get("displayName"),
            extra={"user_id": user_name, "space_id": space_id},
        )
        return True
