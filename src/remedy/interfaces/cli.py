"""Remedy CLI -- the primary command-line interface.

Provides the `remedy` command with subcommands for memory, skills,
handoff, and interactive sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedy import __version__
from remedy.core.learning.reflection import ExecutionTrace, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.execution.runtime import ToolRuntime
from remedy.execution.sandbox import SubprocessSandbox
from remedy.gateway.cli import main_gateway
from remedy.interfaces.config import (
    config_to_agent_config,
    create_default_config,
    resolve_config,
)
from remedy.interfaces.uninstaller import run_uninstall
from remedy.interfaces.updater import run_update
from remedy.interfaces.wizard import run_wizard
from remedy.memory.consolidator import MemoryConsolidator
from remedy.memory.repair import MemoryRepair
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    ToolCall,
    ToolSource,
)
from remedy.skills.executor import SkillExecutor
from remedy.skills.exporter import SkillExporter
from remedy.skills.registry import SkillRegistry
from remedy.skills.tool_registry import ToolRegistry
from remedy.skills.validator import SkillValidator

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

    mem_sub.add_parser("backup", help="Backup the memory database")

    # remedy user profile|facts
    user = sub.add_parser("user", help="User profile operations")
    user_sub = user.add_subparsers(dest="user_cmd")
    user_sub.add_parser("show", help="Show user profile")
    user_facts = user_sub.add_parser("facts", help="Search user facts")
    user_facts.add_argument("query", nargs="?", default="")
    user_facts.add_argument("--limit", type=int, default=10)

    # remedy session start|end
    session = sub.add_parser("session", help="Session management")
    session_sub = session.add_subparsers(dest="session_cmd")
    session_sub.add_parser("start", help="Start a new session")
    session_sub.add_parser("end", help="End current session")

    # remedy skill discover <path>
    skill = sub.add_parser("skill", help="Skill operations")
    skill_sub = skill.add_subparsers(dest="skill_cmd")
    skill_sub.add_parser("list", help="List registered skills")
    skill_discover = skill_sub.add_parser("discover", help="Discover skills in a directory")
    skill_discover.add_argument("path", help="Directory to scan")
    skill_discover.add_argument("--no-recurse", action="store_true")
    skill_info = skill_sub.add_parser("info", help="Show skill details")
    skill_info.add_argument("name", help="Skill name")
    skill_load = skill_sub.add_parser("load", help="Load a single skill")
    skill_load.add_argument("path", help="Path to skill directory or SKILL.md")

    skill_run = skill_sub.add_parser("run", help="Run a skill's scripts")
    skill_run.add_argument("name", help="Skill name to run")
    skill_run.add_argument("--script", dest="script", default=None, help="Specific script to run")

    skill_test = skill_sub.add_parser("test", help="Validate and test a skill")
    skill_test.add_argument("name", help="Skill name to validate")

    skill_export = skill_sub.add_parser("export", help="Export a skill to another format")
    skill_export.add_argument("name", help="Skill name to export")
    skill_export.add_argument("output", help="Output directory")
    skill_export.add_argument("--format", dest="fmt", default="native",
                              choices=["native", "hermes", "openclaw", "zip"])

    # remedy tool list|search
    tool = sub.add_parser("tool", help="Tool operations")
    tool_sub = tool.add_subparsers(dest="tool_cmd")
    tool_sub.add_parser("list", help="List registered tools")
    tool_search = tool_sub.add_parser("search", help="Search tools")
    tool_search.add_argument("query", help="Search query")
    tool_sub.add_parser("stats", help="Tool invocation statistics")
    tool_run = tool_sub.add_parser("run", help="Execute a tool through the runtime")
    tool_run.add_argument("name", help="Tool name")
    tool_run.add_argument("--args", dest="tool_args", default="{}", help="JSON arguments")
    tool_run.add_argument("--timeout", type=float, default=30.0)
    tool_run.add_argument("--retries", type=int, default=0)

    # remedy exec <command...>
    exec_cmd = sub.add_parser("exec", help="Execute a command in the sandbox")
    exec_cmd.add_argument("--timeout", type=float, default=30.0)
    exec_cmd.add_argument("--workdir", default=None)
    exec_cmd.add_argument("--shell", default=None, help="Shell to use (pwsh, cmd, bash)")
    exec_cmd.add_argument("cmdline", nargs=argparse.REMAINDER, help="Command and arguments to run")

    # remedy learn reflect|refine|history|stats
    learn = sub.add_parser("learn", help="Learning loop operations")
    learn_sub = learn.add_subparsers(dest="learn_cmd")
    learn_reflect = learn_sub.add_parser("reflect", help="Reflect on a completed task")
    learn_reflect.add_argument("task_title", help="Task title to reflect on")
    learn_reflect.add_argument("--steps", dest="steps_json", default="[]", help="JSON trace steps")
    learn_history = learn_sub.add_parser("history", help="Show learning history")
    learn_history.add_argument("--limit", type=int, default=20)
    learn_changelog = learn_sub.add_parser("changelog", help="Show refinement changelog")
    learn_changelog.add_argument(
        "skill_name", nargs="?", default=None, help="Optional skill name filter"
    )
    learn_stats = learn_sub.add_parser("stats", help="Show skill execution stats")
    learn_stats.add_argument("--skill", dest="skill_name", default=None)
    learn_sub.add_parser("sync", help="Sync learning events to memory store")

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

    # remedy gateway start|status|serve|channels
    gw = sub.add_parser("gateway", help="Gateway operations")
    gw_sub = gw.add_subparsers(dest="gateway_cmd")
    gw_start = gw_sub.add_parser("start", help="Start the gateway daemon")
    gw_start.add_argument("--telegram-token", default="")
    gw_start.add_argument("--discord-token", default="")
    gw_start.add_argument("--slack-token", default="")
    gw_start.add_argument("--heartbeat", type=float, default=60.0)
    gw_sub.add_parser("status", help="Show gateway status")
    gw_sub.add_parser("serve", help="Start the REST API server")
    gw_sub.add_parser("channels", help="List available channels")

    # remedy config init|show|path
    config_cmd = sub.add_parser("config", help="Configuration management")
    config_sub = config_cmd.add_subparsers(dest="config_cmd")
    config_sub.add_parser("init", help="Create default config file")
    config_sub.add_parser("show", help="Show current configuration")
    config_sub.add_parser("path", help="Show config file path")

    # remedy chat
    chat_cmd = sub.add_parser("chat", help="Launch interactive chat with the Remedy agent")
    chat_cmd.add_argument("--config", dest="config_file", default=None)
    chat_cmd.add_argument("--session", dest="session_id", default=None,
                          help="Resume an existing session")
    chat_cmd.add_argument("--no-memory", action="store_true",
                          help="Don't persist conversation to memory")

    # remedy serve
    serve_cmd = sub.add_parser("serve", help="Start the full API server (with config)")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--config", dest="config_file", default=None)

    # remedy desktop
    desktop_cmd = sub.add_parser("desktop", help="Desktop app management")
    desktop_sub = desktop_cmd.add_subparsers(dest="desktop_cmd")
    desktop_sub.add_parser("install", help="Install desktop Node dependencies")
    desktop_dev = desktop_sub.add_parser("dev", help="Start desktop dev server")
    desktop_dev.add_argument("--open", action="store_true", help="Open browser")
    desktop_sub.add_parser("build", help="Build desktop for production")

    # remedy uninstall
    uninstall_cmd = sub.add_parser("uninstall", help="Uninstall Remedy")
    uninstall_cmd.add_argument(
        "--purge", action="store_true", help="Also delete ~/.remedy/ user data"
    )
    uninstall_cmd.add_argument(
        "--dry-run", action="store_true", help="Show what would be removed"
    )

    # remedy update
    update_cmd = sub.add_parser("update", help="Check for and apply updates")
    update_cmd.add_argument(
        "--check", action="store_true", help="Check only, don't apply"
    )

    # remedy setup
    setup_cmd = sub.add_parser("setup", help="Interactive setup wizard")
    setup_cmd.add_argument(
        "--quick", action="store_true", help="Minimal prompts, use defaults"
    )
    setup_cmd.add_argument(
        "--skip-providers", action="store_true",
        help="Skip LLM provider configuration",
    )
    setup_cmd.add_argument(
        "--skip-messaging", action="store_true",
        help="Skip messaging app configuration",
    )
    setup_cmd.add_argument(
        "--skip-skills", action="store_true",
        help="Skip skill discovery configuration",
    )

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


async def _cmd_skill(args) -> None:
    registry = SkillRegistry()
    # Auto-load default skill dirs so list/info/run work without prior discover
    if args.skill_cmd in ("list", "info", "run", "test", "export"):
        registry.discover_defaults()

    if args.skill_cmd == "list":
        if not registry.skills:
            console.print("[dim]No skills registered. Use 'remedy skill discover <path>'[/dim]")
            return
        console.print(f"[bold]{len(registry.skills)} skill(s):[/bold]")
        for skill in sorted(registry.skills, key=lambda s: s.manifest.name):
            desc = skill.manifest.description or ""
            console.print(f"  [cyan]{skill.manifest.name}[/cyan] {desc[:60]}")
        return
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

    elif args.skill_cmd == "run":
        skill = registry.get(args.name)
        if skill is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            return
        executor = SkillExecutor()
        if args.script and skill.source_skill_dir:
            script_path = Path(skill.source_skill_dir) / args.script
            if not script_path.is_file():
                console.print(f"[red]Script not found: {args.script}[/red]")
                return
            result = await executor.run_script(script_path)
            _print_exec_result(result)
        elif skill.scripts and skill.source_skill_dir:
            results = await executor.run_all_scripts(skill.scripts, Path(skill.source_skill_dir))
            for name, res in results.items():
                console.print(f"\n[bold]Script: {name}[/bold]")
                _print_exec_result(res)
        else:
            console.print("[yellow]No scripts to run. Running instruction code blocks...[/yellow]")
            results = await executor.run_instructions(skill.instructions)
            for i, res in enumerate(results):
                console.print(f"\n[bold]Block {i+1}[/bold]")
                _print_exec_result(res)

    elif args.skill_cmd == "test":
        skill = registry.get(args.name)
        if skill is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            return
        validator = SkillValidator()
        results = [
            validator.validate_metadata(skill),
            validator.validate_dependencies(skill),
            validator.validate_scripts(skill),
        ]
        test_result = await validator.run_tests(skill)
        results.append(test_result)

        for r in results:
            status = "[green]PASS[/green]" if r.is_valid else "[red]FAIL[/red]"
            console.print(f"\n{status} {r.skill_name}:")
            for err in r.errors:
                console.print(f"  [red]Error:[/red] {err}")
            for warn in r.warnings:
                console.print(f"  [yellow]Warning:[/yellow] {warn}")
            for tr in r.test_results:
                res = "[green]PASS[/green]" if tr["success"] else "[red]FAIL[/red]"
                console.print(f"  Test {tr['file']}: {res}")

        score = validator.compute_score(results)
        console.print(f"\n[bold]Compliance Score: {score:.0%}[/bold]")

    elif args.skill_cmd == "export":
        skill = registry.get(args.name)
        if skill is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            return
        exporter = SkillExporter(Path(args.output))
        if args.fmt == "native":
            dest = exporter.export_native(skill)
        elif args.fmt == "hermes":
            dest = exporter.export_hermes(skill)
        elif args.fmt == "openclaw":
            dest = exporter.export_openclaw(skill)
        elif args.fmt == "zip":
            dest = exporter.export_zip(skill, format="native")
        else:
            dest = exporter.export_native(skill)
        console.print(f"[green]Exported to:[/green] {dest}")

    if args.skill_cmd in ("list", "discover", "load"):
        _print_skills(registry)


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


async def _cmd_tool(args) -> None:
    registry = ToolRegistry()

    registry.register_builtin("memory_search", "Search the memory store via FTS5")
    registry.register_builtin("memory_add", "Add an entry to the memory store")
    registry.register_builtin("skill_load", "Load a skill by name")
    registry.register_builtin("skill_list", "List registered skills")
    registry.register_builtin("file_read", "Read a file from disk")
    registry.register_builtin("file_write", "Write content to a file")
    registry.register_builtin("bash_exec", "Execute a shell command")

    if args.tool_cmd == "list":
        table = Table(title="Registered Tools")
        table.add_column("Source")
        table.add_column("Name")
        table.add_column("Description")
        for t in registry.tools:
            table.add_row(t.source.value, t.name, t.description[:60])
        console.print(table)

    elif args.tool_cmd == "search":
        results = registry.search(args.query)
        if results:
            for t in results:
                console.print(f"[{t.source.value}] [bold]{t.name}[/bold]: {t.description}")
        else:
            console.print(f"[dim]No tools matching '{args.query}'[/dim]")

    elif args.tool_cmd == "stats":
        stats = registry.get_stats()
        if stats["total_calls"] > 0:
            body = (
                f"Registered: {stats['registered_tools']}\n"
                f"Total calls: {stats['total_calls']}\n"
                f"Success rate: {stats['success_rate']:.1%}\n"
                f"By source: {json.dumps(stats['by_source'])}"
            )
        else:
            body = (
                f"Registered: {stats['registered_tools']}\n"
                "No invocations yet."
            )
        console.print(Panel(body, title="Tool Stats"))

    elif args.tool_cmd == "run":
        sandbox = SubprocessSandbox()
        runtime = ToolRuntime(sandbox=sandbox)
        tool_args = json.loads(args.tool_args)

        tool_call = ToolCall(
            tool_name=args.name,
            arguments=tool_args,
            source=ToolSource.BUILTIN,
        )

        console.print(f"[bold]Running:[/bold] {args.name}")
        result = await runtime.execute(
            tool_call,
            timeout=args.timeout,
        )

        if result.success:
            console.print("[green]Success[/green]")
            if result.data:
                console.print(json.dumps(result.data, indent=2))
        else:
            console.print(f"[red]Failed:[/red] {result.error}")


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
        from remedy.migrate.from_openclaw import migrate_from_openclaw as migrate_from_oc

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


async def _cmd_learn(args, db_path: Path) -> None:
    skills_dir = db_path.parent / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    async with MemoryStore(db_path) as store:
        loop = LearningLoop(skills_dir=skills_dir, memory=store)

        if args.learn_cmd == "reflect":
            from uuid import uuid4 as _uuid4

            trace_steps = []
            try:
                raw_steps = json.loads(args.steps_json)
            except json.JSONDecodeError:
                raw_steps = []

            if raw_steps:
                trace_steps = [
                    TraceStep(
                        index=i, tool_name=s.get("tool", f"step_{i}"),
                        arguments=s.get("args", {}),
                        result_summary=str(s.get("result", ""))[:200],
                        success=s.get("success", True),
                        error=s.get("error"),
                    )
                    for i, s in enumerate(raw_steps)
                ]

            trace = ExecutionTrace(
                task_id=_uuid4(),
                title=args.task_title,
                steps=trace_steps,
            )
            result = loop.learn_from_trace(trace, auto_approve=False)
            if result:
                console.print(f"[green]Generated skill:[/green] {result.manifest.name}")
                console.print(f"  Version: {result.manifest.version}")
                console.print(f"  Tags: {', '.join(result.manifest.tags)}")
                console.print(Panel(result.instructions[:400], title="Instructions (preview)"))
            else:
                console.print("[yellow]Trace too short for meaningful reflection.[/yellow]")

        elif args.learn_cmd == "history":
            events = loop.get_learning_history(limit=args.limit)
            if events:
                for e in events:
                    ts = e.occurred_at.isoformat()[:19]
                    console.print(
                        f"[{e.event_type}] [bold]{e.skill_name}[/bold] v{e.skill_version} — "
                        f"{e.description[:80]} [dim]({ts})[/dim]"
                    )
            else:
                console.print("[dim]No learning events recorded.[/dim]")

        elif args.learn_cmd == "changelog":
            changelog = loop.get_refinement_changelog(
                skill_name=getattr(args, "skill_name", None)
            )
            console.print(changelog)

        elif args.learn_cmd == "stats":
            if args.skill_name:
                stats = loop.get_skill_stats(args.skill_name)
                console.print(Panel(
                    f"[bold]{stats.skill_name}[/bold]\n"
                    f"Executions: {stats.total_executions}\n"
                    f"Successes: {stats.successes}\n"
                    f"Failures: {stats.failures}\n"
                    f"Success rate: {stats.success_rate:.0%}\n"
                    f"Avg duration: {stats.avg_duration_ms:.0f}ms\n"
                    f"Last executed: {stats.last_executed}",
                    title="Skill Stats",
                ))
                if stats.common_errors:
                    console.print("\n[bold]Common Errors:[/bold]")
                    for err, count in stats.common_errors.items():
                        console.print(f"  ({count}x) {err}")
            else:
                all_stats = loop.refiner.get_all_stats()
                if all_stats:
                    for name, st in all_stats.items():
                        console.print(
                            f"[bold]{name}[/bold]: {st.successes}/{st.total_executions} "
                            f"({st.success_rate:.0%})"
                        )
                else:
                    console.print("[dim]No skill stats recorded.[/dim]")

        elif args.learn_cmd == "sync":
            count = await loop.sync_to_memory()
            console.print(f"[green]Synced {count} learning events to memory.[/green]")


async def _cmd_exec(args) -> None:
    import json as _json

    from remedy.core.security import check_dangerous_command

    command = list(args.cmdline) if args.cmdline else []
    if not command:
        console.print("[red]No command specified[/red]")
        return

    danger = check_dangerous_command(command)
    if danger:
        console.print(f"[bold red]WARNING: {danger}[/bold red]")
        result = _json.dumps({"warning": danger, "command": " ".join(command)})
        console.print("[yellow]Execution blocked by security policy[/yellow]")
        return

    sandbox = SubprocessSandbox()
    console.print(f"[bold]Executing:[/bold] {' '.join(command)}")

    result = await sandbox.execute(
        command=command,
        workdir=args.workdir,
        timeout_seconds=args.timeout,
    )

    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(f"[red]{result.stderr}[/red]")

    console.print(f"[dim]Exit code: {result.exit_code} ({result.duration_ms:.0f}ms)[/dim]")


async def _cmd_config(args) -> None:
    from pathlib import Path as _Path
    home = _Path(args.home).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    if args.config_cmd == "init":
        cfg_path = create_default_config(home)
        console.print(f"[green]Config created:[/green] {cfg_path}")
    elif args.config_cmd == "show":
        resolved = resolve_config(
            home_dir=str(home),
        )
        console.print_json(data=resolved)
    elif args.config_cmd == "path":
        cfg_path = home / "config.toml"
        if cfg_path.exists():
            console.print(str(cfg_path))
        else:
            console.print(f"[dim]No config found at {cfg_path}[/dim]")
            console.print("Run 'remedy config init' to create one.")


def _cmd_serve(args) -> None:
    import uvicorn

    from remedy.core.agent import BasicRuntime
    from remedy.gateway.router import Gateway
    from remedy.interfaces.api import create_app
    from remedy.memory.store import MemoryStore

    home = Path(args.home).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    config = resolve_config(
        home_dir=str(home),
    )
    agent_config = config_to_agent_config(config)

    async def _start():
        memory = MemoryStore(
            agent_config.memory_db_path
            or f"{agent_config.home_dir}/memory.db"
        )
        await memory.initialize()

        runtime = BasicRuntime(agent_config, memory=memory)
        await runtime.start()

        # Discover skills
        skills_dir = Path(agent_config.home_dir).expanduser() / "skills"
        if skills_dir.is_dir():
            runtime.skills.discover(str(skills_dir), recurse=True)

        gateway = Gateway(runtime=runtime, memory_store=memory)
        gateway.register_handler(runtime.handle_event)
        await gateway.start()

        return runtime, gateway, memory

    runtime, gateway, memory = asyncio.run(_start())

    api_key = os.environ.get("REMEDY_API_KEY", config.get("api_key", ""))
    app = create_app(
        runtime=runtime,
        gateway=gateway,
        memory=memory,
        title=config.get("name", "Remedy AI"),
        version=__version__,
        api_key=api_key,
    )

    if not agent_config.llm_api_key:
        console.print("[bold yellow]WARNING: No LLM API key configured.[/bold yellow]")
        console.print("  Set REMEDY_LLM_API_KEY env var or run [bold]remedy setup[/bold] to configure.")
        console.print("  The server will run in [bold]fallback (echo)[/bold] mode without a real LLM.\n")

    console.print(f"[green]Starting Remedy API on http://{args.host}:{args.port}[/green]")
    console.print("[dim]Dashboard:[/dim] /dashboard")
    console.print("[dim]OpenAPI:[/dim]   /api/openapi.json  /api/openapi.yaml")
    console.print("[dim]Docs:[/dim]       /docs  /redoc")
    uvicorn.run(app, host=args.host, port=args.port, log_level=config.get("log_level", "info").lower())


def _cmd_chat(args) -> None:
    import asyncio as _asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.gateway.router import Gateway
    from remedy.memory.store import MemoryStore
    from remedy.models import ChannelKind, EventKind, GatewayEvent

    home = Path(args.home).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    config = resolve_config(
        config_path=Path(args.config_file) if args.config_file else None,
        home_dir=str(home),
    )
    agent_config = config_to_agent_config(config)

    async def _chat_loop():
        memory = MemoryStore(
            agent_config.memory_db_path or f"{agent_config.home_dir}/memory.db"
        )
        await memory.initialize()

        runtime = BasicRuntime(agent_config, memory=memory)
        await runtime.start()

        skills_dir = home / "skills"
        if skills_dir.is_dir():
            runtime.skills.discover(str(skills_dir), recurse=True)

        gateway = Gateway(runtime=runtime, memory_store=memory)
        gateway.register_handler(runtime.handle_event)
        await gateway.start()

        sid = args.session_id or await runtime.start_session()

        llm_ready = bool(agent_config.llm_api_key)
        model = agent_config.llm_model or "none"

        console.print()
        console.print(Panel(
            f"[bold green]{agent_config.name}[/bold green] is ready.\n\n"
            f"Session: [dim]{sid}[/dim]\n"
            f"LLM:     [{'green' if llm_ready else 'red'}]{model}[/{'green' if llm_ready else 'red'}]\n"
            f"Skills:  {len(runtime.skills.skills)} loaded\n"
            f"Memory:  {'enabled' if not args.no_memory else 'disabled'}\n\n"
            f"[dim]Type /help for commands, /exit to quit[/dim]",
            title="Remedy Chat",
            border_style="green",
        ))

        try:
            while True:
                try:
                    user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[dim]Goodbye.[/dim]")
                    break

                if not user_input:
                    continue

                if user_input.startswith("/"):
                    cmd = user_input[1:].strip().lower()
                    if cmd in ("exit", "quit", "q"):
                        console.print("[dim]Goodbye.[/dim]")
                        break
                    elif cmd == "help":
                        console.print("""
[bold]Commands:[/bold]
  /exit, /quit, /q  — End this chat session
  /help             — Show this help
  /session          — Show current session ID
  /skills           — List loaded skills
  /clear            — Clear the screen
  Any other input   — Send a message to Remedy
""")
                        continue
                    elif cmd == "session":
                        console.print(f"[dim]Session: {sid}[/dim]")
                        continue
                    elif cmd == "skills":
                        if runtime.skills.skills:
                            for skill in sorted(
                                runtime.skills.skills, key=lambda s: s.manifest.name
                            ):
                                desc = skill.manifest.description or ""
                                console.print(
                                    f"  [cyan]{skill.manifest.name}[/cyan] {desc[:60]}"
                                )
                        else:
                            console.print("[dim]No skills loaded.[/dim]")
                        continue
                    elif cmd == "clear":
                        console.clear()
                        continue
                    else:
                        console.print(f"[dim]Unknown command: /{cmd}. Type /help[/dim]")
                        continue

                event = GatewayEvent(
                    kind=EventKind.MESSAGE,
                    channel=ChannelKind.CLI,
                    source_id="user",
                    payload={"message": user_input},
                    session_id=sid,
                )

                with console.status("[dim]Thinking...[/dim]", spinner="dots"):
                    responses = await gateway.emit(event)

                for r in responses:
                    if isinstance(r, str):
                        console.print(f"[bold green]Remedy:[/bold green] {r}")
                        break

        finally:
            await runtime.stop()
            await gateway.stop()

    _asyncio.run(_chat_loop())


def _cmd_desktop(parsed: argparse.Namespace) -> None:
    """Handle the `remedy desktop` subcommand."""
    # Try package-relative path first (editable install), then working dir
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    desktop_dir = repo_root / "desktop"

    if not desktop_dir.exists():
        # Fall back to searching from cwd upward
        cur = Path.cwd()
        for _ in range(5):
            candidate = cur / "desktop"
            if candidate.exists():
                desktop_dir = candidate
                break
            if (cur / ".git").exists():
                break
            cur = cur.parent

    if not desktop_dir.exists():
        console.print(f"[red]Desktop directory not found at {desktop_dir}[/red]")
        console.print("[dim]Run `git clone` again or ensure the desktop/ folder is present.[/dim]")
        return

    npm = _find_npm()
    subcommand = parsed.desktop_cmd or "install"

    if subcommand == "install":
        console.print("[bold]Installing desktop dependencies...[/bold]")
        import subprocess
        result = subprocess.run(
            [npm, "install"],
            cwd=str(desktop_dir),
            shell=True,
        )
        if result.returncode == 0:
            console.print("[green]Desktop dependencies installed.[/green]")
            console.print("[dim]Run 'remedy desktop dev' to start, then open http://localhost:5173[/dim]")
        else:
            console.print("[red]npm install failed. Is Node.js installed?[/red]")

    elif subcommand == "dev":
        console.print("[bold]Starting desktop dev server...[/bold]")
        console.print("[dim]Make sure 'remedy serve' is running in another terminal.[/dim]")
        console.print("[dim]Open http://localhost:5173 in your browser.[/dim]")
        console.print()
        import subprocess
        subprocess.run(
            [npm, "run", "dev"] + (["--", "--open"] if getattr(parsed, "open", False) else []),
            cwd=str(desktop_dir),
            shell=True,
        )

    elif subcommand == "build":
        console.print("[bold]Building desktop for production...[/bold]")
        import subprocess
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(desktop_dir),
            shell=True,
        )
        if result.returncode == 0:
            console.print(f"[green]Desktop built to {desktop_dir / 'dist'}[/green]")
        else:
            console.print("[red]Build failed.[/red]")

    else:
        console.print(f"[yellow]Unknown desktop subcommand: {subcommand}[/yellow]")
        console.print("Available: install, dev, build")


def _find_npm() -> str:
    """Find the npm executable."""
    import shutil
    npm = shutil.which("npm") or shutil.which("pnpm") or shutil.which("yarn")
    if npm is None:
        console.print("[red]No Node package manager found (npm/pnpm/yarn).[/red]")
        console.print("[dim]Install Node.js from https://nodejs.org[/dim]")
        raise SystemExit(1)
    return npm


def main(args: list[str] | None = None) -> None:
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
    elif parsed.command == "tool":
        asyncio.run(_cmd_tool(parsed))
    elif parsed.command == "learn":
        asyncio.run(_cmd_learn(parsed, db_path))
    elif parsed.command == "gateway":
        main_gateway(parsed)
    elif parsed.command == "exec":
        asyncio.run(_cmd_exec(parsed))
    elif parsed.command == "config":
        asyncio.run(_cmd_config(parsed))
    elif parsed.command == "chat":
        _cmd_chat(parsed)
    elif parsed.command == "serve":
        _cmd_serve(parsed)
    elif parsed.command == "desktop":
        _cmd_desktop(parsed)
    elif parsed.command == "setup":
        run_wizard(
            quick=parsed.quick,
            skip_providers=parsed.skip_providers,
            skip_messaging=parsed.skip_messaging,
            skip_skills=parsed.skip_skills,
        )
    elif parsed.command == "update":
        run_update(check_only=parsed.check)
    elif parsed.command == "uninstall":
        run_uninstall(purge=parsed.purge, dry_run=parsed.dry_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
