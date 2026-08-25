"""Per-turn execution budgets. Soft limits until OS enforcement lands (M1.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

_TRUNCATION_MARK = "\n…[truncated]"
_TRUNCATION_RESERVE = 32


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    wall_time: timedelta = timedelta(seconds=660)
    cpu_time: timedelta | None = None
    memory_bytes: int | None = None
    stdout_bytes: int = 8 * 1024 * 1024
    stderr_bytes: int = 2 * 1024 * 1024
    max_processes: int = 8
    max_disk_write_bytes: int | None = None
    network_bytes: int | None = None

    def limit_for(self, stream: str) -> int:
        if stream == "stderr":
            return int(self.stderr_bytes)
        return int(self.stdout_bytes)

    def clip(self, text: str, *, stream: str = "stdout") -> str:
        """Trim captured output so a child cannot flood the turn.

        CPU / memory / process caps stay advisory until the OS backend
        enforces them. Oversize stdout/stderr is clipped in-process.
        """
        limit = self.limit_for(stream)
        raw = text.encode("utf-8", errors="replace")
        if limit <= 0:
            return _TRUNCATION_MARK.lstrip("\n")
        if len(raw) <= limit:
            return text
        cut_at = max(0, limit - _TRUNCATION_RESERVE)
        cut = raw[:cut_at].decode("utf-8", errors="replace")
        return cut + _TRUNCATION_MARK

    def clamp_timeout(self, seconds: float) -> float:
        """Soft wall-clock cap. Does not kill the process by itself."""
        cap = float(self.wall_time.total_seconds())
        try:
            requested = float(seconds)
        except (TypeError, ValueError):
            return cap
        if requested <= 0:
            return cap
        return min(requested, cap)
