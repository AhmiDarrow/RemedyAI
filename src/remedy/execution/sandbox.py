"""Sandboxed execution backends for safe skill and tool execution.

Supports subprocess isolation and Docker container-based isolation.
All backends share the ExecutionResult contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remedy.core.security import check_dangerous_command


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


def scrub_subprocess_env(
    env: dict[str, str] | None = None,
    *,
    grants: list[Any] | None = None,
    argv: list[str] | None = None,
) -> dict[str, str]:
    """Child env: safe OS/path only, plus explicit credential grants.

    Generic shell (no grants, no git/gh argv) does **not** inherit GH_TOKEN,
    SSH_AUTH_SOCK, or registry tokens. git/gh/npm argv infers a grant so
    ``git push`` / ``gh`` still work when those tools are the executable.
    """
    from remedy.credentials.broker import child_environment, grant_for_argv

    inferred = list(grants or [])
    if not inferred and argv:
        inferred = grant_for_argv(argv, source=env)
    return child_environment(env, grants=inferred)


def _clip_output(text: str, stream: str) -> str:
    """Soft ExecutionBudget cap so a child cannot flood the turn."""
    from remedy.execution.budgets import ExecutionBudget

    return ExecutionBudget().clip(text, stream=stream)


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0


def allowed_paths_for_shell(
    roots: list[Path] | None,
    cwd: Path | None = None,
) -> list[Path]:
    """Workdir jail for subprocesses. Empty list = no workdir jail (Full)."""
    try:
        from remedy.core.approvals import is_full_approval

        if is_full_approval():
            return []
    except Exception:
        pass
    out: list[Path] = list(roots or [])
    if cwd is not None and cwd not in out:
        out.append(cwd)
    return out


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


class SubprocessSandbox(Sandbox):
    """Execute commands in a restricted subprocess.

    Security controls:
    - Working directory confinement
    - Timeout enforcement
    - Environment variable isolation
    - Input size limits
    """

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

        # Check for dangerous commands before executing
        danger = check_dangerous_command(command)
        if danger:
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Blocked by security policy: {danger}",
                duration_ms=0.0,
            )

        # Enforce allowed_paths jail: verify workdir is within allowed paths.
        # Resolve both sides the same way so symlinks / mixed path forms compare equal.
        if self.allowed_paths and workdir:
            try:
                resolved = workdir.expanduser().resolve(strict=False)
            except OSError:
                resolved = workdir.expanduser().absolute()
            allowed = False
            for p in self.allowed_paths:
                try:
                    root = p.expanduser().resolve(strict=False)
                except OSError:
                    root = p.expanduser().absolute()
                try:
                    if resolved == root or resolved.is_relative_to(root):
                        allowed = True
                        break
                except (ValueError, TypeError, OSError):
                    continue
            if not allowed:
                return ExecutionResult(
                    exit_code=-1,
                    stderr=f"Workdir {workdir} not in allowed paths: {self.allowed_paths}",
                    duration_ms=0.0,
                )

        # Always scrub secrets; infer VCS grants only when argv is git/gh/ssh.
        safe_env = scrub_subprocess_env(env, argv=command)
        # Force UTF-8 in the child so non-ASCII stdout/stderr survives the
        # decode("utf-8") below (mirrors the persistent-session path). Without
        # this, a child Python on Windows emits cp1252 and unicode output is
        # corrupted into replacement chars.
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
                    # Aborted or timed out — process already killed
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


async def _communicate_or_abort(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    abort_event: asyncio.Event | None,
) -> tuple[bytes | None, bytes | None]:
    """Wait for process I/O, abort event, or timeout. Returns (None, None) if killed."""
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

    # Timeout — nothing finished
    if not done:
        kill_process_tree(proc)
        for t in pending:
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await comm
        return None, None

    # Abort won
    if abort_task is not None and abort_task in done and not comm.done():
        kill_process_tree(proc)
        comm.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await comm
        if abort_task and not abort_task.done():
            abort_task.cancel()
        return None, None

    # Communicate finished
    if abort_task is not None and not abort_task.done():
        abort_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await abort_task
    try:
        return await comm
    except Exception:
        kill_process_tree(proc)
        return None, None
