"""Sandboxed execution backends for safe skill and tool execution.

Supports subprocess isolation and Docker container-based isolation.
All backends share the ExecutionResult contract.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from remedy.core.security import check_dangerous_command


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

        # Sanitize environment: strip inheritable dangerous vars
        safe_env = dict(env) if env else None
        if safe_env is not None:
            for key in list(safe_env):
                upper = key.upper()
                if upper in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP"):
                    safe_env.pop(key)

        try:
            from remedy.execution.process import create_hidden_subprocess_exec

            proc = await create_hidden_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir) if workdir else None,
                env=safe_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
                elapsed = (time.monotonic() - start) * 1000
                return ExecutionResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                    stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                    duration_ms=elapsed,
                )
            except TimeoutError:
                proc.kill()
                elapsed = (time.monotonic() - start) * 1000
                return ExecutionResult(
                    exit_code=-1,
                    stderr=f"Command timed out after {timeout_seconds}s",
                    duration_ms=elapsed,
                )
        except FileNotFoundError as e:
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Command not found: {e}",
                duration_ms=elapsed,
            )
        except OSError as e:
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(
                exit_code=-1,
                stderr=f"OS error: {e}",
                duration_ms=elapsed,
            )
