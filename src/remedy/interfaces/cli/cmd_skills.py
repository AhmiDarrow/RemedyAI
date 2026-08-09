"""CLI: skill / tool / learn / exec."""

from __future__ import annotations

import json
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from remedy.core.learning.reflection import ExecutionTrace, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.execution.sandbox import SubprocessSandbox
from remedy.interfaces.cli.util import _print_exec_result, _print_skills, console
from remedy.interfaces.config import config_to_agent_config, resolve_config
from remedy.memory.store import MemoryStore
from remedy.models import ToolCall, ToolSource
from remedy.skills.executor import SkillExecutor
from remedy.skills.exporter import SkillExporter
from remedy.skills.registry import SkillRegistry
from remedy.skills.validator import SkillValidator


async def _cmd_skill(args) -> None:
    registry = SkillRegistry()
    # Auto-load default skill dirs so list/info/run work without prior discover
    if args.skill_cmd in ("list", "info", "run", "test", "export"):
        registry.discover_defaults()

    if args.skill_cmd == "list":
        if not registry.skills:
            console.print("[dim]No skills registered. Use 'remedy skill discover <path>'[/dim]")
            return
        show_all = bool(getattr(args, "all", False))
        learned_only = bool(getattr(args, "learned", False))
        visible: list = []
        hidden_learned = 0
        for skill in registry.skills:
            meta = skill.manifest.metadata or {}
            auto = bool(meta.get("auto_generated"))
            st = skill.manifest.status
            st_v = st.value if hasattr(st, "value") else str(st)
            if learned_only:
                if auto:
                    visible.append(skill)
                continue
            # Default: hide auto-learned probation so coding workflows stay usable.
            if (
                auto
                and not show_all
                and st_v not in ("active",)
            ):
                hidden_learned += 1
                continue
            visible.append(skill)
        total = len(registry.skills)
        console.print(
            f"[bold]{len(visible)} skill(s)[/bold]"
            + (
                f" [dim](of {total}; {hidden_learned} learned probation hidden — "
                f"use --all)[/dim]"
                if hidden_learned
                else (f" [dim](of {total})[/dim]" if len(visible) != total else "")
            )
            + ":"
        )
        for skill in sorted(visible, key=lambda s: s.manifest.name):
            desc = skill.manifest.description or ""
            meta = skill.manifest.metadata or {}
            badge = ""
            if meta.get("auto_generated"):
                st = skill.manifest.status
                st_v = st.value if hasattr(st, "value") else str(st)
                badge = f" [dim](learned/{st_v})[/dim]"
            console.print(f"  [cyan]{skill.manifest.name}[/cyan]{badge} {desc[:60]}")
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




async def _cmd_tool(args) -> None:
    """Tool CLI — uses BasicRuntime so file/shell tools stay workspace-jailed."""
    from remedy.core.agent import BasicRuntime

    home = Path(getattr(args, "home", None) or "~/.remedy").expanduser()
    cfg = config_to_agent_config(resolve_config(home_dir=str(home)))
    if not getattr(cfg, "home_dir", None):
        cfg.home_dir = str(home)
    if not getattr(cfg, "memory_db_path", None):
        cfg.memory_db_path = str(home / "memory.db")

    rt = BasicRuntime(cfg)
    registry = rt.tool_registry

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
        tool_args = json.loads(args.tool_args)
        tool_call = ToolCall(
            tool_name=args.name,
            arguments=tool_args,
            source=ToolSource.BUILTIN,
        )
        console.print(f"[bold]Running:[/bold] {args.name}")
        # Goes through BasicRuntime.call_tool → jailed workspace handlers.
        result = await rt.call_tool(tool_call)
        if result.success:
            console.print("[green]Success[/green]")
            if result.data is not None:
                if isinstance(result.data, str):
                    console.print(result.data)
                else:
                    console.print(json.dumps(result.data, indent=2, default=str))
        else:
            console.print(f"[red]Failed:[/red] {result.error}")




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



