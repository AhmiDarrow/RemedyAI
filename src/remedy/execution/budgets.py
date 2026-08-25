"""Per-turn execution budgets. Soft limits until OS enforcement lands (M1.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


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
