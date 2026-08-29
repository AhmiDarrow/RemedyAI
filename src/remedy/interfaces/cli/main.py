"""CLI entrypoint — dispatch subcommands."""

from __future__ import annotations

import asyncio

from remedy.gateway.cli import main_gateway
from remedy.interfaces.cli.cmd_runtime import _cmd_chat, _cmd_desktop, _cmd_serve
from remedy.interfaces.cli.cmd_settings import (
    _cmd_auth,
    _cmd_computer,
    _cmd_config,
    _cmd_settings,
)
from remedy.interfaces.cli.cmd_skills import _cmd_exec, _cmd_learn, _cmd_skill, _cmd_tool
from remedy.interfaces.cli.cmd_store import (
    _cmd_handoff,
    _cmd_memory,
    _cmd_migrate,
    _cmd_session,
    _cmd_user,
)
from remedy.interfaces.cli.parser import build_parser
from remedy.interfaces.cli.util import UnsafeHomeError, _get_db_path, console
from remedy.interfaces.uninstaller import run_uninstall
from remedy.interfaces.updater import run_update
from remedy.interfaces.wizard import run_wizard


def main(args: list[str] | None = None) -> None:
    parser = build_parser()
    parsed = parser.parse_args(args)

    if parsed.command is None:
        parser.print_help()
        raise SystemExit(2)

    try:
        db_path = _get_db_path(parsed.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

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
    elif parsed.command == "settings":
        _cmd_settings(parsed)
    elif parsed.command == "computer":
        _cmd_computer(parsed)
    elif parsed.command == "auth":
        _cmd_auth(parsed)
    elif parsed.command == "chat":
        _cmd_chat(parsed)
    elif parsed.command == "serve":
        _cmd_serve(parsed)
    elif parsed.command == "mcp":
        if getattr(parsed, "mcp_cmd", None) == "serve":
            from remedy.tools.mcp_server import run_stdio_server

            raise SystemExit(run_stdio_server())
        console.print("[dim]Usage: remedy mcp serve[/dim]")
        raise SystemExit(2)
    elif parsed.command == "desktop":
        _cmd_desktop(parsed)
    elif parsed.command == "connect-relay":
        from remedy.connect.relay import main as connect_relay_main

        raise SystemExit(connect_relay_main(host=parsed.host, port=parsed.port))
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
        run_uninstall(
            purge=parsed.purge,
            dry_run=parsed.dry_run,
            config=getattr(parsed, "config", False),
            skills=getattr(parsed, "skills", False),
            home=getattr(parsed, "home", None),
        )
    else:
        parser.print_help()




if __name__ == "__main__":
    main()
