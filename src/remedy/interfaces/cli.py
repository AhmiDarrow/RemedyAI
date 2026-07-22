"""Remedy CLI -- the primary command-line interface.

Provides the `remedy` command with subcommands for memory, skills,
handoff, and interactive sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from remedy import __version__
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
)
from remedy.skills.loader import load_skill_from_dir
from remedy.skills.registry import SkillRegistry

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remedy",
        description="Remedy: The self-improving, multi-channel AI agent framework.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"remedy {__version__}")
    parser.add_argument(
        "--home",
        default="~/.remedy",
        help="Remedy home directory (default: ~/.remedy)",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # remedy memory search <query>
    mem = sub.add_parser("memory", help="Memory operations")
    mem_sub = mem.add_subparsers(dest="memory_cmd")
    mem_search = mem_sub.add_parser("search", help="Search memory")
    mem_search.add_argument("query", help="Search query")
    mem_search.add_argument("--limit", type=int, default=10)

    mem_list = mem_sub.add_parser("list", help="List recent memories")
    mem_list.add_argument("--limit", type=int, default=20)
    mem_list.add_argument("--type", dest="entry_type", default=None)

    mem_add = mem_sub.add_parser("add", help="Add a memory entry")
    mem_add.add_argument("title", help="Entry title")
    mem_add.add_argument("content", help="Entry content")
    mem_add.add_argument("--type", dest="entry_type", default="note")
    mem_add.add_argument("--tags", default="")
    mem_add.add_argument("--importance", type=float, default=0.5)

    # remedy skill discover <path>
    skill = sub.add_parser("skill", help="Skill operations")
    skill_sub = skill.add_subparsers(dest="skill_cmd")
    skill_list = skill_sub.add_parser("list", help="List registered skills")
    skill_discover = skill_sub.add_parser("discover", help="Discover skills in a directory")
    skill_discover.add_argument("path", help="Directory to scan")
    skill_discover.add_argument("--no-recurse", action="store_true")
    skill_info = skill_sub.add_parser("info", help="Show skill details")
    skill_info.add_argument("name", help="Skill name")
    skill_load = skill_sub.add_parser("load", help="Load a single skill")
    skill_load.add_argument("path", help="Path to skill directory or SKILL.md")

    # remedy handoff create ...
    handoff = sub.add_parser("handoff", help="Handoff note operations")
    handoff_sub = handoff.add_subparsers(dest="handoff_cmd")
    ho_create = handoff_sub.add_parser("create", help="Create a handoff note")
    ho_create.add_argument("title", help="Note title")
    ho_create.add_argument("content", help="Note content")
    ho_create.add_argument("--tags", default="")
    ho_list = handoff_sub.add_parser("list", help="List handoff notes")
    ho_list.add_argument("--limit", type=int, default=20)
    ho_search = handoff_sub.add_parser("search", help="Search handoffs")
    ho_search.add_argument("query", help="Search query")
    ho_search.add_argument("--limit", type=int, default=10)
    ho_show = handoff_sub.add_parser("show", help="Show a handoff note")
    ho_show.add_argument("id", help="Handoff note ID")

    # remedy migrate hermes <path>
    migrate = sub.add_parser("migrate", help="Migration operations")
    migrate_sub = migrate.add_subparsers(dest="migrate_cmd")
    mig_h = migrate_sub.add_parser("hermes", help="Migrate from Hermes Agent")
    mig_h.add_argument("path", help="Path to Hermes skills directory")
    mig_h.add_argument("--no-copy", action="store_true")
    mig_oc = migrate_sub.add_parser("openclaw", help="Migrate from OpenClaw")
    mig_oc.add_argument("path", help="Path to OpenClaw skills directory")
    mig_oc.add_argument("--no-copy", action="store_true")

    return parser


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
                title=f"Memory Entry",
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


async def _cmd_memory(args, db_path: Path) -> None:
    async with MemoryStore(db_path) as store:
        if args.memory_cmd == "search":
            entries = await store.search(args.query, limit=args.limit)
            _print_memory_entries(entries)

        elif args.memory_cmd == "list":
            if args.entry_type:
                entries = await store.list_by_type(
                    MemoryEntryType(args.entry_type), limit=args.limit
                )
            else:
                entries = await store.list_recent(limit=args.limit)
            _print_memory_entries(entries)

        elif args.memory_cmd == "add":
            entry = MemoryEntry(
                title=args.title,
                content=args.content,
                entry_type=MemoryEntryType(args.entry_type),
                tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                importance=args.importance,
            )
            await store.upsert(entry)
            console.print(f"[green]Memory entry saved:[/green] {entry.id}")


async def _cmd_skill(args) -> None:
    registry = SkillRegistry()

    if args.skill_cmd == "list":
        pass
    elif args.skill_cmd == "discover":
        count = registry.discover(args.path, recurse=not args.no_recurse)
        console.print(f"[green]Discovered {count} skill(s) from {args.path}[/green]")
    elif args.skill_cmd == "info":
        skill = registry.get(args.name)
        if skill is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            console.print("Run 'remedy skill discover <path>' first.")
            return
        m = skill.manifest
        console.print(Panel(
            f"[bold]{m.name}[/bold] v{m.version}\n"
            f"{m.description}\n\n"
            f"[dim]Kind: {m.kind.value} | Status: {m.status.value}[/dim]\n"
            f"[dim]Tags: {', '.join(m.tags) if m.tags else 'none'}[/dim]\n"
            f"[dim]Path: {m.path}[/dim]",
            title="Skill Info",
        ))
        if skill.instructions:
            console.print("\n[bold]Instructions:[/bold]")
            console.print(skill.instructions[:500])
    elif args.skill_cmd == "load":
        skill = registry.load_single(args.path)
        console.print(f"[green]Loaded:[/green] {skill.manifest.name} v{skill.manifest.version}")

    if args.skill_cmd in ("list", "discover", "load"):
        _print_skills(registry)


async def _cmd_handoff(args, db_path: Path) -> None:
    async with MemoryStore(db_path) as store:
        if args.handoff_cmd == "create":
            note = HandoffNote(
                title=args.title,
                content=args.content,
                tags=[t.strip() for t in args.tags.split(",") if t.strip()],
            )
            await store.create_handoff(note)
            console.print(f"[green]Handoff created:[/green] {note.id}")

        elif args.handoff_cmd == "list":
            notes = await store.list_handoffs(limit=args.limit)
            _print_handoffs(notes)

        elif args.handoff_cmd == "search":
            notes = await store.get_relevant_handoffs(args.query, limit=args.limit)
            _print_handoffs(notes)

        elif args.handoff_cmd == "show":
            note = await store.get_handoff(args.id)
            if note is None:
                console.print(f"[red]Handoff not found: {args.id}[/red]")
                return
            console.print_json(json.dumps(note.model_dump(mode="json"), default=str))


async def _cmd_migrate(args) -> None:
    registry = SkillRegistry()
    skills_dir = Path(args.home).expanduser() / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    if args.migrate_cmd == "hermes":
        from remedy.migrate.from_hermes import migrate_from_hermes

        result = migrate_from_hermes(
            registry,
            args.path,
            copy_to_remedy=not args.no_copy,
            remedy_skills_dir=skills_dir,
        )
        console.print(
            f"[green]Hermes migration: {result.skills_imported} imported, "
            f"{result.skills_skipped} skipped[/green]"
        )

    elif args.migrate_cmd == "openclaw":
        from remedy.migrate.from_hermes import migrate_from_openclaw as migrate_from_oc

        result = migrate_from_oc(
            registry,
            args.path,
            copy_to_remedy=not args.no_copy,
            remedy_skills_dir=skills_dir,
        )
        console.print(
            f"[green]OpenClaw migration: {result.skills_imported} imported, "
            f"{result.skills_skipped} skipped[/green]"
        )

    if result.errors:
        for err in result.errors:
            console.print(f"[red]  Error: {err}[/red]")


def main(args: Optional[list[str]] = None) -> None:
    parser = build_parser()
    parsed = parser.parse_args(args)

    if parsed.command is None:
        parser.print_help()
        return

    db_path = _get_db_path(parsed.home)

    if parsed.command == "memory":
        asyncio.run(_cmd_memory(parsed, db_path))
    elif parsed.command == "skill":
        asyncio.run(_cmd_skill(parsed))
    elif parsed.command == "handoff":
        asyncio.run(_cmd_handoff(parsed, db_path))
    elif parsed.command == "migrate":
        asyncio.run(_cmd_migrate(parsed))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
