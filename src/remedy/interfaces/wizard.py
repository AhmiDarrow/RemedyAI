"""Interactive setup wizard for Remedy.

Walks the user through initial configuration: agent identity, providers,
messaging apps, skill directories, and runtime settings.

Sections can be skipped individually with --skip-* flags or interactively.

Usage:
    remedy setup                                            # run the wizard
    remedy setup --quick                                    # non-interactive, use defaults
    remedy setup --skip-providers                           # skip LLM provider config
    remedy setup --skip-messaging                           # skip messaging app config
    remedy setup --skip-skills                              # skip skill discovery
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from remedy.core.security import validate_skill_name

console = Console()


WELCOME_ART = [
    r"██████╗ ███████╗███╗   ███╗███████╗██████╗ ██╗   ██╗",
    r"██╔══██╗██╔════╝████╗ ████║██╔════╝██╔══██╗╚██╗ ██╔╝",
    r"██████╔╝█████╗  ██╔████╔██║█████╗  ██║  ██║ ╚████╔╝ ",
    r"██╔══██╗██╔══╝  ██║╚██╔╝██║██╔══╝  ██║  ██║  ╚██╔╝  ",
    r"██║  ██║███████╗██║ ╚═╝ ██║███████╗██████╔╝   ██║   ",
    r"╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚══════╝╚═════╝    ╚═╝   ",
    r"",
    r"  The self-improving, multi-channel AI agent framework.",
]

WELCOME_ASCII = [
    r" #####  ######  #    #  ######  ####   #   # ",
    r" #    #  #       ##  ##  #       #   #   # #  ",
    r" #####   #####   # ## #  #####   #   #    #   ",
    r" #  #    #       #    #  #       #   #    #   ",
    r" #   ##  ######  #    #  ######  ####     #   ",
    r"",
    r"  The self-improving, multi-channel AI agent framework.",
]


CHANNELS = {
    "cli": ("CLI (command-line)", True, None),
    "web": ("REST API + Web dashboard", False, None),
    "telegram": ("Telegram Bot", False, "bot_token"),
    "discord": ("Discord Bot", False, "bot_token"),
    "slack": ("Slack Bot", False, "bot_token"),
}

PERSONAS = {
    "default": "Balanced, helpful agent",
    "concise": "Short, direct responses only",
    "verbose": "Detailed explanations with context",
    "sarcastic": "Witty, dry humor (experimental)",
    "minimal": "Commands only, no conversation",
}

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def run_wizard(
    quick: bool = False,
    skip_providers: bool = False,
    skip_messaging: bool = False,
    skip_skills: bool = False,
) -> Path:
    """Run the interactive setup wizard. Returns the config file path."""
    _print_welcome()

    config: dict[str, Any] = {}

    # -- Step 1: Agent identity -----------------------------------------------
    console.rule("[bold]Step 1: Agent Identity")

    if quick:
        config["name"] = "Remedy"
        config["persona"] = "default"
        console.print("[dim]Quick mode: using defaults (Remedy / default persona)[/dim]")
    else:
        config["name"] = Prompt.ask("Agent name", default="Remedy", console=console)
        try:
            validate_skill_name(config["name"])
        except Exception:
            console.print("[yellow]Name contains special chars; using 'remedy'[/yellow]")
            config["name"] = "remedy"
        config["persona"] = _pick_persona()

    # -- Step 2: Providers (LLM provider configuration) -----------------------
    console.rule("[bold]Step 2: LLM Provider")
    _configure_llm_provider(config, quick=quick, skip=skip_providers)

    # -- Step 3: Messaging Apps -----------------------------------------------
    console.rule("[bold]Step 3: Messaging Apps")
    _configure_messaging(config, quick=quick, skip=skip_messaging)

    # -- Step 4: Runtime Settings ---------------------------------------------
    console.rule("[bold]Step 4: Runtime Settings")
    config["home_dir"] = Path("~/.remedy").expanduser().as_posix()

    if quick:
        config["log_level"] = "INFO"
        config["auto_approve_threshold"] = 0.8
        config["allow_skill_creation"] = True
        console.print("[dim]Quick mode: INFO logging, 0.8 auto-approve[/dim]")
    else:
        config["log_level"] = _pick_option("Log level", LOG_LEVELS, default="INFO")
        config["auto_approve_threshold"] = FloatPrompt.ask(
            "Auto-approve threshold for skill creation",
            default=0.8,
            console=console,
        )
        config["allow_skill_creation"] = Confirm.ask(
            "Allow automatic skill creation from traces?", default=True, console=console
        )

    # -- Step 5: Skills -------------------------------------------------------
    console.rule("[bold]Step 5: Skill Discovery")
    _configure_skills(config, quick=quick, skip=skip_skills)

    # -- Gateway & execution defaults -----------------------------------------
    config["gateway"] = {"heartbeat_interval": 60, "rate_limit": 120}
    config["execution"] = {"default_timeout": 30, "max_retries": 3, "retry_backoff": 1.0}

    # -- Review and confirm ---------------------------------------------------
    console.rule("[bold]Review")
    _print_config_summary(config)

    if quick:
        console.print("[dim]Quick mode: auto-saving[/dim]")
    elif not Confirm.ask("Save this configuration?", default=True, console=console):
        console.print("[yellow]Setup cancelled. No changes made.[/yellow]")
        sys.exit(0)

    # -- Write config ---------------------------------------------------------
    cfg_path = _write_config(config)

    # -- Final ----------------------------------------------------------------
    console.print()
    console.print(
        Panel.fit(
            f"Agent [bold]{config['name']}[/bold] is ready.\n\n"
            f"Config:  {cfg_path}\n"
            f"Memory:  {_db_path(config)}\n\n"
            "[bold]Next steps:[/bold]\n"
            f"  remedy serve                  Start the API server\n"
            f"  remedy memory add 'hello' 'My first memory'\n"
            f"  remedy session start           Begin a working session\n"
            f"  remedy --help                  See all commands",
            title="Setup Complete",
            border_style="green",
        )
    )

    return cfg_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _supports_unicode() -> bool:
    """Check if stdout can handle Unicode box-drawing characters."""
    try:
        # Try encoding a box-drawing character to the terminal encoding
        "\u2588\u2502".encode(sys.stdout.encoding)
        return True
    except (UnicodeError, LookupError):
        return False


def _print_welcome() -> None:
    """Print the welcome banner with fallback for legacy terminals."""
    if _supports_unicode():
        art = WELCOME_ART
    else:
        art = WELCOME_ASCII

    for i, line in enumerate(art):
        if 0 <= i < 6:
            style = "bold green" if i % 2 == 0 else "bold #a855f7"
        else:
            style = "dim"
        console.print(line, style=style)

    console.print(
        Panel.fit(
            "Welcome to the Remedy setup wizard.\n"
            "I'll walk you through configuring your agent.\n"
            "You can always change these later in ~/.remedy/config.toml",
            title="Setup Wizard",
            border_style="bold green",
        )
    )


def _pick_persona() -> str:
    table = Table(title="Available Personas")
    table.add_column("#", style="dim")
    table.add_column("Name")
    table.add_column("Description")
    items = list(PERSONAS.items())
    for i, (name, desc) in enumerate(items, 1):
        table.add_row(str(i), name, desc)
    console.print(table)
    choice = IntPrompt.ask(
        f"Select persona (1-{len(items)})", default=1, console=console
    )
    idx = max(1, min(choice, len(items))) - 1
    return items[idx][0]


def _pick_channels() -> list[str]:
    table = Table(title="Channel Setup")
    table.add_column("#", style="dim")
    table.add_column("Channel")
    table.add_column("Requires Token", style="dim")
    items = list(CHANNELS.items())
    for i, (key, (label, enabled, token)) in enumerate(items, 1):
        table.add_row(str(i), label, token or "none")
    console.print(table)
    console.print(
        "[dim]Enter channel numbers to enable (comma-separated, e.g. 1,3,4)\n"
        "or press Enter for CLI only.[/dim]"
    )
    raw = Prompt.ask("Channels", default="1", console=console)
    selected: list[str] = []
    seen = set()
    for part in raw.replace(" ", "").split(","):
        try:
            idx = int(part) - 1
            if 0 <= idx < len(items):
                key = items[idx][0]
                if key not in seen:
                    selected.append(key)
                    seen.add(key)
        except ValueError:
            pass
    return selected or ["cli"]


LLM_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "label": "Anthropic",
        "model": "claude-sonnet-4-20250514",
        "base_url": "https://api.anthropic.com",
    },
    "google": {
        "label": "Google Gemini",
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    },
    "openrouter": {
        "label": "OpenRouter",
        "model": "openrouter/auto",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "label": "Ollama (local)",
        "model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "model": "",
        "base_url": "",
    },
}


def _configure_llm_provider(config: dict, quick: bool = False, skip: bool = False) -> None:
    """Configure the LLM provider (backend model)."""
    if quick or skip:
        if quick:
            config["llm_model"] = LLM_PROVIDERS["openai"]["model"]
            config["llm_base_url"] = LLM_PROVIDERS["openai"]["base_url"]
            config["llm_api_key"] = ""
            console.print("[dim]Quick mode: OpenAI / gpt-4o-mini (no API key)[/dim]")
        if skip:
            console.print("[dim]Skipping provider setup[/dim]")
        return

    if not Confirm.ask(
        "Configure an LLM provider?", default=True, console=console
    ):
        console.print("[dim]Skipping provider setup. Set llm_api_key later in config.[/dim]")
        return

    # Pick provider
    table = Table(title="Available LLM Providers")
    table.add_column("#", style="dim")
    table.add_column("Provider")
    table.add_column("Default Model")
    items = list(LLM_PROVIDERS.items())
    for i, (_key, info) in enumerate(items, 1):
        table.add_row(str(i), info["label"], info["model"] or "(custom)")
    console.print(table)

    choice = IntPrompt.ask(
        f"Select provider (1-{len(items)})", default=1, console=console
    )
    idx = max(1, min(choice, len(items))) - 1
    provider_key, provider_info = items[idx]
    console.print(f"[green]Selected: {provider_info['label']}[/green]")

    # API key
    api_key_prompt = f"  {provider_info['label']} API key (or press Enter to skip)"
    api_key = getpass.getpass(api_key_prompt + ": ").strip()
    config["llm_api_key"] = api_key

    # Model name
    default_model = provider_info["model"]
    if provider_key == "custom" or not default_model:
        model = Prompt.ask("  Model name", default="gpt-4o-mini", console=console)
    else:
        model = Prompt.ask("  Model name", default=default_model, console=console)
    config["llm_model"] = model

    # Base URL
    default_base = provider_info["base_url"]
    if provider_key == "custom" or not default_base:
        base_url = Prompt.ask(
            "  API base URL", default="https://api.openai.com/v1", console=console
        )
    else:
        base_url = Prompt.ask("  API base URL", default=default_base, console=console)
    config["llm_base_url"] = base_url.rstrip("/")

    if api_key:
        console.print(f"  [green]{provider_info['label']} API key saved[/green]")
    else:
        console.print("  [dim]No API key set. Set REMEDY_LLM_API_KEY env var or edit config.[/dim]")

    console.print(f"  [dim]Model: {model} | Base URL: {base_url}[/dim]")


def _configure_messaging(config: dict, quick: bool = False, skip: bool = False) -> None:
    """Configure messaging app channels and their tokens."""
    if quick or skip:
        config["enabled_channels"] = ["cli"]
        if quick:
            console.print("[dim]Quick mode: CLI channel only[/dim]")
        if skip:
            console.print("[dim]Skipping messaging app setup[/dim]")
        return

    if not Confirm.ask(
        "Configure messaging apps (Telegram, Discord, Slack)?",
        default=False,
        console=console,
    ):
        config["enabled_channels"] = ["cli"]
        console.print("[dim]Skipping messaging setup. CLI channel enabled by default.[/dim]")
        return

    config["enabled_channels"] = _pick_channels()

    needs_tokens = {
        "telegram": "Telegram Bot Token (from @BotFather)",
        "discord": "Discord Bot Token (from Discord Developer Portal)",
        "slack": "Slack Bot Token (from Slack API)",
    }
    for channel, prompt_text in needs_tokens.items():
        if channel in config.get("enabled_channels", []):
            console.print(f"\n[bold]{prompt_text}[/bold]")
            if Confirm.ask(f"Do you have a {channel.title()} token?", default=False, console=console):
                token = getpass.getpass(f"  {channel.title()} token: ").strip()
                config[channel] = {"bot_token": token}
                console.print(f"  [green]{channel.title()} token saved[/green]")
            else:
                console.print(f"  [dim]Skipped. Add it later in ~/.remedy/config.toml under [{channel}][/dim]")
                config[channel] = {"bot_token": ""}


def _configure_skills(config: dict, quick: bool = False, skip: bool = False) -> None:
    """Configure skill discovery directories."""
    if quick or skip:
        config["skills_dir"] = []
        if quick:
            console.print("[dim]Quick mode: no skill scanning[/dim]")
        if skip:
            console.print("[dim]Skipping skill discovery setup[/dim]")
        return

    if not Confirm.ask(
        "Configure skill directories?", default=True, console=console
    ):
        config["skills_dir"] = []
        console.print("[dim]Skipping skill discovery[/dim]")
        return

    paths: list[str] = []
    while True:
        p = Prompt.ask(
            "Skills directory path (Enter empty to finish)",
            default="skills" if not paths else "",
            console=console,
        )
        if not p:
            break
        paths.append(p)
    config["skills_dir"] = paths
    if paths:
        console.print(f"  [green]Will scan: {', '.join(paths)}[/green]")


def _pick_option(prompt: str, options: list[str], default: str) -> str:
    for i, opt in enumerate(options, 1):
        marker = " [default]" if opt == default else ""
        console.print(f"  {i}. {opt}{marker}")
    choice = Prompt.ask(prompt, default=default, console=console)
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return choice if choice in options else default


def _print_config_summary(config: dict) -> None:
    table = Table(title="Configuration Summary")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("Agent", config.get("name", "Remedy"))
    table.add_row("Persona", config.get("persona", "default"))
    # Provider row
    model = config.get("llm_model", "gpt-4o-mini")
    api_key = config.get("llm_api_key", "")
    provider_label = model
    if api_key:
        provider_label += " [green](key set)[/green]"
    else:
        provider_label += " [yellow](no key)[/yellow]"
    table.add_row("LLM Provider", provider_label)
    # Channels
    table.add_row("Channels", ", ".join(config.get("enabled_channels", [])) or "cli")
    table.add_row("Log Level", config.get("log_level", "INFO"))
    table.add_row("Auto-approve", str(config.get("auto_approve_threshold", 0.8)))
    allow = config.get("allow_skill_creation", True)
    table.add_row("Allow skill creation", "[green]yes[/green]" if allow else "[red]no[/red]")
    table.add_row("Skills dir", ", ".join(config.get("skills_dir", [])) or "(none)")
    table.add_row("Home", config.get("home_dir", "~/.remedy"))
    table.add_row("Memory DB", str(_db_path(config)))
    for ch in ("telegram", "discord", "slack"):
        if ch in config and config[ch].get("bot_token"):
            table.add_row(f"{ch.title()} token", "[green]configured[/green]")
        elif ch in config.get("enabled_channels", []):
            table.add_row(f"{ch.title()} token", "[yellow]not set[/yellow]")
    console.print(table)


def _write_config(config: dict) -> Path:
    home = Path(config.get("home_dir", "~/.remedy")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.toml"
    lines = ["# Remedy AI Configuration", "# Generated by remedy setup wizard", ""]

    for key, value in config.items():
        if key in ("home_dir",):
            # Normalize Windows backslashes to forward slashes for TOML safety
            safe = value.replace("\\", "/")
            lines.append(f'{key} = "{safe}"')
        elif key in ("name", "persona", "log_level", "llm_model", "llm_base_url") or key == "llm_api_key":
            if value:
                lines.append(f'{key} = "{value}"')
        elif key == "auto_approve_threshold":
            lines.append(f"{key} = {value}")
        elif key == "allow_skill_creation":
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif key == "enabled_channels":
            items = ", ".join(f'"{c}"' for c in value)
            lines.append(f"{key} = [{items}]")
        elif key == "skills_dir":
            if isinstance(value, list) and value:
                items = ", ".join(f'"{s}"' for s in value)
                lines.append(f"{key} = [{items}]")
        elif key in ("telegram", "discord", "slack"):
            if isinstance(value, dict) and value.get("bot_token"):
                lines.append(f"\n[{key}]")
                lines.append(f'bot_token = "{value["bot_token"]}"')
        elif key in ("gateway", "execution"):
            if isinstance(value, dict):
                lines.append(f"\n[{key}]")
                for k, v in value.items():
                    if isinstance(v, str):
                        lines.append(f'{k} = "{v}"')
                    else:
                        lines.append(f"{k} = {v}")

    content = "\n".join(lines) + "\n"
    cfg_path.write_text(content, encoding="utf-8")
    console.print(f"\n[green]Config written to[/green] {cfg_path}")
    return cfg_path


def _db_path(config: dict) -> Path:
    home = Path(config.get("home_dir", "~/.remedy")).expanduser()
    return home / "memory.db"
