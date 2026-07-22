"""Gateway event router -- the always-on hub for multi-channel messaging.

Inspired by OpenClaw's gateway architecture. Receives events from chat
platforms, timers, and webhooks; routes them to the core runtime; and
supports proactive heartbeat-driven behavior.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Optional

from remedy.models import ChannelKind, EventKind, GatewayEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[GatewayEvent], AsyncIterator[Any]]


class Gateway:
    """Async event router with heartbeat for always-on operation.

    Usage:
        gw = Gateway(runtime)
        gw.register_handler(my_handler)
        await gw.start()
        await gw.emit(GatewayEvent(kind=EventKind.MESSAGE, channel=ChannelKind.CLI, ...))
    """

    def __init__(
        self,
        runtime,  # AgentRuntime
        heartbeat_interval: float = 60.0,
    ) -> None:
        self.runtime = runtime
        self.heartbeat_interval = heartbeat_interval
        self._handlers: list[EventHandler] = []
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[GatewayEvent] = asyncio.Queue()

    @property
    def running(self) -> bool:
        return self._running

    def register_handler(self, handler: EventHandler) -> None:
        """Register a callable that processes GatewayEvent and yields responses."""
        self._handlers.append(handler)

    async def start(self) -> None:
        """Begin the event loop and heartbeat."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Gateway started (heartbeat=%.1fs)", self.heartbeat_interval)

    async def stop(self) -> None:
        """Gracefully shut down."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("Gateway stopped")

    async def emit(self, event: GatewayEvent) -> list[Any]:
        """Send an event through the gateway and collect responses."""
        responses: list[Any] = []
        for handler in self._handlers:
            async for response in handler(event):
                responses.append(response)
        return responses

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            heartbeat = GatewayEvent(
                kind=EventKind.HEARTBEAT,
                channel=ChannelKind.CLI,
                source_id="system",
                payload={"timestamp": datetime.utcnow().isoformat()},
            )
            try:
                await self.emit(heartbeat)
            except Exception:
                logger.exception("Heartbeat handler error")

    async def enqueue(self, event: GatewayEvent) -> None:
        """Add an event to the async processing queue."""
        await self._event_queue.put(event)

    async def process_queue(self) -> None:
        """Worker loop to drain the event queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self.emit(event)
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("Event processing error")
