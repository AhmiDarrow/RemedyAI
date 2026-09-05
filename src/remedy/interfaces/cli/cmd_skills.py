"""CLI: skill / tool / learn / exec."""

from __future__ import annotations

import json
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from remedy.core.learning.reflection import ExecutionTrace, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.execution.result import SubprocessSandbox
from remedy.interfaces.cli.util import _print_exec_result, _print_skills, console
from remedy.memory.store import MemoryStore
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
        found = registry.get(args.name)
        if found is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            console.print("Run 'remedy skill discover <path>' first.")
            raise SystemExit(1)
        m = found.manifest
        console.print(Panel(
            f"[bold]{m.name}[/bold] v{m.version}\n"
            f"{m.description}\n\n"
            f"[dim]Kind: {m.kind.value} | Status: {m.status.value}[/dim]\n"
            f"[dim]Tags: {', '.join(m.tags) if m.tags else 'none'}[/dim]\n"
            f"[dim]Path: {m.path}[/dim]",
            title="Skill Info",
        ))
        if found.instructions:
            console.print("\n[bold]Instructions:[/bold]")
            console.print(found.instructions[:500])
    elif args.skill_cmd == "load":
        loaded = registry.load_single(args.path)
        console.print(f"[green]Loaded:[/green] {loaded.manifest.name} v{loaded.manifest.version}")

    elif args.skill_cmd == "run":
        found = registry.get(args.name)
        if found is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            raise SystemExit(1)
        executor = SkillExecutor()
        failed = False
        if args.script and found.source_skill_dir:
            from remedy.skills.script_path import (
                SkillScriptJailError,
                resolve_jailed_skill_script,
            )

            try:
                script_path = resolve_jailed_skill_script(
                    found.source_skill_dir, args.script
                )
            except SkillScriptJailError as exc:
                console.print(
                    f"[red]Script path escapes skill scripts/: {args.script}[/red]"
                )
                raise SystemExit(1) from exc
            if not script_path.is_file():
                console.print(f"[red]Script not found: {args.script}[/red]")
                raise SystemExit(1)
            result = await executor.run_script(script_path)
            _print_exec_result(result)
            failed = not bool(getattr(result, "success", result.exit_code == 0))
        elif found.scripts and found.source_skill_dir:
            results = await executor.run_all_scripts(found.scripts, Path(found.source_skill_dir))
            for name, res in results.items():
                console.print(f"\n[bold]Script: {name}[/bold]")
                _print_exec_result(res)
                if not bool(getattr(res, "success", getattr(res, "exit_code", 1) == 0)):
                    failed = True
        else:
            console.print("[yellow]No scripts to run. Running instruction code blocks...[/yellow]")
            blocks = await executor.run_instructions(found.instructions)
            for i, res in enumerate(blocks):
                console.print(f"\n[bold]Block {i+1}[/bold]")
                _print_exec_result(res)
                if not bool(getattr(res, "success", getattr(res, "exit_code", 1) == 0)):
                    failed = True
        if failed:
            raise SystemExit(1)

    elif args.skill_cmd == "test":
        found = registry.get(args.name)
        if found is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            raise SystemExit(1)
        validator = SkillValidator()
        checks = [
            validator.validate_metadata(found),
            validator.validate_dependencies(found),
            validator.validate_scripts(found),
        ]
        test_result = await validator.run_tests(found)
        checks.append(test_result)

        for r in checks:
            status = "[green]PASS[/green]" if r.is_valid else "[red]FAIL[/red]"
            console.print(f"\n{status} {r.skill_name}:")
            for err in r.errors:
                console.print(f"  [red]Error:[/red] {err}")
            for warn in r.warnings:
                console.print(f"  [yellow]Warning:[/yellow] {warn}")
            for tr in r.test_results:
                mark = "[green]PASS[/green]" if tr["success"] else "[red]FAIL[/red]"
                console.print(f"  Test {tr['file']}: {mark}")

        score = validator.compute_score(checks)
        console.print(f"\n[bold]Compliance Score: {score:.0%}[/bold]")
        if any(not r.is_valid for r in checks):
            raise SystemExit(1)

    elif args.skill_cmd == "export":
        found = registry.get(args.name)
        if found is None:
            console.print(f"[red]Skill not found: {args.name}[/red]")
            raise SystemExit(1)
        exporter = SkillExporter(Path(args.output))
        if args.fmt == "native":
            dest = exporter.export_native(found)
        elif args.fmt == "hermes":
            dest = exporter.export_hermes(found)
        elif args.fmt == "openclaw":
            dest = exporter.export_openclaw(found)
        elif args.fmt == "zip":
            dest = exporter.export_zip(found, format="native")
        else:
            dest = exporter.export_native(found)
        console.print(f"[green]Exported to:[/green] {dest}")

    if args.skill_cmd in ("list", "discover", "load"):
        _print_skills(registry)




def _tool_runtime_base() -> str:
    import os

    return (os.environ.get("REMEDY_API") or "http://127.0.0.1:7400").rstrip("/")


def _tool_runtime_request(
    method: str,
    path: str,
    *,
    home: Path,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, object]:
    """Call Go remedy-runtime Tool ABI routes (no Python BasicRuntime)."""
    import urllib.error
    import urllib.request

    from remedy.interfaces.local_auth import ensure_local_api_token, load_local_api_token

    token = load_local_api_token(home) or ensure_local_api_token(home)
    url = f"{_tool_runtime_base()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            payload: object = None
            if raw:
                payload = json.loads(raw.decode("utf-8"))
            return int(resp.status), payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload: object = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = raw.decode("utf-8", errors="replace")
        return int(exc.code), payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        console.print(
            "[bold red]remedy tool needs Go remedy-runtime.[/bold red]\n"
            "  Start [green]remedy serve[/green] (or Desktop), then retry.\n"
            f"  [dim]({_tool_runtime_base()} unreachable: {exc})[/dim]\n"
            "  Tool ABI ids look like [cyan]runtime.probe[/cyan], "
            "[cyan]workspace.read[/cyan], [cyan]shell.exec[/cyan] — "
            "not the retired Python BasicRuntime names."
        )
        raise SystemExit(2) from exc


async def _cmd_tool(args) -> None:
    """Tool CLI — Go Tool ABI via remedy-runtime HTTP (no BasicRuntime)."""
    from remedy.interfaces.cli.util import UnsafeHomeError, resolve_cli_home

    try:
        home = resolve_cli_home(getattr(args, "home", None) or "~/.remedy")
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    timeout = float(getattr(args, "timeout", 30.0) or 30.0)

    if args.tool_cmd == "list":
        code, payload = _tool_runtime_request("GET", "/api/tools", home=home, timeout=timeout)
        if code != 200 or not isinstance(payload, dict):
            detail = payload if isinstance(payload, str) else (
                payload.get("detail") if isinstance(payload, dict) else payload
            )
            console.print(f"[red]Tool list failed ({code}):[/red] {detail}")
            raise SystemExit(1)
        table = Table(title="Tool ABI (remedy-runtime)")
        table.add_column("Runtime")
        table.add_column("Id")
        table.add_column("Risk")
        table.add_column("Description")
        for t in payload.get("tools") or []:
            if not isinstance(t, dict):
                continue
            table.add_row(
                str(t.get("runtime") or ""),
                str(t.get("id") or ""),
                str(t.get("risk") or ""),
                str(t.get("description") or "")[:60],
            )
        console.print(table)

    elif args.tool_cmd == "search":
        from urllib.parse import quote

        q = quote(str(args.query or ""), safe="")
        code, payload = _tool_runtime_request(
            "GET", f"/api/tools?q={q}", home=home, timeout=timeout
        )
        if code != 200 or not isinstance(payload, dict):
            detail = payload if isinstance(payload, str) else (
                payload.get("detail") if isinstance(payload, dict) else payload
            )
            console.print(f"[red]Tool search failed ({code}):[/red] {detail}")
            raise SystemExit(1)
        tools = payload.get("tools") or []
        if tools:
            for t in tools:
                if not isinstance(t, dict):
                    continue
                console.print(
                    f"[{t.get('runtime')}] [bold]{t.get('id')}[/bold]: "
                    f"{t.get('description')}"
                )
        else:
            console.print(f"[dim]No tools matching '{args.query}'[/dim]")

    elif args.tool_cmd == "stats":
        code, payload = _tool_runtime_request("GET", "/api/tools", home=home, timeout=timeout)
        if code != 200 or not isinstance(payload, dict):
            detail = payload if isinstance(payload, str) else (
                payload.get("detail") if isinstance(payload, dict) else payload
            )
            console.print(f"[red]Tool stats failed ({code}):[/red] {detail}")
            raise SystemExit(1)
        count = int(payload.get("count") or 0)
        by_runtime: dict[str, int] = {}
        for t in payload.get("tools") or []:
            if not isinstance(t, dict):
                continue
            rt = str(t.get("runtime") or "unknown")
            by_runtime[rt] = by_runtime.get(rt, 0) + 1
        console.print(Panel(
            f"Registered (Tool ABI): {count}\n"
            f"By runtime: {json.dumps(by_runtime, sort_keys=True)}\n"
            "Invocation counters live on Go turn metrics, not this CLI.",
            title="Tool Stats",
        ))

    elif args.tool_cmd == "run":
        try:
            tool_args = json.loads(args.tool_args)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --args JSON:[/red] {exc}")
            raise SystemExit(2) from exc
        if not isinstance(tool_args, dict):
            console.print("[red]--args must be a JSON object[/red]")
            raise SystemExit(2)
        console.print(f"[bold]Running Tool ABI:[/bold] {args.name}")
        code, payload = _tool_runtime_request(
            "POST",
            "/api/tools/invoke",
            home=home,
            body={"id": args.name, "input": tool_args},
            timeout=timeout,
        )
        if code == 200 and isinstance(payload, dict) and payload.get("ok"):
            console.print("[green]Success[/green]")
            out = payload.get("output")
            if out is not None:
                if isinstance(out, str):
                    console.print(out)
                else:
                    console.print(json.dumps(out, indent=2, default=str))
            return
        err = None
        if isinstance(payload, dict):
            err = payload.get("error") or payload.get("detail")
        console.print(f"[red]Failed ({code}):[/red] {err or payload}")
        raise SystemExit(1)




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
            except json.JSONDecodeError as exc:
                console.print(f"[red]Invalid --steps JSON:[/red] {exc}")
                raise SystemExit(2) from exc

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
    from remedy.core.security import check_dangerous_command

    command = list(args.cmdline) if args.cmdline else []
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        console.print("[red]No command specified[/red]")
        raise SystemExit(2)

    danger = check_dangerous_command(command)
    if danger:
        console.print(f"[bold red]WARNING: {danger}[/bold red]")
        console.print("[yellow]Execution blocked by security policy[/yellow]")
        raise SystemExit(2)

    sandbox = SubprocessSandbox(shell=getattr(args, "shell", None))
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
    if result.exit_code not in (0, None):
        raise SystemExit(int(result.exit_code) if result.exit_code > 0 else 1)



