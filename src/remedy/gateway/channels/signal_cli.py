"""Signal TestClient stub — Go + Zig exec-capture owns signal-cli send/receive."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class SignalChannel(ChannelAdapter):
    """Shape-compatible stub; never spawns signal-cli from Python."""

    def __init__(
        self,
        gateway,
        *,
        cli_path: str = "signal-cli",
        account: str = "",
        allow_from: list[str] | None = None,
        allow_all: bool = False,
        home_dir: str | None = None,
    ) -> None:
        super().__init__(ChannelKind.SIGNAL, gateway)
        self.cli_path = (cli_path or "signal-cli").strip()
        self.account = (account or "").strip()
        self._allowed = parse_ids(allow_from)
        self.allow_all = bool(allow_all)
        self._home_dir = home_dir
        self._resolved: str | None = None

    def _bin(self) -> str | None:
        if self._resolved:
            return self._resolved
        p = Path(self.cli_path)
        if p.is_file():
            self._resolved = str(p)
            return self._resolved
        found = shutil.which(self.cli_path)
        self._resolved = found
        return found

    async def start(self) -> None:
        await super().start()
        bin_path = self._bin()
        if not bin_path or not self.account:
            logger.info(
                "Signal channel: stub (signal-cli=%s account=%s)",
                bool(bin_path),
                bool(self.account),
            )
            return
        logger.info(
            "Signal TestClient stub (cli=%s); Go+Zig owns exec-capture network",
            bin_path,
        )

    async def stop(self) -> None:
        await super().stop()

    async def send(self, message: str, target: str | None = None) -> bool:
        _ = message
        if not self._bin() or not self.account:
            return False
        return bool((target or "").strip())

    async def send_typing(self, target: str | None = None) -> None:
        _ = target
