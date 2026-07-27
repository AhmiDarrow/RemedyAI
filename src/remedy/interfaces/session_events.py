"""In-process pub/sub for realtime desktop session sync.

When messengers (or any path) create/update sessions or append messages,
call ``publish_session_event``. Desktop clients subscribe via
``GET /api/events/sessions`` (SSE) and refresh list / active thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    """One realtime session notification."""

    type: str  # session_created | session_updated | message_added | session_deleted
    session_id: str
    origin_channel: str | None = None
    message_id: str | None = None
    title: str | None = None
    message_count: int | None = None
    role: str | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "origin_channel": self.origin_channel,
            "message_id": self.message_id,
            "title": self.title,
            "message_count": self.message_count,
            "role": self.role,
            "ts": self.ts,
        }


class SessionEventHub:
    """Fan-out hub: each subscriber gets an asyncio.Queue of SessionEvent."""

    def __init__(self, *, max_queue: int = 256) -> None:
        self._subs: list[asyncio.Queue[SessionEvent | None]] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue
        self._seq = 0

    async def subscribe(self) -> asyncio.Queue[SessionEvent | None]:
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subs.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[SessionEvent | None]) -> None:
        async with self._lock:
            if q in self._subs:
                self._subs.remove(q)
        # Unblock waiter
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(None)

    async def publish(self, event: SessionEvent) -> None:
        self._seq += 1
        async with self._lock:
            subs = list(self._subs)
        dead: list[asyncio.Queue[SessionEvent | None]] = []
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest then retry once so slow clients don't stall publishers
                try:
                    _ = q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    dead.append(q)
            except Exception:
                dead.append(q)
        if dead:
            async with self._lock:
                for q in dead:
                    if q in self._subs:
                        self._subs.remove(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


_HUB: SessionEventHub | None = None


def get_session_event_hub() -> SessionEventHub:
    global _HUB
    if _HUB is None:
        _HUB = SessionEventHub()
    return _HUB


def reset_session_event_hub() -> None:
    """Test helper — clear hub between tests."""
    global _HUB
    _HUB = None


async def publish_session_event(
    type: str,
    session_id: str,
    *,
    origin_channel: str | None = None,
    message_id: str | None = None,
    title: str | None = None,
    message_count: int | None = None,
    role: str | None = None,
) -> None:
    """Publish a session event to all SSE subscribers (best-effort)."""
    if not session_id:
        return
    try:
        hub = get_session_event_hub()
        await hub.publish(
            SessionEvent(
                type=type,
                session_id=session_id,
                origin_channel=origin_channel,
                message_id=message_id,
                title=title,
                message_count=message_count,
                role=role,
            )
        )
    except Exception as exc:
        logger.debug("session event publish failed: %s", exc)
