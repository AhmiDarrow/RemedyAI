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
from remedy.memory.consolidator import MemoryConsolidator
from remedy.memory.handoff import AutoHandoffManager
from remedy.memory.repair import MemoryRepair
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

    mem_consolidate = mem_sub.add_parser("consolidate", help="Consolidate memory entries")
    mem_consolidate.add_argument("session_id", help="Session ID to consolidate")
    mem_consolidate.add_argument("--max-entries", type=int, default=100)

    mem_repair = mem_sub.add_parser("repair", help="Run memory integrity checks")
    mem_repair.add_argument("--vacuum", action="store_true", help="Also vacuum database")

    mem_backup = mem_sub.add_parser("backup", help="Backup the memory database")

    # remedy user profile|facts
    user = sub.add_parser("user", help="User profile operations")
    user_sub = user.add_subparsers(dest="user_cmd")
    user_show = user_sub.add_parser("show", help="Show user profile")
    user_facts = user_sub.add_parser("facts", help="Search user facts")
    user_facts.add_argument("query", nargs="?", default="")
    user_facts.add_argument("--limit", type=int, default=10)

    # remedy session start|end
    session = sub.add_parser("session", help="Session management")
    session_sub = session.add_subparsers(dest="session_cmd")
    session_start = session_sub.add_parser("start", help="Start a new session")
    session_end = session_sub.add_parser("end", help="End current session")

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

        elif args.memory_cmd == "consolidate":
            consolidator = MemoryConsolidator(store)
            result = await consolidator.consolidate_session(args.session_id, max_entries=args.max_entries)
            if result:
                console.print(f"[green]Consolidated session {args.session_id}:[/green] {result.id}")
            else:
                console.print("[yellow]Not enough entries to consolidate.[/yellow]")

        elif args.memory_cmd == "repair":
            repair = MemoryRepair(store)
            info = await repair.check_integrity()
            console.print("[bold]Memory Store Integrity[/bold]")
            for k, v in info.items():
                console.print(f"  {k}: {v}")
            if args.vacuum:
                vacuum_result = await repair.vacuum()
                console.print(f"\n[green]Vacuumed:[/green] reclaimed {vacuum_result['reclaimed_bytes']} bytes")

        elif args.memory_cmd == "backup":
            repair = MemoryRepair(store)
            backup_path = await repair.backup()
            console.print(f"[green]Backup created:[/green] {backup_path}")


async def _cmd_user(args, db_path: Path) -> None:
    async with MemoryStore(db_path) as store:
        if args.user_cmd == "show":
            profile = await store.get_or_create_profile()
            console.print(Panel(
                f"[bold]User: {profile.display_name or profile.user_id}[/bold]\n"
                f"Sessions: {profile.stats['sessions_count']}\n"
                f"Active since: {profile.created_at.isoformat()}\n"
                f"Last active: {profile.last_active.isoformat()}\n\n"
                f"[bold]Traits:[/bold]\n" +
                "\n".join(f"  {k}: {v.value} (confidence: {v.confidence:.1f})" for k, v in profile.traits.items())
                + "\n\n" +
                f"[bold]Facts ({len(profile.facts)}):[/bold]\n" +
                "\n".join(f"  [{f.category}] {f.fact}" for f in profile.facts[:10]),
                title="User Profile",
            ))
            if len(profile.facts) > 10:
                console.print(f"[dim]  ... and {len(profile.facts) - 10} more facts[/dim]")

        elif args.user_cmd == "facts":
            facts = await store.search_user_facts(args.query, limit=args.limit)
            if facts:
                for f in facts:
                    console.print(f"  [{f['category']}] {f['fact']} (ref: {f['reference_count']})")
            else:
                console.print("[dim]No facts found.[/dim]")


async def _cmd_session(args, db_path: Path) -> None:
    from remedy.core.runtime import AgentRuntime
    config = AgentConfig(
        memory_db_path=str(db_path),
        home_dir=str(db_path.parent),
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    if args.session_cmd == "start":
        sid = await runtime.start_session()
        console.print(f"[green]Session started:[/green] {sid}")

        pending = await runtime.handoff.get_pending_handoffs()
        if pending:
            console.print(f"[yellow]{len(pending)} pending handoff(s) from previous sessions:[/yellow]")
            for h in pending:
                console.print(f"  {h.title}: {h.content[:80]}...")

    elif args.session_cmd == "end":
        handoff = await runtime.end_session()
        if handoff:
            console.print(f"[green]Session ended. Handoff created:[/green] {handoff.id}")
            console.print(Panel(
                f"[bold]{handoff.title}[/bold]\n{handoff.content[:300]}",
                title="Auto-Handoff",
            ))
        else:
            console.print("[dim]No active session to end.[/dim]")

    await runtime.stop()


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
    elif parsed.command == "user":
        asyncio.run(_cmd_user(parsed, db_path))
    elif parsed.command == "session":
        asyncio.run(_cmd_session(parsed, db_path))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
