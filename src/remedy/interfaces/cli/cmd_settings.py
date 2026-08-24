"""CLI: auth / config / settings / computer."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

from rich.panel import Panel
from rich.table import Table

from remedy.interfaces.cli.util import (
    UnsafeHomeError,
    console,
    redact_cli_mapping,
    resolve_cli_home,
)
from remedy.interfaces.config import (
    create_default_config,
    resolve_config,
)


def _known_providers() -> dict[str, dict]:
    from remedy.interfaces.config import PROVIDER_CATALOG

    return dict(PROVIDER_CATALOG)


def _auth_methods(provider: str) -> list[str]:
    entry = _known_providers().get(provider) or {}
    return [str(a) for a in (entry.get("auth") or [])]


def _print_key_status(home, provider: str = "") -> None:
    """What is stored, never what it is. Fingerprints only."""
    from remedy.interfaces import secret_store

    status = secret_store.public_secret_status(home, include_fingerprints=True)
    fingerprints = status.get("fingerprints") or {}
    catalog = _known_providers()

    rows = [provider] if provider else sorted(status.get("providers_with_keys") or [])
    table = Table(title=f"API keys ({status.get('encoding')})")
    table.add_column("Provider")
    table.add_column("Key stored")
    table.add_column("Fingerprint")
    table.add_column("Env fallback")
    if not rows:
        console.print("[dim]No provider API keys stored yet.[/dim]")
    else:
        for name in rows:
            entry = catalog.get(name) or {}
            env = ", ".join(
                k for k in (entry.get("env_keys") or []) if os.environ.get(k)
            )
            has = name in fingerprints
            table.add_row(
                name,
                "[green]yes[/green]" if has else "[dim]no[/dim]",
                str(fingerprints.get(name) or "—"),
                env or "—",
            )
        console.print(table)
    console.print(f"[dim]Stored in {status.get('store_path')}[/dim]")
    if status.get("encoding_warning"):
        console.print(f"[yellow]{status['encoding_warning']}[/yellow]")


def _auth_api_key_provider(provider: str, cmd: str | None, args, home) -> None:
    """auth login/logout/status/apikey for every provider that is not xAI.

    The storage layer has always handled all of them — ``secret_store`` is
    DPAPI-sealed and keyed by provider — but this command refused anything but
    xAI, so a CLI-only owner had no way to set a key at all short of editing
    files by hand.
    """
    from remedy.interfaces import secret_store

    catalog = _known_providers()
    if provider == "all":
        # Additive: the bare command still defaults to xai, as it always has.
        if cmd == "status":
            _print_key_status(home)
            return
        if cmd == "logout":
            secret_store.clear_provider_secret(None, home)
            console.print("[green]Cleared every stored provider API key.[/green]")
            return
        console.print(
            "[yellow]'all' works with status and logout.[/yellow] "
            "Name a provider to store or sign in to one."
        )
        raise SystemExit(2)
    if provider not in catalog:
        console.print(
            f"[red]Unknown provider '{provider}'.[/red] "
            f"Known: {', '.join(sorted(catalog))}, all"
        )
        raise SystemExit(2)

    methods = _auth_methods(provider)
    label = str(catalog[provider].get("label") or provider)

    if cmd == "status":
        if "api_key" not in methods:
            console.print(
                f"[green]{label} needs no key[/green] — it runs locally or "
                "allows guest access."
            )
            return
        _print_key_status(home, provider)
        return

    if cmd == "logout":
        if secret_store.get_provider_secret(provider, home) is None:
            console.print(f"[dim]No stored key for {label}; nothing to clear.[/dim]")
            return
        secret_store.clear_provider_secret(provider, home)
        console.print(f"[green]Cleared the stored API key for {label}.[/green]")
        return

    if cmd == "apikey":
        if "api_key" not in methods:
            console.print(
                f"[yellow]{label} does not take an API key[/yellow] — it runs "
                "locally or allows guest access, so there is nothing to store."
            )
            raise SystemExit(2)
        key = getattr(args, "api_key", None)
        if not key:
            from rich.prompt import Prompt

            key = Prompt.ask(f"{label} API key", password=True, console=console)
        key = str(key or "").strip()
        if not key:
            console.print("[red]API key is empty.[/red]")
            raise SystemExit(1)
        # Same door the desktop app uses: it refuses Claude subscription
        # tokens and files a key under the provider that actually issued it,
        # instead of whatever provider was named on the command line.
        from remedy.interfaces.config import infer_key_owner, set_provider_key

        try:
            set_provider_key({}, provider, key, home=home)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise SystemExit(1) from exc
        owner = infer_key_owner(key)
        if owner and owner != provider:
            console.print(
                f"[yellow]That key belongs to {owner}, not {label}; "
                f"stored it under {owner}.[/yellow]"
            )
            label = owner
        console.print(
            f"[green]Saved {label} API key.[/green] "
            f"fingerprint={secret_store.fingerprint_key(key)}"
        )
        return

    if cmd == "login":
        if "oauth" in methods:  # pragma: no cover — xAI is handled above today
            console.print(
                f"[yellow]{label} supports OAuth, but only xAI's device-code "
                "flow is wired up.[/yellow]"
            )
            raise SystemExit(2)
        console.print(
            f"[yellow]{label} has no interactive sign-in — it authenticates with "
            f"an API key.[/yellow]",
            f"Run: [bold]remedy auth apikey {provider}[/bold]",
        )
        raise SystemExit(2)

    console.print(
        "[dim]Usage: remedy auth login|logout|status|apikey "
        f"{provider}[/dim]"
    )
    raise SystemExit(2)


def _cmd_auth(args) -> None:
    """Provider auth: login / logout / status / apikey (xAI device-code OAuth)."""
    import time
    import webbrowser

    provider = str(getattr(args, "provider", None) or "xai").strip().lower()
    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    cmd = getattr(args, "auth_cmd", None)

    if provider != "xai":
        _auth_api_key_provider(provider, cmd, args, home)
        return

    from remedy.interfaces import xai_auth

    if cmd == "status":
        creds = xai_auth.load_credentials(home=home)
        pub = creds.to_public_dict()
        table = Table(title="xAI auth")
        table.add_column("Field")
        table.add_column("Value")
        for k, v in pub.items():
            table.add_row(str(k), str(v))
        console.print(table)
        return

    if cmd == "logout":
        xai_auth.clear_credentials(home=home)
        console.print("[green]Signed out of xAI (cleared ~/.remedy/auth/xai.json).[/green]")
        return

    if cmd == "apikey":
        key = getattr(args, "api_key", None)
        if not key:
            from rich.prompt import Prompt

            key = Prompt.ask("xAI API key", password=True, console=console)
        try:
            creds = xai_auth.save_api_key(str(key), home=home)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise SystemExit(1) from exc
        console.print(
            f"[green]Saved xAI API key.[/green] connected={creds.connected}"
        )
        return

    if cmd == "login":
        console.print("[bold]Sign in with xAI[/bold] (device-code OAuth)…")
        try:
            start = xai_auth.start_device_login(home=home)
        except Exception as exc:
            console.print(f"[red]Failed to start OAuth: {exc}[/red]")
            raise SystemExit(1) from exc
        uri = start.get("verification_uri_complete") or start.get("verification_uri")
        code = start.get("user_code")
        console.print(f"User code: [bold cyan]{code}[/bold cyan]")
        console.print(f"Open: [link={uri}]{uri}[/link]")
        if uri:
            with contextlib.suppress(Exception):
                webbrowser.open(str(uri))
        session_id = start.get("session_id") or code
        deadline = time.time() + int(start.get("expires_in") or 900)
        interval = max(3, int(start.get("interval") or 5))
        with console.status("Waiting for approval…"):
            while time.time() < deadline:
                time.sleep(interval)
                status = xai_auth.login_status(session_id=session_id, home=home)
                sess = status.get("session") or {}
                st = sess.get("status")
                if st == "connected":
                    console.print("[green]Connected via xAI OAuth.[/green]")
                    return
                if st == "error":
                    console.print(
                        f"[red]Login failed: {sess.get('error') or 'unknown'}[/red]"
                    )
                    raise SystemExit(1)
        console.print("[red]Login timed out. Run `remedy auth login xai` again.[/red]")
        raise SystemExit(1)

    console.print("[dim]Usage: remedy auth login|logout|status|apikey xai[/dim]")


async def _cmd_config(args) -> None:
    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    if args.config_cmd == "init":
        cfg_path = create_default_config(home)
        console.print(f"[green]Config created:[/green] {cfg_path}")
    elif args.config_cmd == "show":
        resolved = resolve_config(
            home_dir=str(home),
        )
        console.print_json(data=redact_cli_mapping(resolved))
    elif args.config_cmd == "path":
        cfg_path = home / "config.toml"
        if cfg_path.exists():
            console.print(str(cfg_path))
        else:
            console.print(f"[dim]No config found at {cfg_path}[/dim]")
            console.print("Run 'remedy config init' to create one.")


def _parse_setting_value(raw: str) -> Any:
    """Parse CLI setting values: bool/int/float/JSON, else plain string."""
    s = raw.strip()
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    if (s.startswith("{") and s.endswith("}")) or (
        s.startswith("[") and s.endswith("]")
    ):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    try:
        if s.startswith("0") and len(s) > 1 and "." not in s:
            raise ValueError("leading zero")
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _cmd_settings(args) -> None:
    """``remedy settings show|get|set|keys`` — same apply path as Desktop/API."""
    from remedy.interfaces.settings_apply import (
        SETTABLE_KEYS,
        apply_settings_update,
        public_settings_snapshot,
    )

    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    # Ensure resolve_config/home is consistent for load_config paths
    os.environ.setdefault("REMEDY_HOME", str(home))

    sub = getattr(args, "settings_cmd", None)
    if sub is None or sub == "show":
        snap = public_settings_snapshot()
        console.print(Panel("[bold]Remedy settings[/bold] (secrets redacted)", border_style="cyan"))
        console.print_json(data=snap)
        return

    if sub == "keys":
        table = Table(title="Settable settings keys")
        table.add_column("Key")
        for k in sorted(SETTABLE_KEYS):
            table.add_row(k)
        console.print(table)
        console.print(
            "[dim]Usage: remedy settings set thinking_level=high tool_process=full[/dim]"
        )
        return

    if sub == "get":
        key = str(args.key or "").strip()
        snap = public_settings_snapshot()
        if key in snap:
            console.print_json(data={key: snap[key]})
            return
        # Allow querying keys not in public snapshot (still settable)
        if key in SETTABLE_KEYS:
            console.print(f"[dim]{key}[/dim] is settable but not in public snapshot "
                          f"(secret or nested). Use [bold]settings show[/bold].")
            return
        console.print(f"[red]Unknown key:[/red] {key}")
        console.print("[dim]Run: remedy settings keys[/dim]")
        raise SystemExit(1)

    if sub == "set":
        patch: dict[str, Any] = {}
        raw_json = getattr(args, "json_patch", None)
        if raw_json:
            try:
                loaded = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                console.print(f"[red]Invalid --json:[/red] {exc}")
                raise SystemExit(1) from exc
            if not isinstance(loaded, dict):
                console.print("[red]--json must be an object[/red]")
                raise SystemExit(1)
            patch.update(loaded)
        for pair in getattr(args, "pairs", None) or []:
            if "=" not in pair:
                console.print(f"[red]Expected KEY=VALUE, got:[/red] {pair}")
                raise SystemExit(1)
            k, _, v = pair.partition("=")
            k = k.strip()
            if not k:
                console.print(f"[red]Empty key in:[/red] {pair}")
                raise SystemExit(1)
            patch[k] = _parse_setting_value(v)
        if not patch:
            console.print("[red]No settings to apply. Use KEY=VALUE or --json '{...}'[/red]")
            raise SystemExit(1)
        try:
            result = asyncio.run(apply_settings_update(patch))
        except ValueError as exc:
            console.print(f"[red]Settings rejected:[/red] {exc}")
            raise SystemExit(1) from exc
        except OSError as exc:
            console.print(f"[red]Write failed:[/red] {exc}")
            raise SystemExit(1) from exc
        console.print(f"[green]{result.get('message', 'saved')}[/green]")
        changes = result.get("changes") or []
        if changes:
            console.print(f"[dim]Changed:[/dim] {', '.join(str(c) for c in changes)}")
        # Echo key fields that were in the patch
        show = {k: result[k] for k in patch if k in result}
        if show:
            console.print_json(data=show)
        return

    console.print("[dim]Usage: remedy settings show|get|set|keys[/dim]")


def _cmd_computer(args) -> None:
    """``remedy computer status|host`` — computer-use host control for CLI."""
    try:
        home = resolve_cli_home(args.home)
    except UnsafeHomeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    os.environ.setdefault("REMEDY_HOME", str(home))

    sub = getattr(args, "computer_cmd", None) or "status"

    if sub == "status":
        from remedy.core.computer.cli_host import get_local_computer_host
        from remedy.core.computer.host_bridge import get_host_bridge

        bridge = get_host_bridge(home)
        local = get_local_computer_host(home)
        st = local.status()
        console.print(Panel("[bold]Computer use[/bold]", border_style="green"))
        console.print(
            f"  host_connected:  "
            f"{'[green]True[/green]' if bridge.host_connected() else '[yellow]False[/yellow]'}"
        )
        console.print(f"  pending_jobs:    {bridge.pending_count()}")
        console.print(
            f"  CLI host:        "
            f"{'[green]running[/green]' if local.running else '[dim]stopped[/dim]'}"
        )
        console.print(f"  jobs_completed:  {st.get('jobs_completed', 0)}")
        if st.get("last_action"):
            console.print(f"  last_action:     {st['last_action']}")
        if st.get("last_error"):
            console.print(f"  last_error:      [red]{st['last_error']}[/red]")
        console.print(f"  home:            {st.get('home')}")
        console.print(
            "\n[dim]Desktop tools (app/windows/click) work in-process.\n"
            "Browser rail needs Remedy Desktop, or start: remedy computer host start\n"
            "Chat starts the CLI host automatically unless --no-computer-host.[/dim]"
        )
        return

    if sub == "host":
        from remedy.core.computer.cli_host import (
            get_local_computer_host,
            start_cli_computer_host,
            stop_cli_computer_host,
        )

        action = str(getattr(args, "action", "start") or "start").lower()
        if action == "stop":
            stop_cli_computer_host()
            console.print("[green]CLI computer host stopped.[/green]")
            return

        host = start_cli_computer_host(home)
        st = host.status()
        connected = st.get("host_connected")
        console.print(
            f"[green]CLI computer host started.[/green] "
            f"host_connected={connected}"
        )

        if getattr(args, "api", False):
            # Optional HTTP poller against remedy serve (separate process bridge)
            try:
                import sys
                from pathlib import Path as _P

                # Walk up from this file (src tree *or* site-packages) until
                # scripts/computer_host_poller.py exists. Counting parents
                # assumed a src/ layout and missed editable/CI installs.
                here = _P(__file__).resolve()
                poller = None
                repo = here.parent
                for parent in here.parents:
                    candidate = parent / "scripts" / "computer_host_poller.py"
                    if candidate.is_file():
                        poller = candidate
                        repo = parent
                        break
                if poller is None:
                    poller = here.parents[4] / "scripts" / "computer_host_poller.py"
                if poller.is_file():
                    import subprocess

                    env = os.environ.copy()
                    env.setdefault("REMEDY_API", "http://127.0.0.1:7400")
                    env.setdefault("REMEDY_HOME", str(home))
                    popen_kw: dict[str, Any] = {
                        "cwd": str(repo),
                        "env": env,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    }
                    if sys.platform == "win32":
                        popen_kw["creationflags"] = getattr(
                            subprocess, "CREATE_NO_WINDOW", 0
                        )
                    subprocess.Popen([sys.executable, str(poller)], **popen_kw)
                    console.print(
                        "[dim]Also started HTTP host poller → "
                        f"{env.get('REMEDY_API')}[/dim]"
                    )
                else:
                    console.print(f"[yellow]HTTP poller script missing:[/yellow] {poller}")
            except Exception as exc:
                console.print(f"[yellow]HTTP poller not started:[/yellow] {exc}")

        if action == "run":
            console.print("[dim]Foreground host running — Ctrl+C to stop[/dim]")
            try:
                while host.running:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopping…[/dim]")
            finally:
                stop_cli_computer_host()
            return
        return

    console.print("[dim]Usage: remedy computer status|host [start|stop|run][/dim]")



