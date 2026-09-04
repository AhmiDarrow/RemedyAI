"""Thin hop/abort orchestrator — Zig write-jail owns spawn scrub.

``SubprocessSandbox`` expands shell chains and runs hidden children with
abort/timeout. Workdir / write-root / auth-path gates go through
``host_binding.write_jail_*`` (no Python path-jail twin, no soft fallback).
Env scrub lives in ``remedy.execution.env``; Docker in ``remedy.execution.docker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from remedy.execution.host.runner import ChainHop

from remedy.core.security import check_dangerous_command
from remedy.execution.env import (
    allowed_paths_for_shell,
    run_unattended_git,
    scrub_subprocess_env,
    unattended_vcs_env,
)
from remedy.execution.result import ExecutionResult, Sandbox

__all__ = [
    "ExecutionResult",
    "Sandbox",
    "SubprocessSandbox",
    "allowed_paths_for_shell",
    "run_unattended_git",
    "scrub_subprocess_env",
    "unattended_vcs_env",
]


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
    """Zig write-jail path gate for cd/mkdir hops."""
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
    """Execute argv under Zig write-jail with hop chain + abort."""

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
        if workdir is not None:
            denied = _check_path_jail(workdir, None, start=start, what="workdir")
            if denied is not None:
                return denied

        from remedy.execution.host.runner import expand_shell_chain

        hops = expand_shell_chain(
            list(command),
            project_path=workdir,
        )
        if hops and len(hops) >= 2:
            return await self._execute_shell_chain(
                hops,
                workdir=workdir,
                timeout_seconds=timeout_seconds,
                env=env,
                start=start,
            )
        return await self._execute_one(
            list(command),
            workdir=workdir,
            timeout_seconds=timeout_seconds,
            env=env,
            start=start,
        )

    def _resolve_path(self, cwd: Path | None, raw: str) -> Path:
        from remedy.core.workspace import resolve_existing_path

        return resolve_existing_path(raw, cwd=cwd)

    async def _execute_shell_chain(
        self,
        hops: list[ChainHop],
        *,
        workdir: Path | None,
        timeout_seconds: float,
        env: dict[str, str] | None,
        start: float,
    ) -> ExecutionResult:
        """Run ``cd``/``mkdir``/process hops without cmd.exe (no inherited console)."""
        from remedy.core.turn_context import is_turn_aborted
        from remedy.execution.host.runner import ChainHop

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        last = ExecutionResult(exit_code=0)
        remaining = float(timeout_seconds)
        cwd: Path | None = Path(workdir) if workdir is not None else None
        for hop in hops:
            if not isinstance(hop, ChainHop):
                return ExecutionResult(
                    exit_code=-1,
                    stderr="internal: malformed shell chain",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            if is_turn_aborted():
                return ExecutionResult(
                    exit_code=-1,
                    stderr="Aborted before start (session stop)",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            if remaining <= 0.05:
                return ExecutionResult(
                    exit_code=-1,
                    stdout=_clip_output("\n".join(stdout_parts), "stdout"),
                    stderr=_clip_output(
                        "\n".join(
                            [*stderr_parts, f"Command timed out after {timeout_seconds}s"]
                        ),
                        "stderr",
                    ),
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            hop_start = time.monotonic()
            if hop.kind == "cd":
                raw = hop.paths[0] if hop.paths else ""
                target = self._resolve_path(cwd, raw)
                denied = _check_path_jail(target, cwd, start=start, what=f"cd {raw}")
                if denied is not None:
                    return denied
                if not target.is_dir():
                    return ExecutionResult(
                        exit_code=1,
                        stderr=f"cd: {target} is not a directory",
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                cwd = target
                remaining -= max(0.0, (time.monotonic() - hop_start))
                last = ExecutionResult(exit_code=0)
                continue
            if hop.kind == "mkdir":
                try:
                    for p in hop.paths:
                        dest = self._resolve_path(cwd, p)
                        denied = _check_path_jail(
                            dest, cwd, start=start, what=f"mkdir {p}"
                        )
                        if denied is not None:
                            return denied
                        dest.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    return ExecutionResult(
                        exit_code=1,
                        stderr=f"mkdir failed: {exc}",
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                remaining -= max(0.0, (time.monotonic() - hop_start))
                last = ExecutionResult(exit_code=0)
                continue
            argv = list(hop.argv)
            hop_danger = check_dangerous_command(argv)
            if hop_danger:
                return ExecutionResult(
                    exit_code=-1,
                    stderr=f"Blocked by security policy: {hop_danger}",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            last = await self._execute_one(
                argv,
                workdir=cwd,
                timeout_seconds=remaining,
                env=env,
                start=time.monotonic(),
            )
            if last.stdout:
                stdout_parts.append(last.stdout)
            if last.stderr:
                stderr_parts.append(last.stderr)
            remaining -= max(0.0, last.duration_ms / 1000.0)
            if last.exit_code != 0:
                break
        return ExecutionResult(
            exit_code=last.exit_code,
            stdout=_clip_output("\n".join(stdout_parts), "stdout"),
            stderr=_clip_output("\n".join(stderr_parts), "stderr"),
            duration_ms=(time.monotonic() - start) * 1000,
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
            from remedy.core.turn_context import (
                current_abort_event,
                is_turn_aborted,
                register_turn_process,
                unregister_turn_process,
            )
            from remedy.execution.process import (
                create_hidden_subprocess_exec,
            )

            if is_turn_aborted():
                return ExecutionResult(
                    exit_code=-1,
                    stderr="Aborted before start (session stop)",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            proc = await create_hidden_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(workdir) if workdir else None,
                env=safe_env,
            )
            register_turn_process(proc)

            try:
                abort_ev = current_abort_event()
                try:
                    loop = asyncio.get_running_loop()
                    ev_loop = getattr(abort_ev, "_loop", None)
                    if ev_loop is not None and ev_loop is not loop:
                        abort_ev = None
                except Exception:
                    abort_ev = None
                stdout, stderr = await _communicate_or_abort(
                    proc,
                    timeout_seconds=timeout_seconds,
                    abort_event=abort_ev,
                )
                if stdout is None and stderr is None:
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
                return ExecutionResult(
                    exit_code=proc.returncode or 0,
                    stdout=_clip_output(
                        stdout.decode("utf-8", errors="replace") if stdout else "",
                        "stdout",
                    ),
                    stderr=_clip_output(
                        stderr.decode("utf-8", errors="replace") if stderr else "",
                        "stderr",
                    ),
                    duration_ms=elapsed,
                )
            finally:
                unregister_turn_process(proc)
        except OSError as e:
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(
                exit_code=-1,
                stderr=_spawn_error_stderr(command, e),
                duration_ms=elapsed,
            )


async def _cancel_waiters(*tasks: asyncio.Task[Any] | None) -> None:
    """Cancel leftover waiters and await them so they are not destroyed pending."""
    for t in tasks:
        if t is None:
            continue
        if not t.done():
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


async def _communicate_or_abort(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    abort_event: asyncio.Event | None,
) -> tuple[bytes | None, bytes | None]:
    """Wait for process I/O, abort event, or timeout. Returns (None, None) if killed.

    Every waiter this function creates must be awaited. Cancelling
    ``_wait_abort`` without awaiting it logs ``Task was destroyed but it
    is pending`` on Windows when a Stop or timeout races communicate().
    """
    from remedy.execution.process import kill_process_tree

    comm = asyncio.create_task(proc.communicate())
    waiters: set[asyncio.Task[Any]] = {comm}
    abort_task: asyncio.Task[Any] | None = None
    if abort_event is not None:

        async def _wait_abort() -> None:
            await abort_event.wait()

        abort_task = asyncio.create_task(_wait_abort())
        waiters.add(abort_task)

    done, pending = await asyncio.wait(
        waiters,
        timeout=max(0.05, float(timeout_seconds)),
        return_when=asyncio.FIRST_COMPLETED,
    )

    if not done:
        kill_process_tree(proc)
        await _cancel_waiters(*pending)
        return None, None

    if abort_task is not None and abort_task in done and not comm.done():
        kill_process_tree(proc)
        await _cancel_waiters(comm)
        return None, None

    await _cancel_waiters(abort_task)
    try:
        return await comm
    except Exception:
        kill_process_tree(proc)
        return None, None
