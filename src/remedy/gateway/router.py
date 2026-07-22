"""Gateway: the always-on multi-channel event hub.

Enhanced version with session-aware routing, channel management,
rate limiting, and event persistence. Bridges CLI, Telegram, Discord,
Web/API, and webhook channels to the core runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Union
from uuid import UUID, uuid4

from remedy.models import ChannelKind, EventKind, GatewayEvent, MemoryEntry, MemoryEntryType
from remedy.memory.store import MemoryStore

logger = logging.getLogger(__name__)

EventHandler = Callable[[GatewayEvent], AsyncIterator[Any]]


class ChannelAdapter:
    """Base class for channel adapters."""

    def __init__(self, kind: ChannelKind, gateway: "Gateway") -> None:
        self.kind = kind
        self.gateway = gateway
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        logger.info("Channel %s started", self.kind.value)

    async def stop(self) -> None:
        self._running = False
        logger.info("Channel %s stopped", self.kind.value)

    async def send(self, message: str, target: Optional[str] = None) -> bool:
        """Send a message through this channel. Override for real implementations."""
        return True


class Gateway:
    """Async multi-channel event router with heartbeat, rate limiting,
    event persistence, and session-aware routing."""

    def __init__(
        self,
        runtime,  # AgentRuntime
        heartbeat_interval: float = 60.0,
        rate_limit: int = 60,  # max events per minute per channel
        memory_store: Optional[MemoryStore] = None,
    ) -> None:
        self.runtime = runtime
        self.heartbeat_interval = heartbeat_interval
        self.rate_limit = rate_limit
        self.memory = memory_store

        self._handlers: list[EventHandler] = []
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[GatewayEvent] = asyncio.Queue()

        self._channels: dict[ChannelKind, ChannelAdapter] = {}
        self._rate_buckets: dict[ChannelKind, list[float]] = defaultdict(list)
        self._event_counter: int = 0
        self._started_at: Optional[datetime] = None

    # -- properties ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    @property
    def channels(self) -> list[ChannelKind]:
        return list(self._channels.keys())

    # -- channel management --------------------------------------------------

    def register_channel(self, adapter: ChannelAdapter) -> None:
        self._channels[adapter.kind] = adapter

    def get_channel(self, kind: ChannelKind) -> Optional[ChannelAdapter]:
        return self._channels.get(kind)

    async def broadcast(self, message: str, exclude: Optional[list[ChannelKind]] = None) -> dict[ChannelKind, bool]:
        """Send a message to all channels."""
        exclude = exclude or []
        results: dict[ChannelKind, bool] = {}
        for kind, ch in self._channels.items():
            if kind not in exclude:
                results[kind] = await ch.send(message)
        return results

    async def send_to(self, kind: ChannelKind, message: str, target: Optional[str] = None) -> bool:
        ch = self._channels.get(kind)
        if ch is None:
            return False
        return await ch.send(message, target)

    # -- handlers ------------------------------------------------------------

    def register_handler(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def remove_handler(self, handler: EventHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._started_at = datetime.utcnow()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._queue_task = asyncio.create_task(self._process_queue())

        for ch in self._channels.values():
            await ch.start()

        logger.info(
            "Gateway started (heartbeat=%.1fs, rate_limit=%d/min, channels=%d)",
            self.heartbeat_interval, self.rate_limit, self.channel_count,
        )

    async def stop(self) -> None:
        self._running = False

        for ch in self._channels.values():
            await ch.stop()

        for task in [self._heartbeat_task, self._queue_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Gateway stopped (events=%d, uptime=%s)",
                     self._event_counter,
                     self._uptime_str())

    # -- event emission ------------------------------------------------------

    async def emit(self, event: GatewayEvent) -> list[Any]:
        """Send an event through the gateway and collect responses."""
        if not self._check_rate_limit(event.channel):
            logger.warning("Rate limit exceeded for %s", event.channel.value)
            return [{"error": "rate_limited"}]

        self._event_counter += 1
        responses: list[Any] = []

        for handler in self._handlers:
            try:
                result = handler(event)
                if inspect.isasyncgen(result):
                    async for response in result:
                        responses.append(response)
                elif inspect.isawaitable(result):
                    response = await result
                    if response is not None:
                        responses.append(response)
            except Exception:
                logger.exception("Handler error for event %s", event.id)

        if self.memory:
            try:
                from datetime import datetime as _dt
                mem_entry = MemoryEntry(
                    entry_type=MemoryEntryType.SYSTEM,
                    title=f"Gateway event: {event.kind.value}",
                    content=json.dumps(event.payload, default=str)[:500],
                    importance=0.2,
                    tags=["gateway", event.channel.value],
                    session_id=event.session_id,
                )
                await self.memory.upsert(mem_entry)
            except Exception:
                logger.exception("Failed to persist gateway event")

        return responses

    async def enqueue(self, event: GatewayEvent) -> None:
        await self._event_queue.put(event)

    # -- stats ---------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "events_processed": self._event_counter,
            "channels_active": self.channel_count,
            "channels": [c.value for c in self._channels],
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "uptime": self._uptime_str(),
            "rate_limit_per_min": self.rate_limit,
        }

    # -- internal ------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            heartbeat = GatewayEvent(
                kind=EventKind.HEARTBEAT,
                channel=ChannelKind.CLI,
                source_id="system",
                payload={
                    "timestamp": datetime.utcnow().isoformat(),
                    "uptime": self._uptime_str(),
                    "events": self._event_counter,
                },
            )
            try:
                await self.emit(heartbeat)
            except Exception:
                logger.exception("Heartbeat error")

    async def _process_queue(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self.emit(event)
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("Queue processing error")

    def _check_rate_limit(self, channel: ChannelKind) -> bool:
        now = time.time()
        bucket = self._rate_buckets[channel]
        bucket[:] = [t for t in bucket if now - t < 60.0]
        if len(bucket) >= self.rate_limit:
            return False
        bucket.append(now)
        return True

    def _uptime_str(self) -> str:
        if self._started_at is None:
            return "0s"
        delta = datetime.utcnow() - self._started_at
        total = int(delta.total_seconds())
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h}h {m}m {s}s"
