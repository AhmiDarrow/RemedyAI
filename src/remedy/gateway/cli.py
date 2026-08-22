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


def _default_model(provider: Any) -> str:
    try:
        from remedy.interfaces.config import default_model_for_provider

        return default_model_for_provider(str(provider or ""))
    except Exception:
        return ""


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
        llm_model=str(cfg.get("llm_model") or _default_model(cfg.get("llm_provider"))),
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
    runtime.memory = memory
    await runtime.start()

    gw_raw = cfg.get("gateway")
    gw_cfg: dict = gw_raw if isinstance(gw_raw, dict) else {}
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
        token_telegram = cli_tg or os.environ.get("TELEGRAM_BOT_TOKEN", "") or ""
        token_discord = cli_dc or os.environ.get("DISCORD_BOT_TOKEN", "") or ""
        token_slack = cli_sl or os.environ.get("SLACK_BOT_TOKEN", "") or ""
        asyncio.run(
            run_gateway(
                db_file,
                token_telegram=token_telegram,
                token_discord=token_discord,
                token_slack=token_slack,
                heartbeat=getattr(args, "heartbeat", 60.0),
            )
        )
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
    """Same serve path as ``remedy serve`` (lock, bind, config, messengers)."""
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
