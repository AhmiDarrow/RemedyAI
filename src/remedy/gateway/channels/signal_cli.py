"""Signal outbound via signal-cli — receive inbound owned by Go + Zig."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from remedy.gateway.channels.allowlist import parse_ids
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


class SignalChannel(ChannelAdapter):
    """Uses local signal-cli binary when present (send only)."""

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
            "Signal outbound-ready (cli=%s; Go remedy-runtime owns inbound receive)",
            bin_path,
        )

    async def stop(self) -> None:
        await super().stop()

    async def _run(self, *args: str, timeout: float = 60.0) -> tuple[int, str, str]:
        bin_path = self._bin()
        if not bin_path:
            return 1, "", "signal-cli not found"
        import subprocess

        from remedy.execution.process import run_hidden_async

        cmd = [bin_path, "-a", self.account, *args]
        try:
            completed = await run_hidden_async(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 1, "", "timeout"
        return (
            int(completed.returncode or 0),
            str(completed.stdout or ""),
            str(completed.stderr or ""),
        )

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self._bin() or not self.account:
            return False
        to = (target or "").strip()
        if not to:
            return False
        code, _out, err = await self._run("send", "-m", message or "", to, timeout=90.0)
        if code != 0:
            logger.warning("signal-cli send failed: %s", err[:200])
            return False
        return True

    async def send_typing(self, target: str | None = None) -> None:
        return
