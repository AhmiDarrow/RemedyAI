"""Emit a normalized messenger GatewayEvent (keeps adapters tiny)."""

from __future__ import annotations

from typing import Any

from remedy.models import ChannelKind, EventKind, GatewayEvent


async def emit_message(
    gateway: Any,
    kind: ChannelKind,
    *,
    message: str,
    chat_id: str,
    source_id: str = "",
    username: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "message": message,
        "chat_id": chat_id,
        "channel_id": chat_id,
    }
    if username:
        payload["username"] = username
    if extra:
        payload.update(extra)
    event = GatewayEvent(
        kind=EventKind.MESSAGE,
        channel=kind,
        source_id=source_id or chat_id,
        session_id=chat_id or None,
        payload=payload,
        raw=str(message)[:500],
    )
    await gateway.emit(event)
