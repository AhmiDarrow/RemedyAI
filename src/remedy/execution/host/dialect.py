"""Last-good host dialect — thin binding over Zig ``remedy_core``.

Authority lives in ``native/zig/src/host_dialect.zig``. No Python twin.
Persists under ``~/.remedy/host/dialect.json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HostDialect:
    host: str = "cmd"
    python_cmd: str = ""
    git_cmd: str = ""
    rg_cmd: str = ""
    curl_kind: str = ""  # real | missing
    pwsh_cmd: str = ""
    last_good_verify: str = ""
    successes: int = 0
    last_success_at: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostDialect:
        if not isinstance(data, dict):
            return cls()
        notes = data.get("notes") or []
        if not isinstance(notes, list):
            notes = []
        return cls(
            host=str(data.get("host") or "cmd"),
            python_cmd=str(data.get("python_cmd") or ""),
            git_cmd=str(data.get("git_cmd") or ""),
            rg_cmd=str(data.get("rg_cmd") or ""),
            curl_kind=str(data.get("curl_kind") or ""),
            pwsh_cmd=str(data.get("pwsh_cmd") or ""),
            last_good_verify=str(data.get("last_good_verify") or ""),
            successes=int(data.get("successes") or 0),
            last_success_at=str(data.get("last_success_at") or ""),
            notes=[str(n) for n in notes][:12],
        )


def _home_str(home: str | Path | None) -> str:
    if home:
        return str(Path(home).expanduser())
    return ""


def dialect_path(home: str | Path | None = None) -> Path:
    base = Path(_home_str(home)) if home else None
    if base is None:
        try:
            from remedy.core.security import get_home_dir

            base = get_home_dir()
        except Exception:
            import os

            env = (os.environ.get("REMEDY_HOME") or "").strip()
            base = Path(env or "~/.remedy").expanduser()
    return base / "host" / "dialect.json"


def load_dialect(home: str | Path | None = None) -> HostDialect:
    from remedy.core.computer import host_binding

    return HostDialect.from_dict(host_binding.dialect_load(_home_str(home)))


def save_dialect(dialect: HostDialect, home: str | Path | None = None) -> Path:
    """Persist via Zig probe+record path: write through dialect_probe(persist).

    Prefer ``probe_host_dialect(persist=True)`` / ``record_success``. This keeps
    a Path-returning helper for tests that assert the file exists.
    """
    from remedy.core.atomic_json import write_json_atomic

    path = dialect_path(home)
    write_json_atomic(path, dialect.to_dict())
    return path


def probe_host_dialect(
    *,
    home: str | Path | None = None,
    persist: bool = False,
) -> HostDialect:
    """Cheap PATH probe — Zig owns which + sidecar rejection."""
    from remedy.core.computer import host_binding

    return HostDialect.from_dict(
        host_binding.dialect_probe(_home_str(home), persist=persist)
    )


def record_success(
    command: str,
    *,
    home: str | Path | None = None,
    note: str = "",
) -> HostDialect:
    from remedy.core.computer import host_binding

    return HostDialect.from_dict(
        host_binding.dialect_record_success(
            command, home=_home_str(home), note=note
        )
    )


def format_dialect_line(
    dialect: HostDialect | None = None, home: str | Path | None = None
) -> str:
    """One-line inject: this PC's host, not a tutorial."""
    from remedy.core.computer import host_binding

    return host_binding.dialect_format_line(
        _home_str(home),
        dialect.to_dict() if dialect is not None else None,
    )
