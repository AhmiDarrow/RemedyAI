"""CLI: serve / chat / desktop."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from rich.panel import Panel

from remedy import __version__
from remedy.interfaces.cli.util import (
    UnsafeHomeError,
    console,
    evaluate_serve_bind,
    resolve_cli_home,
)
from remedy.interfaces.config import (
    config_to_agent_config,
    create_default_config,
    resolve_config,
)
from remedy.interfaces.wizard import ensure_setup_before_launch
from remedy.memory.store import MemoryStore


class _NullStream:
    """Drop-in file-like for frozen windowed builds where sys.stdout/stderr are None.

    uvicorn's ColourizedFormatter calls ``sys.stdout.isatty()`` at config time;
    a --noconsole sidecar has ``sys.stdout is None`` and crashes with
    ``AttributeError: 'NoneType' object has no attribute 'isatty'``. Give the
    logging stack a real object that answers isatty()=False and swallows writes.
    """

    def write(self, data) -> None:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no console attached")


def _ensure_stdio() -> None:
    """Never let uvicorn / logging see a None stdout/stderr.

    PyInstaller windowed (--noconsole) builds start Python with both set to
    None. Replace them with a null stream so formatter config (isatty) and
    StreamHandler writes degrade gracefully instead of raising.
    """
    import sys as _sys

    for name in ("stdout", "stderr"):
        if getattr(_sys, name, None) is None:
            setattr(_sys, name, _NullStream())


def _cmd_serve(args) -> None:
    import sys
    import threading
    import time as _time


    # Frozen windowed builds have sys.stdout/stderr == None; give logging a
    # null stream BEFORE any formatter/handler touches them (uvicorn crashes
    # on None.isatty()). Also covers setup_serve_logging and StructuredFormatter.
    _ensure_stdio()

    from remedy.core.agent import BasicRuntime
    from remedy.gateway.router import Gateway
    from remedy.interfaces.api import create_app
    from remedy.interfaces.instance_lock import (
        heartbeat_serve_lock,
        try_acquire_serve_lock,
    )

    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    # Only one API/serve stack at a time (Desktop sidecar or CLI).
    ok_lock, lock_msg = try_acquire_serve_lock(home)
    if not ok_lock:
        console.print(f"[red]{lock_msg}[/red]")
        sys.exit(2)
    if lock_msg and lock_msg != "acquired":
        console.print(f"[dim]Instance lock:[/dim] {lock_msg}")

    def _lock_heartbeat() -> None:
        while True:
            _time.sleep(30.0)
            heartbeat_serve_lock()

    threading.Thread(target=_lock_heartbeat, name="serve-lock-hb", daemon=True).start()

    # First-run setup: NEVER block the HTTP server for the desktop sidecar.
    # Desktop UI (SetupWizard) is the first-run experience and needs the API up.
    # Interactive CLI TTY can still run the wizard; --skip-setup / env skip it.
    from remedy.core.runtime_identity import is_desktop_sidecar

    skip = bool(getattr(args, "skip_setup", False)) or is_desktop_sidecar()
    force = bool(getattr(args, "force_setup", False))
    # Only interactive CLI (real TTY, not desktop) may gate on the wizard.
    # isatty() alone lies on scripted/hidden launches (inherited console);
    # guard stdin availability too so a --noconsole sidecar never prompts.
    real_tty = bool(
        sys.stdin is not None
        and getattr(sys.stdin, "isatty", lambda: False)()
        and not (sys.stdin.closed if hasattr(sys.stdin, "closed") else False)
    )
    if force or (real_tty and not skip):
        ok = ensure_setup_before_launch(
            home_dir=home,
            skip_setup=False,
            force=force,
            non_interactive=False,
        )
        if not ok:
            raise SystemExit(1)
    elif skip and not force:
        # Desktop / headless: ensure a minimal config exists so settings API works,
        # but do NOT mark setup_completed — UI wizard still runs on first open.
        create_default_config(home)

    config = resolve_config(
        config_path=Path(args.config_file) if args.config_file else None,
        home_dir=str(home),
    )

    # Durable logs under ~/.remedy/logs (debug.log always DEBUG for perf diagnosis).
    try:
        from remedy.core.logging import setup_serve_logging

        log_dir = setup_serve_logging(home, config=config)
        console.print(f"[dim]Logs:[/dim]      {log_dir}")
    except Exception as exc:
        console.print(f"[yellow]Logging setup failed:[/yellow] {exc}")

    # A hard crash (access violation inside a C extension, a stack overflow, a
    # deadlocked thread killed by the OS) leaves nothing in the Python logs.
    # faulthandler writes the native traceback of every thread to crash.log
    # at the moment the process dies, so the desktop server panel can show
    # *why* the sidecar vanished instead of only that it did.
    from remedy.interfaces.serve_forensics import enable_crash_forensics

    enable_crash_forensics(home)

    agent_config = config_to_agent_config(config)

    async def _start():
        memory = MemoryStore(
            agent_config.memory_db_path
            or f"{agent_config.home_dir}/memory.db"
        )
        await memory.initialize()

        runtime = BasicRuntime(agent_config, memory=memory)
        await runtime.start()

        # Bundled defaults + ~/.remedy/skills (seeded on first run)
        n_skills = runtime.skills.discover_defaults(home_dir=agent_config.home_dir)
        # Extra paths from config
        for extra in agent_config.skills_dir or []:
            p = Path(str(extra)).expanduser()
            if p.is_dir():
                n_skills += runtime.skills.discover(str(p), recurse=True)

        gateway = Gateway(runtime=runtime, memory_store=memory)
        from remedy.gateway.serve_bootstrap import attach_messengers_to_gateway

        # Register channels only — do NOT gateway.start() here.
        # asyncio.run() closes this loop when _start returns; Telegram poll
        # tasks would die. Real start happens in FastAPI lifespan (uvicorn loop).
        attach_messengers_to_gateway(runtime, gateway)

        return runtime, gateway, memory, n_skills

    # Partner full-power defaults (unset only if operator forces tight mode)
    os.environ.setdefault("REMEDY_FULL_CONTEXT", "1")
    os.environ.setdefault("REMEDY_REACT_AUTO_CONTINUE", "1")

    runtime, gateway, memory, n_skills = asyncio.run(_start())

    # First install / stale map: stretch out and census this PC in the background.
    try:
        from remedy.execution.host.stretch import ensure_home_stretch

        ensure_home_stretch(home, force=False, background=True)
    except Exception:
        pass

    # Warm secret-store / ACL path once so the first Settings GET is not paying
    # Windows icacls (~100ms) on the critical UI path.
    try:
        from remedy.interfaces.secret_store import load_provider_keys

        load_provider_keys(home)
    except Exception:
        pass

    from remedy.interfaces.local_auth import ensure_local_api_token

    api_key = ensure_local_api_token(
        home,
        explicit=os.environ.get("REMEDY_API_KEY") or config.get("api_key") or None,
    )
    host = str(getattr(args, "host", "127.0.0.1") or "127.0.0.1")
    # 0.0.0.0 / non-loopback: owner power preserved only with explicit danger flag.
    bind_verdict = evaluate_serve_bind(host, has_auth=bool(api_key))
    if bind_verdict == "refuse":
        console.print(
            "[bold red]Refusing to serve without auth on a non-loopback bind.[/bold red]\n"
            f"  host={host!r} and API auth is disabled.\n"
            "  Fix: leave auth on (default), or bind 127.0.0.1, or set\n"
            "  REMEDY_ALLOW_INSECURE_BIND=1 if you really want an open LAN agent."
        )
        raise SystemExit(2)
    if bind_verdict == "warn":
        console.print(
            f"[bold yellow]WARNING:[/yellow] Binding {host!r} exposes the agent on the network.\n"
            "  Only processes with your API token can call tools — keep the token secret.\n"
            "  Set REMEDY_ALLOW_INSECURE_BIND=1 to allow auth-off on that bind (not recommended)."
        )
    app = create_app(
        runtime=runtime,
        gateway=gateway,
        memory=memory,
        title=config.get("name", "Remedy AI"),
        version=__version__,
        api_key=api_key,
    )
    try:
        app.state.sidecar_port = int(getattr(args, "port", 7400) or 7400)
    except (TypeError, ValueError):
        app.state.sidecar_port = 7400
    if api_key:
        console.print(
            "[dim]API auth:[/dim]   enabled (Bearer token in ~/.remedy/auth/local_api_token)"
        )
    else:
        console.print(
            "[yellow]API auth:[/yellow]  disabled (REMEDY_API_AUTH=0) — loopback only recommended"
        )

    if not agent_config.llm_api_key:
        console.print("[bold yellow]WARNING: No LLM API key configured.[/bold yellow]")
        console.print("  Set REMEDY_LLM_API_KEY env var or run [bold]remedy setup[/bold] to configure.")
        console.print("  The server will run in [bold]fallback (echo)[/bold] mode without a real LLM.\n")

    console.print(f"[green]Starting Remedy API on http://{args.host}:{args.port}[/green]")
    console.print(f"[dim]Skills:[/dim]    {n_skills} loaded (bundled + user)")
    webui = getattr(app.state, "webui_dir", None)
    if webui:
        console.print(
            f"[dim]Web UI:[/dim]    http://{args.host}:{args.port}/  "
            f"(from {webui})"
        )
    else:
        console.print(
            "[dim]Web UI:[/dim]    not found — build desktop UI "
            "(`cd desktop && npm run build`) or set REMEDY_WEBUI_DIR"
        )
    console.print("[dim]Dashboard:[/dim] /dashboard")
    console.print("[dim]OpenAPI:[/dim]   /api/openapi.json  /api/openapi.yaml")
    console.print("[dim]Docs:[/dim]       /docs  /redoc")

    # Optional in-process computer host so API sessions can navigate without Desktop.
    # Default OFF so Desktop Tauri poller is not racing claims; opt-in via flag/env.
    want_host = bool(getattr(args, "computer_host", False)) or str(
        os.environ.get("REMEDY_CLI_COMPUTER_HOST", "")
    ).strip().lower() in ("1", "true", "yes", "on")
    if bool(getattr(args, "no_computer_host", False)):
        want_host = False
    if want_host:
        try:
            from remedy.core.computer.cli_host import start_cli_computer_host

            ch = start_cli_computer_host(home)
            ok_h = ch.status().get("host_connected")
            console.print(
                f"[dim]Computer:[/dim]  CLI host "
                f"{'[green]connected[/green]' if ok_h else '[yellow]starting[/yellow]'} "
                f"(system browser + desktop; Desktop app still preferred for rail DOM)"
            )
        except Exception as exc:
            console.print(f"[yellow]Computer host failed:[/yellow] {exc}")

    try:
        from remedy.connect.lifecycle import maybe_start_connect

        maybe_start_connect(
            app,
            config if isinstance(config, dict) else {},
            api_key=str(api_key or ""),
            sidecar_port=int(getattr(args, "port", 7400) or 7400),
        )
    except Exception as exc:
        console.print(f"[yellow]Connect gateway skipped:[/yellow] {exc}")

    log_level = config.get("log_level", "INFO").upper()
    # Access logs spam the desktop console (status polls every few seconds).
    # Opt in with access_log=true in config or REMEDY_ACCESS_LOG=1.
    access_log_enabled = bool(
        config.get("access_log")
        or str(os.environ.get("REMEDY_ACCESS_LOG", "")).lower() in ("1", "true", "yes")
    )
    uvicorn_log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": log_level},
            "uvicorn.error": {"handlers": ["default"], "level": log_level, "propagate": False},
            # Keep access logger quiet unless explicitly enabled.
            "uvicorn.access": {
                "handlers": ["access"] if access_log_enabled else [],
                "level": "INFO" if access_log_enabled else "WARNING",
                "propagate": False,
            },
        },
    }
    from remedy.interfaces.serve_forensics import run_uvicorn_logged

    run_uvicorn_logged(
        app,
        host=args.host,
        port=args.port,
        log_config=uvicorn_log_config,
        access_log=access_log_enabled,
    )


def _cmd_chat(args) -> None:
    import asyncio as _asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.gateway.router import Gateway
    from remedy.models import ChannelKind, EventKind, GatewayEvent

    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    # Always gate interactive chat on first-run setup (or --skip-setup).
    ok = ensure_setup_before_launch(
        home_dir=home,
        skip_setup=bool(getattr(args, "skip_setup", False)),
        force=bool(getattr(args, "force_setup", False)),
    )
    if not ok:
        raise SystemExit(1)

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
        n_skills = runtime.skills.discover_defaults(home_dir=home)

        # Computer-use: in-process CLI host so navigate/open works without Desktop.
        # Default OFF so Desktop's poller is not racing claims (same as serve).
        computer_host_on = False
        want_host = bool(getattr(args, "computer_host", False))
        skip_host = bool(getattr(args, "no_computer_host", False)) or not want_host
        if not skip_host:
            try:
                from remedy.core.computer.cli_host import start_cli_computer_host

                host = start_cli_computer_host(home)
                computer_host_on = bool(host.running and host.status().get("host_connected"))
            except Exception as exc:
                console.print(f"[yellow]CLI computer host failed:[/yellow] {exc}")

        gateway = Gateway(runtime=runtime, memory_store=memory)
        gateway.register_handler(runtime.handle_event)
        await gateway.start()

        sid = args.session_id or await runtime.start_session()

        llm_ready = bool(agent_config.llm_api_key)
        model = agent_config.llm_model or "none"
        computer_line = (
            "[green]CLI host on[/green] (system browser + desktop)"
            if computer_host_on
            else (
                "[dim]off[/dim] (desktop tools only; use --computer-host or Desktop app)"
                if skip_host
                else "[yellow]starting…[/yellow]"
            )
        )

        console.print()
        console.print(Panel(
            f"[bold green]{agent_config.name}[/bold green] is ready.\n\n"
            f"Session:  [dim]{sid}[/dim]\n"
            f"LLM:      [{'green' if llm_ready else 'red'}]{model}[/{'green' if llm_ready else 'red'}]\n"
            f"Skills:   {n_skills} loaded\n"
            f"Memory:   {'enabled' if not args.no_memory else 'disabled'}\n"
            f"Computer: {computer_line}\n\n"
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
  /computer         — Computer-use host status
  /clear            — Clear the screen
  Any other input   — Send a message to Remedy
""")
                        continue
                    elif cmd == "session":
                        console.print(f"[dim]Session: {sid}[/dim]")
                        continue
                    elif cmd == "computer":
                        try:
                            from remedy.core.computer.cli_host import get_local_computer_host
                            from remedy.core.computer.host_bridge import get_host_bridge

                            b = get_host_bridge(home)
                            h = get_local_computer_host(home)
                            console.print(
                                f"  host_connected={b.host_connected()}  "
                                f"cli_host={h.running}  pending={b.pending_count()}"
                            )
                        except Exception as exc:
                            console.print(f"[red]{exc}[/red]")
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
            try:
                from remedy.core.computer.cli_host import stop_cli_computer_host

                stop_cli_computer_host()
            except Exception:
                pass
            await runtime.stop()
            await gateway.stop()

    _asyncio.run(_chat_loop())


def _cmd_desktop(parsed: argparse.Namespace) -> None:
    """Handle the `remedy desktop` subcommand."""
    # Try to find repo root: check __file__ first, then cwd upward
    repo_root = None
    for candidate in [
        Path(__file__).resolve().parent.parent.parent.parent,
        Path.cwd(),
    ]:
        for _ in range(6):
            if (candidate / "pyproject.toml").exists():
                repo_root = candidate
                break
            candidate = candidate.parent
        if repo_root:
            break

    desktop_dir = (repo_root / "desktop") if repo_root else None

    if not desktop_dir or not desktop_dir.exists():
        # Fall back to searching from cwd upward for desktop/package.json
        cur = Path.cwd()
        for _ in range(5):
            candidate = cur / "desktop"
            if (candidate / "package.json").exists():
                desktop_dir = candidate
                break
            if (cur / ".git").exists():
                break
            cur = cur.parent

    if not desktop_dir or not desktop_dir.exists():
        console.print(f"[red]Desktop directory not found at {desktop_dir}[/red]")
        console.print("[dim]Run `git clone` again or ensure the desktop/ folder is present.[/dim]")
        raise SystemExit(1)

    npm = _find_npm()
    subcommand = parsed.desktop_cmd or "install"

    if subcommand == "install":
        console.print("[bold]Installing desktop dependencies...[/bold]")
        import subprocess
        result = subprocess.run(
            [npm, "install"],
            cwd=str(desktop_dir),
        )
        if result.returncode == 0:
            console.print("[green]Desktop dependencies installed.[/green]")
            console.print("[dim]Run 'remedy desktop dev' to start, then open http://localhost:5173[/dim]")
        else:
            console.print("[red]npm install failed. Is Node.js installed?[/red]")
            raise SystemExit(result.returncode or 1)

    elif subcommand == "dev":
        console.print("[bold]Starting desktop dev server...[/bold]")
        console.print("[dim]Make sure 'remedy serve' is running in another terminal.[/dim]")
        console.print("[dim]Open http://localhost:5173 in your browser.[/dim]")
        console.print()
        import subprocess
        result = subprocess.run(
            [npm, "run", "dev"] + (["--", "--open"] if getattr(parsed, "open", False) else []),
            cwd=str(desktop_dir),
        )
        if result.returncode not in (0, None):
            raise SystemExit(result.returncode or 1)

    elif subcommand == "build":
        console.print("[bold]Building desktop for production...[/bold]")
        import subprocess
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(desktop_dir),
        )
        if result.returncode == 0:
            console.print(f"[green]Desktop built to {desktop_dir / 'dist'}[/green]")
        else:
            console.print("[red]Build failed.[/red]")
            raise SystemExit(result.returncode or 1)

    elif subcommand == "launch":
        _desktop_launch()

    elif subcommand == "status":
        _desktop_status()

    else:
        console.print(f"[yellow]Unknown desktop subcommand: {subcommand}[/yellow]")
        console.print("Available: install, dev, build, launch, status")


def _find_npm() -> str:
    """Find the npm executable."""
    import shutil
    npm = shutil.which("npm") or shutil.which("pnpm") or shutil.which("yarn")
    if npm is None:
        console.print("[red]No Node package manager found (npm/pnpm/yarn).[/red]")
        console.print("[dim]Install Node.js from https://nodejs.org[/dim]")
        raise SystemExit(1)
    return npm


def _desktop_launch() -> None:
    """Find and launch the installed Remedy Desktop Tauri app (Windows only today)."""
    import subprocess
    import sys

    if sys.platform != "win32":
        console.print(
            "[yellow]remedy desktop launch is currently Windows-only.[/yellow]"
        )
        console.print(
            "[dim]On macOS/Linux, open the installed Remedy Desktop app from "
            "your Applications menu or desktop entry.[/dim]"
        )
        console.print(
            "[dim]Installer downloads: https://github.com/AhmiDarrow/RemedyAI/releases[/dim]"
        )
        raise SystemExit(1)

    candidate_paths: list[Path] = []
    local = Path.home() / "AppData" / "Local"
    if local.exists():
        candidate_paths.extend(
            local / "Programs" / p / "Remedy Desktop.exe"
            for p in ("Remedy Desktop", "remedy-desktop")
        )
    prog = Path("C:/Program Files")
    if prog.exists():
        candidate_paths.append(prog / "Remedy Desktop" / "Remedy Desktop.exe")

    # Detach from this console and never flash an extra console for the GUI app.
    from remedy.execution.process import hidden_creationflags

    create_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    create_flags |= hidden_creationflags()

    for p in candidate_paths:
        if p.exists():
            console.print(f"[green]Launching: {p}[/green]")
            kwargs: dict = {"close_fds": True}
            if create_flags:
                kwargs["creationflags"] = create_flags
            subprocess.Popen([str(p)], **kwargs)
            return

    console.print("[yellow]Installed desktop app not found.[/yellow]")
    console.print("[dim]Download the installer: https://github.com/AhmiDarrow/RemedyAI/releases[/dim]")
    raise SystemExit(1)


def _desktop_status() -> None:
    """Check if the Remedy server is running (sidecar on port 7400)."""
    import socket

    console.print("[bold]Desktop Server Status[/bold]")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 7400))
        sock.close()
        if result == 0:
            console.print("  Server:    [green]Online[/green]  (127.0.0.1:7400)")
        else:
            console.print("  Server:    [red]Offline[/red] (port 7400 not reachable)")
    except Exception as e:
        console.print(f"  Server:    [red]Error[/red] — {e}")

    console.print("[dim]Use 'remedy serve' to start the server manually.[/dim]")


