"""Signal via signal-cli (daemon JSON-RPC or receive poll). Optional / advanced."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

if TYPE_CHECKING:
    from remedy.gateway.poll_lock import MessengerPollLock

logger = logging.getLogger(__name__)


class SignalChannel(ChannelAdapter):
    """Uses local signal-cli binary when present."""

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
        self._poll_task: asyncio.Task | None = None
        self._lock_retry_task: asyncio.Task | None = None
        self._poll_lock: MessengerPollLock | None = None
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
        logger.info("Signal channel active (cli=%s)", bin_path)
        started = await self._try_start_receive()
        if not started:
            logger.error(
                "Signal receive deferred — another process holds the bot lock "
                "(or a stale lock). Will retry every 20s until acquired."
            )
            self._lock_retry_task = asyncio.create_task(self._lock_retry_loop())

    async def _try_start_receive(self) -> bool:
        """Acquire exclusive lock and start receive poll. False if locked out."""
        if self._poll_task is not None and not self._poll_task.done():
            return True
        from remedy.gateway.poll_lock import MessengerPollLock

        if self._poll_lock is not None and getattr(self._poll_lock, "held", False):
            pass
        else:
            if self._poll_lock is not None:
                with contextlib.suppress(Exception):
                    self._poll_lock.release()
            self._poll_lock = MessengerPollLock(self._home_dir, "signal")
            if not self._poll_lock.try_acquire():
                self._poll_lock = None
                return False
        self._poll_task = asyncio.create_task(self._receive_loop())
        logger.info("Signal receive task scheduled")
        return True

    async def _lock_retry_loop(self) -> None:
        while self._running:
            await asyncio.sleep(20.0)
            if not self._running:
                return
            try:
                if await self._try_start_receive():
                    logger.info("Signal receive acquired after retry")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Signal poll lock retry failed")

    async def stop(self) -> None:
        if self._lock_retry_task:
            self._lock_retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._lock_retry_task
            self._lock_retry_task = None
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._poll_lock is not None:
            with contextlib.suppress(Exception):
                self._poll_lock.release()
            self._poll_lock = None
        await super().stop()

    async def _run(self, *args: str, timeout: float = 60.0) -> tuple[int, str, str]:
        bin_path = self._bin()
        if not bin_path:
            return 1, "", "signal-cli not found"
        cmd = [bin_path, "-a", self.account, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            # Reap it. kill() only signals; without the wait the child stays a
            # zombie, and _receive_loop polls every 10s, so they accumulate.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            return 1, "", "timeout"
        return (
            int(proc.returncode or 0),
            out_b.decode("utf-8", errors="replace"),
            err_b.decode("utf-8", errors="replace"),
        )

    async def send(self, message: str, target: str | None = None) -> bool:
        if not self._bin() or not self.account:
            return False
        to = (target or "").strip()
        if not to:
            return False
        # signal-cli: send -m "msg" NUMBER
        code, _out, err = await self._run("send", "-m", message or "", to, timeout=90.0)
        if code != 0:
            logger.warning("signal-cli send failed: %s", err[:200])
            return False
        return True

    async def send_typing(self, target: str | None = None) -> None:
        return

    async def _receive_loop(self) -> None:
        """Poll receive -t 10 (json lines)."""
        while self._running:
            try:
                if self._poll_lock is not None:
                    with contextlib.suppress(Exception):
                        self._poll_lock.heartbeat()
                code, out, err = await self._run(
                    "receive", "-t", "10", "--json", timeout=30.0
                )
                if code != 0 and err:
                    logger.debug("signal receive: %s", err[:120])
                for line in (out or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    await self._on_envelope(data)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Signal receive error")
                await asyncio.sleep(2.0)

    async def _on_envelope(self, data: dict) -> None:
        env = data.get("envelope") or data
        source = str(env.get("source") or env.get("sourceNumber") or "")
        data_msg = env.get("dataMessage") or {}
        text = (data_msg.get("message") or "").strip()
        if not text:
            return
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[source],
            channel="signal",
        ):
            return
        await emit_message(
            self.gateway,
            ChannelKind.SIGNAL,
            message=text,
            chat_id=source or "signal",
            source_id=source,
            username=source,
            extra={"user_id": source},
        )
