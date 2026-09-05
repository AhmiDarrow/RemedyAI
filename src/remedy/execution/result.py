"""Execution result contract and Zig write-jail subprocess sandbox."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remedy.core.security import check_dangerous_command
from remedy.execution.env import scrub_subprocess_env, unattended_vcs_env


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0


class Sandbox:
    """Base class for execution backends."""

    _workdir: Path | None = None

    async def execute(
        self,
        command: list[str],
        workdir: Path | None = None,
        timeout_seconds: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        raise NotImplementedError

    async def check_available(self) -> bool:
        """Check whether this sandbox backend is available."""
        return True


def _spawn_error_stderr(command: list[str], exc: OSError) -> str:
    """Human stderr for a failed spawn.

    WSL + Windows PATH entries often raise ``PermissionError`` (EACCES) for an
    unknown command name instead of ``FileNotFoundError``.
    """
    import shutil

    name = str(command[0]) if command else ""
    if isinstance(exc, FileNotFoundError):
        return f"Command not found: {name or exc}"
    if isinstance(exc, PermissionError) and name:
        if not Path(name).is_file() and shutil.which(name) is None:
            return f"Command not found: {name}"
    return f"OS error: {exc}"


def _clip_output(text: str, stream: str) -> str:
    """Soft ExecutionBudget cap so a child cannot flood the turn."""
    from remedy.execution.budgets import ExecutionBudget

    return ExecutionBudget().clip(text, stream=stream)


def _install_write_roots(roots: list[Path]) -> None:
    """Push workdir roots into Zig write-jail. Empty = Full / unbound."""
    from remedy.core.computer import host_binding

    host_binding.write_jail_set_roots([str(p) for p in roots])


def _clear_write_roots() -> None:
    """Drop process-wide Zig write-jail roots so later Full spawns stay unbound."""
    from remedy.core.computer import host_binding
    from remedy.core.computer.host_binding import HostError
    from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

    with contextlib.suppress(
        HostError, NativeRuntimeUnavailableError, OSError, AttributeError
    ):
        host_binding.write_jail_clear()


def _jail_denied_result(
    *,
    detail: str,
    start: float,
    what: str = "spawn",
) -> ExecutionResult:
    return ExecutionResult(
        exit_code=-1,
        stderr=f"Blocked by write jail ({what}): {detail}",
        duration_ms=(time.monotonic() - start) * 1000,
    )


def _check_spawn_jail(argv: list[str], cwd: Path | None, *, start: float) -> ExecutionResult | None:
    """Zig write-jail spawn gate. ACCESS_DENIED → result; anything else raises."""
    from remedy.core.computer import host_binding
    from remedy.core.computer.host_binding import STATUS_ACCESS_DENIED, HostError

    try:
        host_binding.write_jail_check_spawn(argv, str(cwd) if cwd else None)
    except HostError as exc:
        if exc.status == STATUS_ACCESS_DENIED:
            return _jail_denied_result(detail=str(exc), start=start, what="spawn")
        raise
    return None


def _check_path_jail(
    path: Path | str,
    cwd: Path | None,
    *,
    start: float,
    what: str,
) -> ExecutionResult | None:
    """Zig write-jail path gate for workdir."""
    from remedy.core.computer import host_binding
    from remedy.core.computer.host_binding import STATUS_ACCESS_DENIED, HostError

    try:
        host_binding.write_jail_check_path(str(path), str(cwd) if cwd else None)
    except HostError as exc:
        if exc.status == STATUS_ACCESS_DENIED:
            return _jail_denied_result(detail=str(exc), start=start, what=what)
        raise
    return None


class SubprocessSandbox(Sandbox):
    """Execute argv under Zig write-jail with shell-chain + abort flag."""

    def __init__(
        self,
        shell: str | None = None,
        allowed_paths: list[Path] | None = None,
        max_input_bytes: int = 1_000_000,
    ) -> None:
        self.shell = shell
        self.allowed_paths = allowed_paths or []
        self.max_input_bytes = max_input_bytes

    async def execute(
        self,
        command: list[str],
        workdir: Path | None = None,
        timeout_seconds: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        start = time.monotonic()

        danger = check_dangerous_command(command)
        if danger:
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Blocked by security policy: {danger}",
                duration_ms=0.0,
            )

        _install_write_roots(self.allowed_paths)
        try:
            if workdir is not None:
                denied = _check_path_jail(workdir, None, start=start, what="workdir")
                if denied is not None:
                    return denied

            chain = await self._execute_shell_chain(
                list(command),
                workdir=workdir,
                timeout_seconds=timeout_seconds,
                env=env,
                start=start,
            )
            if chain is not None:
                return chain
            return await self._execute_one(
                list(command),
                workdir=workdir,
                timeout_seconds=timeout_seconds,
                env=env,
                start=start,
            )
        finally:
            _clear_write_roots()

    async def _execute_shell_chain(
        self,
        command: list[str],
        *,
        workdir: Path | None,
        timeout_seconds: float,
        env: dict[str, str] | None,
        start: float,
    ) -> ExecutionResult | None:
        """Zig shell-chain execute. Returns ``None`` when not a chain."""
        from remedy.core.computer.host_binding import HostError, shell_chain_execute
        from remedy.core.turn_context import current_abort_event, is_turn_aborted

        if is_turn_aborted():
            return ExecutionResult(
                exit_code=-1,
                stderr="Aborted before start (session stop)",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        safe_env = scrub_subprocess_env(env, argv=command)
        safe_env.setdefault("PYTHONIOENCODING", "utf-8")
        safe_env.setdefault("PYTHONUTF8", "1")

        payload: dict[str, Any] = {
            "argv": [str(a) for a in command],
            "timeout_ms": max(1, int(float(timeout_seconds) * 1000)),
            "env": {str(k): str(v) for k, v in safe_env.items()},
        }
        if workdir is not None:
            payload["cwd"] = str(workdir)
            payload["project_path"] = str(workdir)

        abort_flag = ctypes.c_uint8(0)
        abort_ev = current_abort_event()
        watch: asyncio.Task[Any] | None = None
        if abort_ev is not None:

            async def _watch_abort() -> None:
                await abort_ev.wait()
                abort_flag.value = 1

            watch = asyncio.create_task(_watch_abort())

        try:
            try:
                result = await asyncio.to_thread(
                    shell_chain_execute,
                    payload,
                    abort_flag=abort_flag,
                )
            except HostError:
                raise
        finally:
            if watch is not None and not watch.done():
                watch.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watch

        if result.get("not_a_chain"):
            return None

        elapsed = (time.monotonic() - start) * 1000
        return ExecutionResult(
            exit_code=int(result.get("exit_code") or 0),
            stdout=_clip_output(str(result.get("stdout") or ""), "stdout"),
            stderr=_clip_output(str(result.get("stderr") or ""), "stderr"),
            duration_ms=float(result.get("duration_ms") or elapsed),
        )

    async def _execute_one(
        self,
        command: list[str],
        *,
        workdir: Path | None,
        timeout_seconds: float,
        env: dict[str, str] | None,
        start: float,
    ) -> ExecutionResult:
        denied = _check_spawn_jail(command, workdir, start=start)
        if denied is not None:
            return denied

        head = Path(command[0]).name.lower() if command else ""
        if head.endswith(".exe"):
            head = head[:-4]
        if head in {"git", "gh"}:
            safe_env = unattended_vcs_env(command, env)
        else:
            safe_env = scrub_subprocess_env(env, argv=command)
        safe_env.setdefault("PYTHONIOENCODING", "utf-8")
        safe_env.setdefault("PYTHONUTF8", "1")

        try:
            import subprocess

            from remedy.core.computer.host_binding import HostError
            from remedy.core.turn_context import current_abort_event, is_turn_aborted
            from remedy.execution.process import run_hidden_async, spawn_piped, wait_piped
            from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

            if is_turn_aborted():
                return ExecutionResult(
                    exit_code=-1,
                    stderr="Aborted before start (session stop)",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            cwd_s = str(workdir) if workdir else None
            abort_ev = current_abort_event()
            # exec_capture cannot be killed mid-wait; during a turn use piped
            # spawn so abort_session can kill the child promptly (shell_chain
            # already polls abort_flag for multi-hop commands).
            if abort_ev is not None:
                child = await asyncio.to_thread(
                    spawn_piped,
                    command,
                    cwd=cwd_s,
                    env=safe_env,
                    text=True,
                )
                try:
                    with contextlib.suppress(Exception):
                        if child.stdin is not None:
                            child.stdin.close()
                    deadline = time.monotonic() + float(timeout_seconds)
                    while True:
                        if abort_ev.is_set():
                            with contextlib.suppress(Exception):
                                child.kill()
                            return ExecutionResult(
                                exit_code=-1,
                                stderr="Aborted (session stop) — shell killed",
                                duration_ms=(time.monotonic() - start) * 1000,
                            )
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            with contextlib.suppress(Exception):
                                child.kill()
                            return ExecutionResult(
                                exit_code=-1,
                                stderr=(
                                    f"Command timed out after {timeout_seconds}s"
                                ),
                                duration_ms=(time.monotonic() - start) * 1000,
                            )
                        code = await asyncio.to_thread(
                            wait_piped, child, min(0.05, remaining)
                        )
                        if code is None:
                            continue

                        def _read(stream: Any) -> str:
                            if stream is None:
                                return ""
                            try:
                                data = stream.read()
                            except Exception:
                                return ""
                            return data if isinstance(data, str) else (
                                data.decode("utf-8", "replace") if data else ""
                            )

                        stdout = await asyncio.to_thread(_read, child.stdout)
                        stderr = await asyncio.to_thread(_read, child.stderr)
                        elapsed = (time.monotonic() - start) * 1000
                        if is_turn_aborted():
                            return ExecutionResult(
                                exit_code=-1,
                                stderr="Aborted (session stop) — shell killed",
                                duration_ms=elapsed,
                            )
                        return ExecutionResult(
                            exit_code=int(code),
                            stdout=_clip_output(stdout, "stdout"),
                            stderr=_clip_output(stderr, "stderr"),
                            duration_ms=elapsed,
                        )
                finally:
                    with contextlib.suppress(Exception):
                        child.close()

            try:
                completed = await run_hidden_async(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    cwd=cwd_s,
                    env=safe_env,
                )
            except subprocess.TimeoutExpired:
                elapsed = (time.monotonic() - start) * 1000
                if is_turn_aborted():
                    return ExecutionResult(
                        exit_code=-1,
                        stderr="Aborted (session stop) — shell killed",
                        duration_ms=elapsed,
                    )
                return ExecutionResult(
                    exit_code=-1,
                    stderr=f"Command timed out after {timeout_seconds}s",
                    duration_ms=elapsed,
                )
            elapsed = (time.monotonic() - start) * 1000
            if is_turn_aborted():
                return ExecutionResult(
                    exit_code=-1,
                    stderr="Aborted (session stop) — shell killed",
                    duration_ms=elapsed,
                )
            return ExecutionResult(
                exit_code=int(completed.returncode or 0),
                stdout=_clip_output(str(completed.stdout or ""), "stdout"),
                stderr=_clip_output(str(completed.stderr or ""), "stderr"),
                duration_ms=elapsed,
            )
        except (OSError, HostError, NativeRuntimeUnavailableError, ValueError) as e:
            elapsed = (time.monotonic() - start) * 1000
            err = (
                _spawn_error_stderr(command, e)
                if isinstance(e, OSError)
                else f"OS error: {e}"
            )
            return ExecutionResult(
                exit_code=-1,
                stderr=err,
                duration_ms=elapsed,
            )
