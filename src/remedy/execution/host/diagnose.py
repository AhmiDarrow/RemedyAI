"""Classify host/shell failures — thin binding over Zig ``remedy_core``.

Authority lives in ``native/zig/src/host_diagnose.zig``. No Python twin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HostDiagnosis:
    code: str
    message: str
    rewritten: str = ""
    hint: str = ""
    notes: list[str] = field(default_factory=list)

    def format_block(self) -> str:
        lines = [f"HOST_DIAG {self.code}", self.message]
        if self.rewritten:
            lines.append(f"rewritten: {self.rewritten}")
        if self.hint:
            lines.append(f"hint: {self.hint}")
        for n in self.notes:
            lines.append(f"note: {n}")
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostDiagnosis:
        if not isinstance(data, dict):
            return cls(code="HOST_OK", message="ok")
        notes = data.get("notes") or []
        if not isinstance(notes, list):
            notes = []
        return cls(
            code=str(data.get("code") or "HOST_OK"),
            message=str(data.get("message") or ""),
            rewritten=str(data.get("rewritten") or ""),
            hint=str(data.get("hint") or ""),
            notes=[str(n) for n in notes][:12],
        )


def diagnose_host_failure(
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    translated: str = "",
    timed_out: bool = False,
    host: str = "cmd",
) -> HostDiagnosis:
    """Return a classified diagnosis for a failed host command."""
    from remedy.core.computer import host_binding

    return HostDiagnosis.from_dict(
        host_binding.diagnose_host_failure(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            translated=translated,
            timed_out=timed_out,
            host=host,
        )
    )
