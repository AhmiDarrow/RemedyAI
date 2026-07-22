"""Gateway CLI entrypoint -- start, status, and manage channels."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedy.gateway.channels.adapters import (
    CLIChannel,
    DiscordChannel,
    SlackChannel,
    TelegramChannel,
    WebChannel,
)
from remedy.gateway.router import Gateway
from remedy.models import AgentConfig, ChannelKind

console = Console()


async def run_gateway(
    db_path: Path,
    token_telegram: str = "",
    token_discord: str = "",
    token_slack: str = "",
    heartbeat: float = 60.0,
) -> None:
    """Start the Remedy gateway and all configured channels."""

    from remedy.core.runtime import AgentRuntime

    config = AgentConfig(
        memory_db_path=str(db_path),
        home_dir=str(db_path.parent),
    )
    runtime = AgentRuntime(config)
    await runtime.start()

    gw = Gateway(
        runtime=runtime,
        heartbeat_interval=heartbeat,
        rate_limit=120,
    )

    # Always register CLI
    cli = CLIChannel(gw)
    gw.register_channel(cli)

    # Optional channels
    if token_telegram:
        gw.register_channel(TelegramChannel(gw, bot_token=token_telegram))
    if token_discord:
        gw.register_channel(DiscordChannel(gw, bot_token=token_discord))
    if token_slack:
        gw.register_channel(SlackChannel(gw, bot_token=token_slack))

    # Web channel for API
    web = WebChannel(gw)
    gw.register_channel(web)

    await gw.start()

    console.print(Panel(
        f"[bold green]Remedy Gateway Running[/bold green]\n"
        f"Channels: {', '.join(c.value for c in gw.channels)}\n"
        f"Heartbeat: {heartbeat}s\n"
        f"Rate limit: 120/min\n"
        f"Database: {db_path}\n"
        f"\n[dim]Press Ctrl+C to stop[/dim]",
        title="Gateway Status",
    ))

    try:
        while gw.running:
            line = await cli.read_line(timeout=0.5)
            if line is None:
                continue
            if line.strip().lower() in ("exit", "quit", "/quit"):
                break
            if line.strip():
                from remedy.models import EventKind, GatewayEvent
                event = GatewayEvent.from_orm(type("Event", (), {
                    "kind": EventKind.MESSAGE,
                    "channel": ChannelKind.CLI,
                    "source_id": "cli-user",
                    "payload": {"message": line.strip()},
                }))
                await gw.emit(type("GatewayEvent", (), {
                    "kind": EventKind.MESSAGE,
                    "channel": ChannelKind.CLI,
                    "id": None,
                    "source_id": "cli-user",
                    "payload": {"message": line.strip()},
                    "received_at": None,
                    "session_id": None,
                    "raw": line.strip(),
                    "model_config": {},
                })())

    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down...[/dim]")
    finally:
        await gw.stop()
        await runtime.stop()
        console.print("[dim]Gateway stopped.[/dim]")


async def gateway_status(db_path: Path) -> None:
    from remedy.memory.store import MemoryStore

    async with MemoryStore(db_path) as store:
        info = {}
        try:
            entries = await store.list_recent(limit=1)
            all_entries = await store.list_recent(limit=1000)
            handoffs = await store.list_handoffs(limit=1000)
            sessions = await store.list_sessions(limit=1000)
            info = {
                "memory_entries": len(all_entries),
                "handoffs": len(handoffs),
                "sessions": len(sessions),
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
    db_path = Path(args.home).expanduser().resolve()
    db_path.mkdir(parents=True, exist_ok=True)
    db_file = db_path / "memory.db"

    if args.gateway_cmd == "start":
        asyncio.run(run_gateway(
            db_file,
            token_telegram=getattr(args, "telegram_token", "") or "",
            token_discord=getattr(args, "discord_token", "") or "",
            token_slack=getattr(args, "slack_token", "") or "",
            heartbeat=getattr(args, "heartbeat", 60.0),
        ))
    elif args.gateway_cmd == "status":
        asyncio.run(gateway_status(db_file))
    elif args.gateway_cmd == "serve":
        _serve_api(db_file)
    elif args.gateway_cmd == "channels":
        console.print("[bold]Available channels:[/bold]")
        for c in ChannelKind:
            console.print(f"  {c.value}")


def _serve_api(db_path: Path) -> None:
    import uvicorn

    from remedy.interfaces.api import create_app

    app = create_app(title="Remedy AI", version="0.1.0")
    console.print("[green]Starting Remedy API on http://127.0.0.1:8000[/green]")
    console.print("[dim]Endpoints: /api/status /api/chat /api/memory/search /api/skills[/dim]")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
