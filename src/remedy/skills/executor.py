"""Skill executor -- runs skill scripts, code blocks, and instructions.

Ties into the execution sandbox for safe subprocess management
and provides structured result reporting.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


@dataclass
class ExecutionResult:
    """Result of executing a skill script or code block."""

    run_id: UUID = field(default_factory=uuid4)
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: float = 0.0
    artifact_paths: list[Path] = field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None


class SkillExecutor:
    """Executes skill scripts and extracted code blocks.

    Supports:
    - Running scripts from a skill's `scripts/` directory
    - Extracting and running Python/bash code blocks from instructions
    - Running the full skill instruction set as a guided workflow
    """

    def __init__(self, sandbox_dir: Path | None = None) -> None:
        self.sandbox_dir = Path(sandbox_dir or tempfile.mkdtemp(prefix="remedy_exec_"))

    async def run_script(
        self,
        script_path: Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 300.0,
    ) -> ExecutionResult:
        """Execute a skill script via subprocess."""
        result = ExecutionResult(started_at=datetime.now(UTC))

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", str(script_path), *(args or []),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(script_path.parent),
            )
            result.started_at = datetime.now(UTC)

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                result.stdout = stdout.decode("utf-8", errors="replace")
                result.stderr = stderr.decode("utf-8", errors="replace")
                result.exit_code = proc.returncode or 0
                result.success = result.exit_code == 0
            except TimeoutError:
                proc.kill()
                result.error = f"Script timed out after {timeout}s"
                result.success = False

        except FileNotFoundError:
            result.error = f"Python executable not found when running {script_path}"
            result.success = False
        except Exception as e:
            result.error = str(e)
            result.success = False

        result.ended_at = datetime.now(UTC)
        if result.started_at:
            result.duration_ms = (
                result.ended_at - result.started_at
            ).total_seconds() * 1000

        return result

    async def run_instructions(
        self,
        instructions: str,
        skill_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> list[ExecutionResult]:
        """Parse a skill's instruction markdown and execute inline code blocks.

        Supports ```python, ```bash, and ```sh fenced blocks.
        Returns one result per block found.
        """
        blocks = self._extract_code_blocks(instructions)
        results: list[ExecutionResult] = []

        for lang, code in blocks:
            if lang in ("python", "py"):
                result = await self._run_python_block(code, skill_dir, env)
            elif lang in ("bash", "sh", "shell"):
                result = await self._run_bash_block(code, skill_dir, env)
            else:
                result = ExecutionResult(
                    success=True,
                    stdout=f"Skipped unsupported language: {lang}",
                )
            results.append(result)

        return results

    async def _run_python_block(
        self,
        code: str,
        skill_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Write a Python code block to a temp file and execute it."""
        tmp = self.sandbox_dir / f"exec_{uuid4().hex[:8]}.py"
        tmp.write_text(code, encoding="utf-8")
        cwd = str(skill_dir or self.sandbox_dir)
        result = await self.run_script(tmp, env=env)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return result

    async def _run_bash_block(
        self,
        code: str,
        skill_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a shell code block."""
        result = ExecutionResult(started_at=datetime.now(UTC))
        result.started_at = datetime.now(UTC)

        try:
            proc = await asyncio.create_subprocess_shell(
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_dir or self.sandbox_dir),
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120.0
                )
                result.stdout = stdout.decode("utf-8", errors="replace")
                result.stderr = stderr.decode("utf-8", errors="replace")
                result.exit_code = proc.returncode or 0
                result.success = result.exit_code == 0
            except TimeoutError:
                proc.kill()
                result.error = "Shell block timed out after 120s"
                result.success = False
        except Exception as e:
            result.error = str(e)
            result.success = False

        result.ended_at = datetime.now(UTC)
        if result.started_at:
            result.duration_ms = (
                result.ended_at - result.started_at
            ).total_seconds() * 1000
        return result

    def _extract_code_blocks(self, text: str) -> list[tuple[str, str]]:
        """Extract (language, code) pairs from markdown fenced blocks."""
        pattern = r"```(\w+)\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)
        return [(lang.strip().lower(), code.strip()) for lang, code in matches]

    async def run_all_scripts(
        self,
        scripts: list[str],
        base_dir: Path,
        env: dict[str, str] | None = None,
    ) -> dict[str, ExecutionResult]:
        """Run every script in a skill's scripts/ directory."""
        results: dict[str, ExecutionResult] = {}
        for script_rel in scripts:
            script_path = base_dir / script_rel
            if script_path.is_file():
                results[script_rel] = await self.run_script(script_path, env=env)
            else:
                results[script_rel] = ExecutionResult(
                    success=False,
                    error=f"Script not found: {script_rel}",
                )
        return results
