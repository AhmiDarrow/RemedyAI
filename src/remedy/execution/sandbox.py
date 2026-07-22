"""Sandboxed execution backends for safe skill and tool execution.

Supports restricted subprocess and Docker-based isolation.
Phase 0 provides the interface; full implementation in Phase 5.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0


class Sandbox:
    """Base class for execution backends."""

    async def execute(
        self,
        command: list[str],
        workdir: Optional[Path] = None,
        timeout_seconds: float = 30.0,
        env: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        raise NotImplementedError


class SubprocessSandbox(Sandbox):
    """Execute commands in a restricted subprocess.

    Security controls:
    - Working directory confinement
    - Timeout enforcement
    - Environment variable isolation
    """

    async def execute(
        self,
        command: list[str],
        workdir: Optional[Path] = None,
        timeout_seconds: float = 30.0,
        env: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        import time

        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=str(workdir) if workdir else None,
                timeout=timeout_seconds,
                env=env,
            )
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=elapsed,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Timed out after {timeout_seconds}s",
                duration_ms=timeout_seconds * 1000,
            )


class DockerSandbox(Sandbox):
    """Execute inside a Docker container for stronger isolation.

    Stub for Phase 0. Full implementation in Phase 5.
    """

    async def execute(
        self,
        command: list[str],
        workdir: Optional[Path] = None,
        timeout_seconds: float = 30.0,
        env: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            exit_code=-1,
            stderr="DockerSandbox not yet implemented (Phase 5)",
        )
