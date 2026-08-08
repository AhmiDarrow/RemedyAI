"""Shared CLI helpers (console, paths, pretty-print)."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedy.models import HandoffNote, MemoryEntry
from remedy.skills.registry import SkillRegistry

console = Console()


def _get_db_path(home: str) -> Path:
    p = Path(home).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p / "memory.db"


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

