"""Gateway CLI entrypoint — start, status, channels list."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedy.gateway.channel_registry import register_messenger_channels
from remedy.gateway.channels import CLIChannel, WebChannel
from remedy.gateway.messengers import is_messenger_channel, list_messenger_definitions
from remedy.gateway.router import Gateway
from remedy.gateway.session_bridge import handle_messenger_event, outbound_chunks
from remedy.models import ChannelKind, EventKind, GatewayEvent

console = Console()


def _load_cfg() -> dict[str, Any]:
    try:
        from remedy.interfaces.api_support import load_config

        return load_config() or {}
    except Exception:
        return {}


async def run_gateway(
    db_path: Path,
    token_telegram: str = "",
    token_discord: str = "",
    token_slack: str = "",
    heartbeat: float = 60.0,
) -> None:
    """Start the Remedy gateway and all configured channels."""
    from remedy.core.agent import BasicRuntime
    from remedy.memory.store import MemoryStore
    from remedy.models import AgentConfig

    cfg = _load_cfg()
    home = db_path.parent
    config = AgentConfig(
        memory_db_path=str(db_path),
        home_dir=str(cfg.get("home_dir") or home),
        llm_provider=str(cfg.get("llm_provider") or "openai"),
        llm_model=str(cfg.get("llm_model") or "gpt-4o-mini"),
        llm_base_url=str(cfg.get("llm_base_url") or "https://api.openai.com/v1"),
        llm_api_key=str(cfg.get("llm_api_key") or ""),
        name=str(cfg.get("name") or "Remedy"),
        project_path=cfg.get("project_path"),
    )
    try:
        from remedy.interfaces.config import resolve_provider_api_key

        key = resolve_provider_api_key(cfg, config.llm_provider, home=home)
        if key:
            config.llm_api_key = key
    except Exception:
        pass

    runtime = BasicRuntime(config)
    memory = MemoryStore(db_path)
    await memory.initialize()
    runtime.memory = memory  # type: ignore[attr-defined]
    await runtime.start()

    gw_cfg = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
    rate = int(gw_cfg.get("rate_limit") or 120)
    hb = float(gw_cfg.get("heartbeat_interval") or heartbeat)

    gw = Gateway(
        runtime=runtime,
        heartbeat_interval=hb,
        rate_limit=rate,
        memory_store=memory,
    )

    async def _handle_event(event: GatewayEvent):
        target = (
            event.payload.get("chat_id")
            or event.payload.get("channel_id")
            or event.source_id
            or None
        )
        ch_name = event.channel.value if hasattr(event.channel, "value") else str(event.channel)

        if is_messenger_channel(ch_name):
            buf: list[str] = []
            async for chunk in handle_messenger_event(runtime, event):
                if chunk is not None:
                    buf.append(str(chunk))
                    yield chunk
            full = "".join(buf).strip()
            if full:
                for part in outbound_chunks(full, ch_name):
                    await gw.send_to(
                        event.channel, part, target=str(target) if target else None
                    )
            return

        async for chunk in runtime.handle_event(event):
            if chunk is not None:
                await gw.send_to(
                    event.channel, str(chunk), target=str(target) if target else None
                )
                yield chunk

    gw.register_handler(_handle_event)
    cli = CLIChannel(gw)
    gw.register_channel(cli)

    registered = register_messenger_channels(
        gw,
        cfg,
        token_telegram=token_telegram,
        token_discord=token_discord,
        token_slack=token_slack,
    )
    gw.register_channel(WebChannel(gw))
    await gw.start()

    console.print(Panel(
        f"[bold green]Remedy Gateway Running[/bold green]\n"
        f"Channels: {', '.join(c.value for c in gw.channels)}\n"
        f"Messengers: {', '.join(registered) or '(none)'}\n"
        f"Heartbeat: {hb}s · Rate: {rate}/min\n"
        f"Database: {db_path}\n"
        f"\n[dim]Messenger chats appear in desktop Sessions (realtime SSE).\n"
        f"Press Ctrl+C to stop[/dim]",
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
                await gw.emit(
                    GatewayEvent(
                        kind=EventKind.MESSAGE,
                        channel=ChannelKind.CLI,
                        source_id="cli-user",
                        payload={"message": line.strip()},
                        raw=line.strip(),
                    )
                )
    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down...[/dim]")
    finally:
        await gw.stop()
        await runtime.stop()
        await memory.close()
        console.print("[dim]Gateway stopped.[/dim]")


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
    db_path = Path(args.home).expanduser().resolve()
    db_path.mkdir(parents=True, exist_ok=True)
    db_file = db_path / "memory.db"

    if args.gateway_cmd == "start":
        asyncio.run(
            run_gateway(
                db_file,
                token_telegram=getattr(args, "telegram_token", "") or "",
                token_discord=getattr(args, "discord_token", "") or "",
                token_slack=getattr(args, "slack_token", "") or "",
                heartbeat=getattr(args, "heartbeat", 60.0),
            )
        )
    elif args.gateway_cmd == "status":
        asyncio.run(gateway_status(db_file))
    elif args.gateway_cmd == "serve":
        _serve_api(db_file)
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
            console.print(
                f"  {m.id:14} {m.status:8} {m.name}  "
                f"[{'/'.join(flags) or '—'}]"
            )


def _serve_api(db_path: Path) -> None:
    """Start the HTTP API with the same fail-closed auth as ``remedy serve``.

    Historical bug: this path called ``create_app()`` without ``api_key``, which
    disabled Bearer middleware entirely (open loopback). Always load/generate the
    local API token unless ``REMEDY_API_AUTH=0``.
    """
    import os

    import uvicorn

    from remedy import __version__
    from remedy.interfaces.api import create_app
    from remedy.interfaces.local_auth import ensure_local_api_token

    # db_path is …/memory.db under REMEDY_HOME; auth lives under home/auth/
    home = db_path.parent if db_path.suffix else db_path
    api_key = ensure_local_api_token(
        home,
        explicit=os.environ.get("REMEDY_API_KEY") or None,
    )
    app = create_app(
        title="Remedy AI",
        version=__version__,
        api_key=api_key,
    )
    if api_key:
        console.print(
            "[dim]API auth:[/dim] enabled (Bearer token under auth/local_api_token)"
        )
    else:
        console.print(
            "[yellow]API auth disabled[/yellow] (REMEDY_API_AUTH=0) — open loopback"
        )
    console.print("[green]Starting Remedy API on http://127.0.0.1:7400[/green]")
    uvicorn.run(app, host="127.0.0.1", port=7400, log_level="info")
