"""CLI channel adapter."""

from __future__ import annotations

import asyncio
import logging

from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class CLIChannel(ChannelAdapter):
    """Real-time interactive CLI channel (stdin/stdout)."""

    def __init__(self, gateway, *, prompt: str = "remedy> ") -> None:
        super().__init__(ChannelKind.CLI, gateway)
        self.prompt = prompt
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        logger.info("CLI channel active (prompt: %r)", self.prompt)

    async def send(self, message: str, target: str | None = None) -> bool:
        print(f"\n{message}")
        return True

    async def read_line(self, timeout: float | None = None) -> str | None:
        """Read a single line from stdin (async-compatible wrapper)."""
        try:
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, input, self.prompt)
            if timeout is None:
                return await fut
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            except TimeoutError:
                return None
        except (EOFError, KeyboardInterrupt):
            return None
