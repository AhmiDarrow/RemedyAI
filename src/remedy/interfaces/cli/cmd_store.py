"""CLI: memory / user / session / handoff / migrate."""

from __future__ import annotations

from pathlib import Path

from rich.panel import Panel

from remedy.interfaces.cli.util import (
    _print_handoffs,
    _print_memory_entries,
    console,
)
from remedy.memory.consolidator import MemoryConsolidator
from remedy.memory.repair import MemoryRepair
from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType

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
    from remedy.core.agent import BasicRuntime
    config = AgentConfig(
        memory_db_path=str(db_path),
        home_dir=str(db_path.parent),
    )
    runtime = BasicRuntime(config)
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
    result = None

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
    else:
        console.print(f"[red]Unknown migrate command: {args.migrate_cmd}[/red]")
        return

    if result is not None and result.errors:
        for err in result.errors:
            console.print(f"[red]  Error: {err}[/red]")



