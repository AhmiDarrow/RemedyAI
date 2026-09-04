"""CLI: serve / chat / desktop.

Production ``remedy serve`` launches Go ``remedy-runtime`` (API authority on
``:7400``). Python does not bind the local HTTP server — keep
``python -m remedy.runtime.rmdy_tool_worker`` for the RMDY tool worker.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.panel import Panel

from remedy.interfaces.cli.util import (
    UnsafeHomeError,
    console,
    resolve_cli_home,
)
from remedy.interfaces.config import (
    config_to_agent_config,
    resolve_config,
)
from remedy.interfaces.wizard import ensure_setup_before_launch
from remedy.memory.store import MemoryStore


class _NullStream:
    """File-like for frozen windowed builds where sys.stdout/stderr are None."""

    def write(self, data) -> None:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no console attached")


def _ensure_stdio() -> None:
    """Never let logging see a None stdout/stderr (PyInstaller --noconsole)."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[3], here.parents[4] if len(here.parents) > 4 else None):
        if candidate is None:
            continue
        if (candidate / "native" / "go" / "go.mod").is_file():
            return candidate
    env = os.environ.get("REMEDY_DEV_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "native" / "go" / "go.mod").is_file():
            return root
    return None


def _runtime_bin_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("remedy-runtime.exe", "remedy-runtime")
    return ("remedy-runtime",)


def resolve_remedy_runtime_command() -> list[str] | None:
    """Return argv to launch ``remedy-runtime``, or None when missing.

    Search order: ``REMEDY_NATIVE_RUNTIME_BIN`` / ``REMEDY_RUNTIME``, PATH,
    ``sys.executable`` dir, repo ``desktop/bin``, then ``go run`` in a checkout.
    """
    for env_name in ("REMEDY_NATIVE_RUNTIME_BIN", "REMEDY_RUNTIME"):
        override = os.environ.get(env_name, "").strip()
        if override:
            return [override]

    for name in _runtime_bin_names():
        found = shutil.which(name)
        if found:
            return [found]

    roots: list[Path] = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    root = _repo_root()
    if root is not None:
        roots.append(root / "desktop" / "bin")
        roots.append(root / "bin")
    for base in roots:
        for name in _runtime_bin_names():
            candidate = base / name
            if candidate.is_file():
                return [str(candidate)]

    if root is not None:
        go = shutil.which("go")
        if go:
            return [go, "run", "./cmd/remedy-runtime"]

    return None


def build_runtime_serve_argv(
    runtime_cmd: list[str],
    *,
    host: str = "127.0.0.1",
    port: int = 7400,
) -> list[str]:
    """Map CLI host/port onto ``remedy-runtime --serve`` / ``--listen``."""
    host_n = (host or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port_n = int(port)
    except (TypeError, ValueError):
        port_n = 7400
    argv = [*runtime_cmd, "--serve"]
    if host_n != "127.0.0.1" or port_n != 7400:
        argv.extend(["--listen", f"{host_n}:{port_n}"])
    return argv


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in {"127.0.0.1", "localhost", "::1"}


def _cmd_serve(args) -> None:
    """Hand production :7400 to ``remedy-runtime``. No Python uvicorn dual-serve."""
    _ensure_stdio()
    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    host = str(getattr(args, "host", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(getattr(args, "port", 7400) or 7400)
    except (TypeError, ValueError):
        port = 7400

    # Go httpapi refuses non-loopback binds — fail closed here with a clear error.
    if not _is_loopback_host(host):
        console.print(
            "[bold red]Refusing non-loopback serve bind.[/bold red]\n"
            f"  host={host!r} — remedy-runtime only listens on 127.0.0.1 / ::1.\n"
            "  Use --host 127.0.0.1 (default)."
        )
        raise SystemExit(2)

    runtime_cmd = resolve_remedy_runtime_command()
    if not runtime_cmd:
        console.print(
            "[red]remedy serve:[/red] Go ``remedy-runtime`` owns the local API "
            "(:7400). Binary not found.\n"
            "  Build ``native/go/cmd/remedy-runtime`` into ``desktop/bin``, put "
            "it on PATH, or set REMEDY_NATIVE_RUNTIME_BIN.\n"
            "  Python no longer starts uvicorn on :7400 (no dual-serve)."
        )
        raise SystemExit(2)

    os.environ["REMEDY_HOME"] = str(home)
    # Partner defaults still apply for any Python worker the runtime may spawn.
    os.environ.setdefault("REMEDY_FULL_CONTEXT", "1")
    os.environ.setdefault("REMEDY_REACT_AUTO_CONTINUE", "1")

    argv = build_runtime_serve_argv(runtime_cmd, host=host, port=port)
    listen = f"{host}:{port}"
    console.print(
        f"[green]Starting remedy-runtime on http://{listen}[/green] "
        "[dim](Go owns the local API; Python is worker-only)[/dim]"
    )

    cwd = None
    if len(runtime_cmd) >= 3 and runtime_cmd[1] == "run":
        root = _repo_root()
        if root is not None:
            cwd = str(root / "native" / "go")

    try:
        raise SystemExit(subprocess.call(argv, cwd=cwd))
    except OSError as exc:
        console.print(f"[red]remedy-runtime launch failed:[/red] {exc}")
        raise SystemExit(1) from exc


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
        console.print(
            "[dim]Make sure 'remedy serve' (remedy-runtime) is up, or use tauri:dev.[/dim]"
        )
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
    from remedy.execution.process import popen_hidden

    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    for p in candidate_paths:
        if p.exists():
            console.print(f"[green]Launching: {p}[/green]")
            popen_hidden([str(p)], close_fds=True, creationflags=new_group)
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

    console.print(
        "[dim]Use 'remedy serve' to start remedy-runtime (local API authority).[/dim]"
    )


