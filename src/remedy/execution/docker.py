"""Docker sandbox — container-based execution isolation.

Spawns temporary Docker containers for secure tool execution.
Supports volume mounts, network control, resource limits,
and image caching.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from remedy.execution.sandbox import ExecutionResult, Sandbox


class DockerSandbox(Sandbox):
    """Execute commands inside ephemeral Docker containers.

    Security features:
    - Isolated filesystem (scratch container)
    - Network control (none, bridge, host)
    - CPU/memory limits
    - Read-only volume mounts
    - Automatic cleanup on exit
    - Image pull + caching

    Requires Docker daemon running on the host.
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        network: str = "none",
        memory_limit: str = "256m",
        cpu_limit: str = "1.0",
        read_only: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.image = image
        self.network = network
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.read_only = read_only
        self.timeout = timeout
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        """Check if Docker is actually available on this host."""
        if self._available is None:
            self._available = shutil.which("docker") is not None
        return self._available

    async def ensure_image(self) -> bool:
        """Pull the image if not already present. Returns True if ready."""
        if not self.available:
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", self.image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode == 0:
                return True

            proc = await asyncio.create_subprocess_exec(
                "docker", "pull", self.image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    async def execute(
        self,
        command: list[str],
        workdir: Path | None = None,
        timeout_seconds: float = 30.0,
        env: dict[str, str] | None = None,
        mounts: list[tuple[str, str]] | None = None,
    ) -> ExecutionResult:
        if not self.available:
            return ExecutionResult(
                exit_code=-1,
                stderr="Docker is not available on this host",
            )

        import time
        start = time.monotonic()

        # Build docker command
        docker_cmd = ["docker", "run", "--rm"]

        if self.network:
            docker_cmd += ["--network", self.network]
        if self.memory_limit:
            docker_cmd += ["--memory", self.memory_limit]
        if self.cpu_limit:
            docker_cmd += ["--cpus", self.cpu_limit]
        if self.read_only:
            docker_cmd.append("--read-only")

        # Mount temporary workspace
        tmpdir = tempfile.mkdtemp(prefix="remedy_docker_")
        docker_cmd += ["-v", f"{tmpdir}:/workspace"]
        docker_cmd += ["-w", "/workspace"]

        # User-provided mounts
        for host_path, container_path in (mounts or []):
            docker_cmd += ["-v", f"{host_path}:{container_path}"]

        # Environment
        for k, v in (env or {}).items():
            docker_cmd += ["-e", f"{k}={v}"]

        # Image + command
        docker_cmd.append(self.image)
        docker_cmd += command

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                proc.kill()
                elapsed = (time.monotonic() - start) * 1000
                return ExecutionResult(
                    exit_code=-1,
                    stderr=f"Container timed out after {timeout_seconds}s",
                    duration_ms=elapsed,
                )

        except FileNotFoundError:
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(
                exit_code=-1,
                stderr="Docker executable not found",
                duration_ms=elapsed,
            )
        finally:
            # Cleanup temp dir
            import shutil
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

        elapsed = (time.monotonic() - start) * 1000

        return ExecutionResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
            stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
            duration_ms=elapsed,
        )

    async def sandbox_exists(self, name: str) -> bool:
        """Check if a sandbox label still exists."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a", "--filter", f"label=remedy.sandbox={name}",
            "--format", "{{.ID}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return bool(stdout.strip())

    async def cleanup(self) -> None:
        """Remove all stopped Remedy sandbox containers."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "container", "prune", "-f",
                "--filter", "label=remedy.sandbox",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
        except Exception:
            pass
