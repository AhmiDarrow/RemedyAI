"""argparse construction for the remedy CLI."""

from __future__ import annotations

import argparse

from remedy import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remedy",
        description="Remedy: The self-improving, multi-channel AI agent framework.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For the best experience, download the desktop app:\n"
               "  https://github.com/AhmiDarrow/RemedyAI/releases\n"
               "Power users: use 'remedy serve' to run the API server.",
    )
    parser.add_argument("--version", action="version", version=f"remedy {__version__}")
    parser.add_argument(
        "--home",
        default="~/.remedy",
        help="Remedy home directory (default: ~/.remedy)",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # remedy memory search <query>
    mem = sub.add_parser("memory", help="Memory operations")
    mem_sub = mem.add_subparsers(dest="memory_cmd", required=True)
    mem_search = mem_sub.add_parser("search", help="Search memory")
    mem_search.add_argument("query", help="Search query")
    mem_search.add_argument("--limit", type=int, default=10)

    mem_list = mem_sub.add_parser("list", help="List recent memories")
    mem_list.add_argument("--limit", type=int, default=20)
    mem_list.add_argument("--type", dest="entry_type", default=None)

    mem_add = mem_sub.add_parser("add", help="Add a memory entry")
    mem_add.add_argument("title", help="Entry title")
    mem_add.add_argument("content", help="Entry content")
    mem_add.add_argument("--type", dest="entry_type", default="note")
    mem_add.add_argument("--tags", default="")
    mem_add.add_argument("--importance", type=float, default=0.5)

    mem_consolidate = mem_sub.add_parser("consolidate", help="Consolidate memory entries")
    mem_consolidate.add_argument("session_id", help="Session ID to consolidate")
    mem_consolidate.add_argument("--max-entries", type=int, default=100)

    mem_repair = mem_sub.add_parser("repair", help="Run memory integrity checks")
    mem_repair.add_argument("--vacuum", action="store_true", help="Also vacuum database")

    mem_sub.add_parser("backup", help="Backup the memory database")

    # remedy user profile|facts
    user = sub.add_parser("user", help="User profile operations")
    user_sub = user.add_subparsers(dest="user_cmd", required=True)
    user_sub.add_parser("show", help="Show user profile")
    user_facts = user_sub.add_parser("facts", help="Search user facts")
    user_facts.add_argument("query", nargs="?", default="")
    user_facts.add_argument("--limit", type=int, default=10)

    # remedy session start|end
    session = sub.add_parser("session", help="Session management")
    session_sub = session.add_subparsers(dest="session_cmd", required=True)
    session_sub.add_parser("start", help="Start a new session")
    session_sub.add_parser("end", help="End current session")

    # remedy skill discover <path>
    skill = sub.add_parser("skill", help="Skill operations")
    skill_sub = skill.add_subparsers(dest="skill_cmd", required=True)
    skill_list = skill_sub.add_parser(
        "list",
        help="List registered skills (hides learned probation by default)",
    )
    skill_list.add_argument(
        "--all",
        action="store_true",
        help="Include auto-learned probation skills (tool-chain noise)",
    )
    skill_list.add_argument(
        "--learned",
        action="store_true",
        help="Show only auto-learned skills",
    )
    skill_discover = skill_sub.add_parser("discover", help="Discover skills in a directory")
    skill_discover.add_argument("path", help="Directory to scan")
    skill_discover.add_argument("--no-recurse", action="store_true")
    skill_info = skill_sub.add_parser("info", help="Show skill details")
    skill_info.add_argument("name", help="Skill name")
    skill_load = skill_sub.add_parser("load", help="Load a single skill")
    skill_load.add_argument("path", help="Path to skill directory or SKILL.md")

    skill_run = skill_sub.add_parser("run", help="Run a skill's scripts")
    skill_run.add_argument("name", help="Skill name to run")
    skill_run.add_argument("--script", dest="script", default=None, help="Specific script to run")

    skill_test = skill_sub.add_parser("test", help="Validate and test a skill")
    skill_test.add_argument("name", help="Skill name to validate")

    skill_export = skill_sub.add_parser("export", help="Export a skill to another format")
    skill_export.add_argument("name", help="Skill name to export")
    skill_export.add_argument("output", help="Output directory")
    skill_export.add_argument("--format", dest="fmt", default="native",
                              choices=["native", "hermes", "openclaw", "zip"])

    # remedy tool list|search
    tool = sub.add_parser("tool", help="Tool operations")
    tool_sub = tool.add_subparsers(dest="tool_cmd", required=True)
    tool_sub.add_parser("list", help="List registered tools")
    tool_search = tool_sub.add_parser("search", help="Search tools")
    tool_search.add_argument("query", help="Search query")
    tool_sub.add_parser("stats", help="Tool invocation statistics")
    tool_run = tool_sub.add_parser("run", help="Execute a tool through the runtime")
    tool_run.add_argument("name", help="Tool name")
    tool_run.add_argument("--args", dest="tool_args", default="{}", help="JSON arguments")
    tool_run.add_argument("--timeout", type=float, default=30.0)
    tool_run.add_argument("--retries", type=int, default=0)

    # remedy exec <command...>
    exec_cmd = sub.add_parser("exec", help="Execute a command in the sandbox")
    exec_cmd.add_argument("--timeout", type=float, default=30.0)
    exec_cmd.add_argument("--workdir", default=None)
    exec_cmd.add_argument("--shell", default=None, help="Shell to use (pwsh, cmd, bash)")
    exec_cmd.add_argument("cmdline", nargs=argparse.REMAINDER, help="Command and arguments to run")

    # remedy learn reflect|refine|history|stats
    learn = sub.add_parser("learn", help="Learning loop operations")
    learn_sub = learn.add_subparsers(dest="learn_cmd", required=True)
    learn_reflect = learn_sub.add_parser("reflect", help="Reflect on a completed task")
    learn_reflect.add_argument("task_title", help="Task title to reflect on")
    learn_reflect.add_argument("--steps", dest="steps_json", default="[]", help="JSON trace steps")
    learn_history = learn_sub.add_parser("history", help="Show learning history")
    learn_history.add_argument("--limit", type=int, default=20)
    learn_changelog = learn_sub.add_parser("changelog", help="Show refinement changelog")
    learn_changelog.add_argument(
        "skill_name", nargs="?", default=None, help="Optional skill name filter"
    )
    learn_stats = learn_sub.add_parser("stats", help="Show skill execution stats")
    learn_stats.add_argument("--skill", dest="skill_name", default=None)
    learn_sub.add_parser("sync", help="Sync learning events to memory store")

    # remedy handoff create ...
    handoff = sub.add_parser("handoff", help="Handoff note operations")
    handoff_sub = handoff.add_subparsers(dest="handoff_cmd", required=True)
    ho_create = handoff_sub.add_parser("create", help="Create a handoff note")
    ho_create.add_argument("title", help="Note title")
    ho_create.add_argument("content", help="Note content")
    ho_create.add_argument("--tags", default="")
    ho_list = handoff_sub.add_parser("list", help="List handoff notes")
    ho_list.add_argument("--limit", type=int, default=20)
    ho_search = handoff_sub.add_parser("search", help="Search handoffs")
    ho_search.add_argument("query", help="Search query")
    ho_search.add_argument("--limit", type=int, default=10)
    ho_show = handoff_sub.add_parser("show", help="Show a handoff note")
    ho_show.add_argument("id", help="Handoff note ID")

    # remedy migrate hermes <path>
    migrate = sub.add_parser("migrate", help="Migration operations")
    migrate_sub = migrate.add_subparsers(dest="migrate_cmd", required=True)
    mig_h = migrate_sub.add_parser("hermes", help="Migrate from Hermes Agent")
    mig_h.add_argument("path", help="Path to Hermes skills directory")
    mig_h.add_argument("--no-copy", action="store_true")
    mig_oc = migrate_sub.add_parser("openclaw", help="Migrate from OpenClaw")
    mig_oc.add_argument("path", help="Path to OpenClaw skills directory")
    mig_oc.add_argument("--no-copy", action="store_true")

    # remedy gateway start|status|serve|channels
    gw = sub.add_parser("gateway", help="Gateway operations")
    gw_sub = gw.add_subparsers(dest="gateway_cmd", required=True)
    gw_start = gw_sub.add_parser("start", help="Start the gateway daemon")
    gw_start.add_argument(
        "--telegram-token",
        default="",
        help="Telegram bot token (prefer TELEGRAM_BOT_TOKEN env; argv is visible in process lists)",
    )
    gw_start.add_argument(
        "--discord-token",
        default="",
        help="Discord bot token (prefer DISCORD_BOT_TOKEN env; argv is visible in process lists)",
    )
    gw_start.add_argument(
        "--slack-token",
        default="",
        help="Slack bot token (prefer SLACK_BOT_TOKEN env; argv is visible in process lists)",
    )
    gw_start.add_argument("--heartbeat", type=float, default=60.0)
    gw_sub.add_parser("status", help="Show gateway status")
    gw_sub.add_parser("serve", help="Start the REST API server")
    gw_sub.add_parser("channels", help="List available channels")

    # remedy config init|show|path
    config_cmd = sub.add_parser("config", help="Configuration management")
    config_sub = config_cmd.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser("init", help="Create default config file")
    config_sub.add_parser("show", help="Show current configuration")
    config_sub.add_parser("path", help="Show config file path")

    # remedy settings show|get|set|keys  (parity with Desktop Settings / agent tools)
    settings_cmd = sub.add_parser(
        "settings",
        help="View and update agent settings (same keys as Desktop Settings)",
    )
    settings_sub = settings_cmd.add_subparsers(dest="settings_cmd")
    settings_sub.add_parser("show", help="Show public settings snapshot (no secrets)")
    settings_sub.add_parser("keys", help="List settable settings keys")
    settings_get = settings_sub.add_parser("get", help="Get one setting value")
    settings_get.add_argument("key", help="Setting key (e.g. llm_model, thinking_level)")
    settings_set = settings_sub.add_parser("set", help="Set one or more settings")
    settings_set.add_argument(
        "pairs",
        nargs="*",
        default=[],
        help="KEY=VALUE pairs (bool/int/float/json auto-parsed). "
        "Example: thinking_level=high tool_process=full",
    )
    settings_set.add_argument(
        "--json",
        dest="json_patch",
        default=None,
        help="JSON object patch instead of KEY=VALUE pairs",
    )

    # remedy computer status|host
    computer_cmd = sub.add_parser(
        "computer",
        help="Computer-use host status and CLI host control",
    )
    computer_sub = computer_cmd.add_subparsers(dest="computer_cmd")
    computer_sub.add_parser(
        "status",
        help="Show host_connected, pending jobs, CLI host state",
    )
    computer_host = computer_sub.add_parser(
        "host",
        help="Start/stop the in-process CLI computer host (system browser + desktop)",
    )
    computer_host.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=["start", "stop", "run"],
        help="start (background), stop, or run (foreground until Ctrl+C)",
    )
    computer_host.add_argument(
        "--api",
        action="store_true",
        help="Also start the HTTP stub poller against remedy serve (REMEDY_API)",
    )

    # remedy auth login|logout|status xai
    auth_cmd = sub.add_parser("auth", help="Provider authentication (OAuth / API keys)")
    auth_sub = auth_cmd.add_subparsers(dest="auth_cmd", required=True)
    auth_login = auth_sub.add_parser("login", help="Sign in to a provider (device-code OAuth)")
    auth_login.add_argument(
        "provider",
        nargs="?",
        default="xai",
        help="Provider id (default: xai)",
    )
    auth_logout = auth_sub.add_parser("logout", help="Sign out / clear stored credentials")
    auth_logout.add_argument(
        "provider",
        nargs="?",
        default="xai",
        help="Provider id (default: xai)",
    )
    auth_status = auth_sub.add_parser("status", help="Show auth status for a provider")
    auth_status.add_argument(
        "provider",
        nargs="?",
        default="xai",
        help="Provider id (default: xai)",
    )
    auth_key = auth_sub.add_parser("apikey", help="Store a console API key for a provider")
    auth_key.add_argument(
        "provider",
        nargs="?",
        default="xai",
        help="Provider id (default: xai)",
    )
    auth_key.add_argument("api_key", nargs="?", default=None, help="API key (prompted if omitted)")

    # remedy chat
    chat_cmd = sub.add_parser("chat", help="Launch interactive chat with the Remedy agent")
    chat_cmd.add_argument("--config", dest="config_file", default=None)
    chat_cmd.add_argument("--session", dest="session_id", default=None,
                          help="Resume an existing session")
    chat_cmd.add_argument("--no-memory", action="store_true",
                          help="Don't persist conversation to memory")
    chat_cmd.add_argument(
        "--skip-setup",
        action="store_true",
        help="Skip first-run setup wizard and remember choice (won't ask again)",
    )
    chat_cmd.add_argument(
        "--force-setup",
        action="store_true",
        help="Force the setup wizard even if setup was completed",
    )
    chat_cmd.add_argument(
        "--no-computer-host",
        action="store_true",
        help="Do not start the in-process CLI computer host (desktop tools still work)",
    )
    chat_cmd.add_argument(
        "--computer-host",
        action="store_true",
        help="Force-start CLI computer host (default: on)",
    )

    # remedy serve
    serve_cmd = sub.add_parser("serve", help="Start the full API server (with config)")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=7400)
    serve_cmd.add_argument("--config", dest="config_file", default=None)
    serve_cmd.add_argument(
        "--skip-setup",
        action="store_true",
        help="Skip first-run setup wizard and remember choice (won't ask again)",
    )
    serve_cmd.add_argument(
        "--force-setup",
        action="store_true",
        help="Force the setup wizard even if setup was completed",
    )
    serve_cmd.add_argument(
        "--computer-host",
        action="store_true",
        help="Start in-process CLI computer host so browser navigate works without Desktop",
    )
    serve_cmd.add_argument(
        "--no-computer-host",
        action="store_true",
        help="Never start CLI computer host (Desktop owns the rail)",
    )

    # remedy mcp serve — expose skills to external MCP clients (stdio)
    mcp_cmd = sub.add_parser(
        "mcp",
        help="MCP host: expose local skills to external apps (stdio)",
    )
    mcp_sub = mcp_cmd.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser(
        "serve",
        help="Run MCP server on stdio (for any MCP-compatible client config)",
    )

    # remedy desktop
    desktop_cmd = sub.add_parser("desktop", help="Desktop app management")
    desktop_sub = desktop_cmd.add_subparsers(dest="desktop_cmd", required=True)
    desktop_sub.add_parser("install", help="Install desktop Node dependencies")
    desktop_dev = desktop_sub.add_parser("dev", help="Start desktop dev server")
    desktop_dev.add_argument("--open", action="store_true", help="Open browser")
    desktop_sub.add_parser("build", help="Build desktop for production")
    desktop_sub.add_parser(
        "launch",
        help="Launch the installed desktop app (Windows only)",
    )
    desktop_sub.add_parser("status", help="Check if the desktop server is running")

    # remedy uninstall
    uninstall_cmd = sub.add_parser("uninstall", help="Uninstall Remedy")
    uninstall_cmd.add_argument(
        "--purge",
        action="store_true",
        help="Full wipe: delete entire ~/.remedy/ (config + skills + memory)",
    )
    uninstall_cmd.add_argument(
        "--config",
        action="store_true",
        help="Remove configuration / auth (config.toml, desktop.json, auth/)",
    )
    uninstall_cmd.add_argument(
        "--skills",
        action="store_true",
        help="Remove ~/.remedy/skills",
    )
    uninstall_cmd.add_argument(
        "--dry-run", action="store_true", help="Show what would be removed"
    )

    # remedy update
    update_cmd = sub.add_parser("update", help="Check for and apply updates")
    update_cmd.add_argument(
        "--check", action="store_true", help="Check only, don't apply"
    )

    # remedy setup
    setup_cmd = sub.add_parser("setup", help="Interactive setup wizard")
    setup_cmd.add_argument(
        "--quick", action="store_true", help="Minimal prompts, use defaults"
    )
    setup_cmd.add_argument(
        "--skip-providers", action="store_true",
        help="Skip LLM provider configuration",
    )
    setup_cmd.add_argument(
        "--skip-messaging", action="store_true",
        help="Skip messaging app configuration",
    )
    setup_cmd.add_argument(
        "--skip-skills", action="store_true",
        help="Skip skill discovery configuration",
    )

    return parser


