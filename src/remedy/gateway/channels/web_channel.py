"""REST / web channel adapter for API request correlation."""

from __future__ import annotations

import asyncio

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind


class WebChannel(ChannelAdapter):
    """Used by FastAPI to relay HTTP requests as gateway events."""

    def __init__(self, gateway) -> None:
        super().__init__(ChannelKind.WEB, gateway)
        self._pending_responses: dict[str, asyncio.Future] = {}

    async def send(self, message: str, target: str | None = None) -> bool:
        if target and target in self._pending_responses:
            fut = self._pending_responses.pop(target)
            if not fut.done():
                fut.set_result(message)
            return True
        return False

    def await_response(self, request_id: str, timeout: float = 30.0) -> asyncio.Future:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_responses[request_id] = fut
        return fut
