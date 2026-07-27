"""Stub adapters for catalog messengers not yet fully implemented."""

from __future__ import annotations

import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class PlannedMessengerChannel(ChannelAdapter):
    """Logs planned status; outbound returns False until implemented."""

    def __init__(self, kind: ChannelKind, gateway, *, label: str = "") -> None:
        super().__init__(kind, gateway)
        self.label = label or kind.value

    async def start(self) -> None:
        await super().start()
        logger.info("%s channel: planned (not yet active)", self.label)

    async def send(self, message: str, target: str | None = None) -> bool:
        logger.debug("%s planned stub: %s", self.label, message[:50])
        return False
