"""Gateway CLI — status / channels / serve. Start retired to Go remedy-runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from remedy.interfaces.messenger_catalog import list_messenger_definitions
from remedy.models import ChannelKind

console = Console()


def run_gateway_start() -> None:
    """Python dual-poll gateway retired — Go remedy-runtime owns messengers."""
    raise SystemExit(
        "remedy gateway start retired Python BasicRuntime/ReAct. "
        "Messengers inbound run inside Go remedy-runtime (remedy serve)."
    )


async def gateway_status(db_path: Path) -> None:
    from remedy.memory.store import MemoryStore

    async with MemoryStore(db_path) as store:
        info: dict[str, Any] = {}
        try:
            sessions = await store.list_chat_sessions(limit=1000)
            messenger_sessions = [s for s in sessions if getattr(s, "origin_channel", None)]
            info = {
                "sessions": len(sessions),
                "messenger_sessions": len(messenger_sessions),
                "db_path": str(db_path),
                "db_exists": db_path.exists(),
            }
        except Exception as e:
            info["error"] = str(e)

        table = Table(title="Remedy Gateway Status")
        table.add_column("Metric")
        table.add_column("Value")
        for k, v in info.items():
            table.add_row(k, str(v))
        console.print(table)


def main_gateway(args) -> None:
    import os

    from remedy.interfaces.cli.util import UnsafeHomeError, resolve_cli_home

    try:
        db_path = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    db_file = db_path / "memory.db"

    if args.gateway_cmd == "start":
        cli_tg = getattr(args, "telegram_token", "") or ""
        cli_dc = getattr(args, "discord_token", "") or ""
        cli_sl = getattr(args, "slack_token", "") or ""
        if cli_tg or cli_dc or cli_sl:
            console.print(
                "[yellow]Passing messenger tokens on the command line exposes them "
                "in process lists. Prefer TELEGRAM_BOT_TOKEN / DISCORD_BOT_TOKEN / "
                "SLACK_BOT_TOKEN.[/yellow]"
            )
        _ = (
            cli_tg or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            cli_dc or os.environ.get("DISCORD_BOT_TOKEN", ""),
            cli_sl or os.environ.get("SLACK_BOT_TOKEN", ""),
            getattr(args, "heartbeat", 60.0),
        )
        run_gateway_start()
    elif args.gateway_cmd == "status":
        asyncio.run(gateway_status(db_file))
    elif args.gateway_cmd == "serve":
        _serve_api(db_file, args=args)
    elif args.gateway_cmd == "channels":
        console.print("[bold]Internal channels:[/bold]")
        for c in ChannelKind:
            if c.value in ("cli", "web", "api"):
                console.print(f"  {c.value}")
        console.print("\n[bold]Messengers:[/bold]")
        for m in list_messenger_definitions():
            flags = []
            if m.inbound:
                flags.append("in")
            if m.outbound:
                flags.append("out")
            # Escaped: rich reads a bare [in/out] as a style tag and prints
            # nothing at all, so the direction column only ever appeared for a
            # messenger that supports neither direction — the exact opposite of
            # what it is for.
            console.print(
                f"  {m.id:14} {m.status:8} {m.name}  "
                rf"\[{'/'.join(flags) or '—'}]"
            )


def _serve_api(db_path: Path, args: Any | None = None) -> None:
    """Same serve path as ``remedy serve`` — hands off to ``remedy-runtime``."""
    from types import SimpleNamespace

    from remedy.interfaces.cli.cmd_runtime import _cmd_serve

    home = str(db_path.parent if db_path.suffix else db_path)
    ns = args if args is not None else SimpleNamespace()
    if not getattr(ns, "home", None):
        ns.home = home
    ns.skip_setup = True if not hasattr(ns, "skip_setup") else ns.skip_setup
    if not hasattr(ns, "force_setup"):
        ns.force_setup = False
    if not getattr(ns, "host", None):
        ns.host = "127.0.0.1"
    if not getattr(ns, "port", None):
        ns.port = 7400
    if not hasattr(ns, "config_file"):
        ns.config_file = None
    if not hasattr(ns, "computer_host"):
        ns.computer_host = False
    if not hasattr(ns, "no_computer_host"):
        ns.no_computer_host = False
    _cmd_serve(ns)
