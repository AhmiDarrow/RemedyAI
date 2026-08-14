"""Shared CLI helpers (console, paths, pretty-print, safety)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedy.models import HandoffNote, MemoryEntry
from remedy.skills.registry import SkillRegistry

console = Console()

# First path component after the drive / FS root that must never be --home.
_BLOCKED_HOME_ROOTS = frozenset(
    {
        "windows",
        "system32",
        "syswow64",
        "program files",
        "program files (x86)",
        "programdata",
        "etc",
        "usr",
        "bin",
        "sbin",
        "boot",
        "proc",
        "sys",
        "dev",
    }
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_BIND_ALL_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Keys whose values must never be printed by `config show` / similar dumps.
_CLI_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|"
    r"private[_-]?key|bot[_-]?token)$",
    re.IGNORECASE,
)


def insecure_bind_allowed(env: dict[str, str] | None = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get("REMEDY_ALLOW_INSECURE_BIND", "")).strip().lower() in _TRUTHY


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def is_bind_all_host(host: str) -> bool:
    return (host or "").strip().lower() in _BIND_ALL_HOSTS


def evaluate_serve_bind(
    host: str,
    *,
    has_auth: bool,
    insecure_ok: bool | None = None,
) -> str:
    """Classify a ``remedy serve --host`` bind.

    Returns ``ok`` (loopback, or operator opted in), ``warn`` (LAN bind with
    auth), or ``refuse`` (LAN / wildcard bind with auth off and no flag).
    """
    h = (host or "127.0.0.1").strip().lower()
    if h in _LOOPBACK_HOSTS:
        return "ok"
    allow = insecure_bind_allowed() if insecure_ok is None else bool(insecure_ok)
    exposed = h in _BIND_ALL_HOSTS or h not in _LOOPBACK_HOSTS
    if not exposed:
        return "ok"
    if not has_auth and not allow:
        return "refuse"
    if not allow:
        return "warn"
    return "ok"


class UnsafeHomeError(ValueError):
    """``--home`` pointed at a drive root or OS directory."""


def resolve_cli_home(home: str | os.PathLike[str] | None, *, mkdir: bool = True) -> Path:
    """Expand, resolve, and jail ``--home`` away from OS trees.

    Tests and operators may point at any non-system directory (including
    ``tmp_path/.remedy``). Drive roots and Windows / POSIX system prefixes
    are refused so a typo cannot mkdir or wipe under ``C:\\Windows``.
    """
    raw = str(home or "~/.remedy").strip() or "~/.remedy"
    p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p.absolute()
    if resolved.exists() and resolved.is_file():
        raise UnsafeHomeError(f"--home is a file, not a directory: {resolved}")
    if resolved.parent == resolved:
        raise UnsafeHomeError(f"--home cannot be a drive or filesystem root: {resolved}")
    parts = [str(part).lower() for part in resolved.parts]
    rest = parts[1:] if parts else []
    if not rest or rest[0] in _BLOCKED_HOME_ROOTS:
        raise UnsafeHomeError(f"--home refuses system path: {resolved}")
    if mkdir:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _get_db_path(home: str) -> Path:
    return resolve_cli_home(home) / "memory.db"


def redact_cli_mapping(obj: Any, *, depth: int = 0) -> Any:
    """Deep-redact secret-shaped keys for CLI dumps (config show, etc.)."""
    if depth > 8:
        return "[redacted-depth]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower().replace("-", "_")
            if (
                _CLI_SECRET_KEY_RE.search(kl)
                or "api_key" in kl
                or kl in {"provider_keys", "secrets", "credentials"}
            ):
                out[k] = "[redacted]" if v not in (None, "", []) else v
            else:
                out[k] = redact_cli_mapping(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [redact_cli_mapping(x, depth=depth + 1) for x in obj[:80]]
    return obj


def _print_skills(registry: SkillRegistry) -> None:
    if registry.count == 0:
        console.print("[dim]No skills registered.[/dim]")
        return

    table = Table(title="Registered Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Description")

    for skill in registry.skills:
        m = skill.manifest
        table.add_row(
            m.name,
            m.version,
            m.kind.value,
            m.status.value,
            m.description[:60] + ("..." if len(m.description) > 60 else ""),
        )
    console.print(table)


def _print_memory_entries(entries: list[MemoryEntry]) -> None:
    if not entries:
        console.print("[dim]No entries found.[/dim]")
        return

    for entry in entries:
        console.print(
            Panel(
                f"[bold cyan]{entry.title}[/bold cyan]\n"
                f"{entry.content[:200]}{'...' if len(entry.content) > 200 else ''}\n\n"
                f"[dim]ID: {entry.id} | Type: {entry.entry_type.value} | "
                f"Importance: {entry.importance:.1f} | {entry.created_at.isoformat()}[/dim]",
                title="Memory Entry",
            )
        )


def _print_handoffs(handoffs: list[HandoffNote]) -> None:
    if not handoffs:
        console.print("[dim]No handoff notes found.[/dim]")
        return

    for h in handoffs:
        ack = "[green]acknowledged[/green]" if h.acknowledged else "[yellow]pending[/yellow]"
        console.print(
            Panel(
                f"[bold cyan]{h.title}[/bold cyan]\n"
                f"{h.content[:300]}{'...' if len(h.content) > 300 else ''}\n\n"
                f"[dim]ID: {h.id} | {ack} | {h.created_at.isoformat()}[/dim]",
                title="Handoff Note",
            )
        )


def _print_exec_result(result) -> None:
    status = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"
    console.print(f"  Status: {status}")
    console.print(f"  Exit code: {result.exit_code}")
    if result.stdout:
        console.print(f"  stdout: {result.stdout[:200]}")
    if result.stderr:
        console.print(f"  [yellow]stderr: {result.stderr[:200]}[/yellow]")
    if result.error:
        console.print(f"  [red]Error: {result.error}[/red]")

