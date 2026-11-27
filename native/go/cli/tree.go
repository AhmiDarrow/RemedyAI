package cli

// Root is the whole `remedy` command tree. It mirrors the flags and
// arguments the Python argparse parser accepted, so scripts and the manual
// keep working unchanged.
func Root() *Command {
	str := func(name, def, help string) Flag { return Flag{Name: name, Kind: FlagString, Default: def, Help: help} }
	boolf := func(name, help string) Flag { return Flag{Name: name, Kind: FlagBool, Help: help} }
	intf := func(name, def, help string) Flag { return Flag{Name: name, Kind: FlagInt, Default: def, Help: help} }
	floatf := func(name, def, help string) Flag { return Flag{Name: name, Kind: FlagFloat, Default: def, Help: help} }
	hidden := func(f Flag) Flag { f.Hidden = true; return f }
	arg := func(name, help string) Arg { return Arg{Name: name, Help: help} }
	opt := func(name, def, help string) Arg { return Arg{Name: name, Optional: true, Default: def, Help: help} }
	leaf := func(name, help string, run func(*Ctx) int, flags []Flag, args ...Arg) *Command {
		return &Command{Name: name, Help: help, Run: run, Flags: flags, Args: args}
	}
	group := func(name, help string, required bool, subs ...*Command) *Command {
		return &Command{Name: name, Help: help, Sub: subs, SubRequired: required}
	}

	memory := group("memory", "Memory operations", true,
		leaf("search", "Search memory", cmdMemory, []Flag{intf("limit", "10", "")}, arg("query", "Search query")),
		leaf("list", "List recent memories", cmdMemory, []Flag{intf("limit", "20", ""), str("type", "", "")}),
		leaf("add", "Add a memory entry", cmdMemory, []Flag{
			str("type", "note", ""), str("tags", "", ""), floatf("importance", "0.5", ""),
		}, arg("title", "Entry title"), arg("content", "Entry content")),
		leaf("consolidate", "Consolidate memory entries", cmdMemory, []Flag{intf("max-entries", "100", "")}, arg("session_id", "Session ID to consolidate")),
		leaf("repair", "Run memory integrity checks", cmdMemory, []Flag{boolf("vacuum", "Also vacuum database")}),
		leaf("backup", "Backup the memory database", cmdMemory, nil),
	)
	user := group("user", "User profile operations", true,
		leaf("show", "Show user profile", cmdUser, nil),
		leaf("facts", "Search user facts", cmdUser, []Flag{intf("limit", "10", "")}, opt("query", "", "")),
	)
	session := group("session", "Session management", true,
		leaf("start", "Start a new session", cmdSession, nil),
		leaf("end", "End current session", cmdSession, nil),
	)
	skill := group("skill", "Skill operations", true,
		leaf("list", "List registered skills (hides learned probation by default)", cmdSkill, []Flag{
			boolf("all", "Include auto-learned probation skills (tool-chain noise)"),
			boolf("learned", "Show only auto-learned skills"),
		}),
		leaf("discover", "Discover skills in a directory", cmdSkill, []Flag{boolf("no-recurse", "")}, arg("path", "Directory to scan")),
		leaf("info", "Show skill details", cmdSkill, nil, arg("name", "Skill name")),
		leaf("load", "Load a single skill", cmdSkill, nil, arg("path", "Path to skill directory or SKILL.md")),
		leaf("run", "Run a skill's scripts", cmdSkill, []Flag{str("script", "", "Specific script to run")}, arg("name", "Skill name to run")),
		leaf("test", "Validate and test a skill", cmdSkill, nil, arg("name", "Skill name to validate")),
		leaf("export", "Export a skill to another format", cmdSkill, []Flag{
			{Name: "format", Kind: FlagString, Default: "native", Choices: []string{"native", "hermes", "openclaw", "zip"}},
		}, arg("name", "Skill name to export"), arg("output", "Output directory")),
	)
	tool := group("tool", "Tool operations", true,
		leaf("list", "List registered tools", cmdTool, nil),
		leaf("search", "Search tools", cmdTool, nil, arg("query", "Search query")),
		leaf("stats", "Tool invocation statistics", cmdTool, nil),
		leaf("run", "Execute a tool through the runtime", cmdTool, []Flag{
			str("args", "{}", "JSON arguments"), floatf("timeout", "30.0", ""), intf("retries", "0", ""),
		}, arg("name", "Tool name")),
	)
	execCmd := leaf("exec", "Execute a command in the sandbox", cmdExec, []Flag{
		floatf("timeout", "30.0", ""), str("workdir", "", ""), str("shell", "", "Shell to use (pwsh, cmd, bash)"),
	}, Arg{Name: "cmdline", Rest: true, Help: "Command and arguments to run"})
	learn := group("learn", "Learning loop operations", true,
		leaf("reflect", "Reflect on a completed task", cmdLearn, []Flag{str("steps", "[]", "JSON trace steps")}, arg("task_title", "Task title to reflect on")),
		leaf("history", "Show learning history", cmdLearn, []Flag{intf("limit", "20", "")}),
		leaf("changelog", "Show refinement changelog", cmdLearn, nil, opt("skill_name", "", "Optional skill name filter")),
		leaf("stats", "Show skill execution stats", cmdLearn, []Flag{str("skill", "", "")}),
		leaf("sync", "Sync learning events to memory store", cmdLearn, nil),
	)
	handoff := group("handoff", "Handoff note operations", true,
		leaf("create", "Create a handoff note", cmdHandoff, []Flag{str("tags", "", "")}, arg("title", "Note title"), arg("content", "Note content")),
		leaf("list", "List handoff notes", cmdHandoff, []Flag{intf("limit", "20", "")}),
		leaf("search", "Search handoffs", cmdHandoff, []Flag{intf("limit", "10", "")}, arg("query", "Search query")),
		leaf("show", "Show a handoff note", cmdHandoff, nil, arg("id", "Handoff note ID")),
	)
	migrate := group("migrate", "Migration operations", true,
		leaf("hermes", "Migrate from Hermes Agent", cmdMigrate, []Flag{boolf("no-copy", "")}, arg("path", "Path to Hermes skills directory")),
		leaf("openclaw", "Migrate from OpenClaw", cmdMigrate, []Flag{boolf("no-copy", "")}, arg("path", "Path to OpenClaw skills directory")),
	)
	gateway := group("gateway", "Gateway operations", true,
		leaf("start", "Start the gateway daemon", cmdGateway, []Flag{
			str("telegram-token", "", "Telegram bot token (prefer TELEGRAM_BOT_TOKEN env; argv is visible in process lists)"),
			str("discord-token", "", "Discord bot token (prefer DISCORD_BOT_TOKEN env; argv is visible in process lists)"),
			str("slack-token", "", "Slack bot token (prefer SLACK_BOT_TOKEN env; argv is visible in process lists)"),
			floatf("heartbeat", "60.0", ""),
		}),
		leaf("status", "Show gateway status", cmdGateway, nil),
		leaf("serve", "Start remedy-runtime (Go local API)", cmdGateway, nil),
		leaf("channels", "List available channels", cmdGateway, nil),
	)
	config := group("config", "Configuration management", true,
		leaf("init", "Create default config file", cmdConfig, nil),
		leaf("show", "Show current configuration", cmdConfig, nil),
		leaf("path", "Show config file path", cmdConfig, nil),
	)
	settings := &Command{Name: "settings", Help: "View and update agent settings (same keys as Desktop Settings)", Run: cmdSettings, Sub: []*Command{
		leaf("show", "Show public settings snapshot (no secrets)", cmdSettings, nil),
		leaf("keys", "List settable settings keys", cmdSettings, nil),
		leaf("get", "Get one setting value", cmdSettings, nil, arg("key", "Setting key (e.g. llm_model, thinking_level)")),
		leaf("set", "Set one or more settings", cmdSettings, []Flag{str("json", "", "JSON object patch instead of KEY=VALUE pairs")},
			Arg{Name: "pairs", Many: true, Help: "KEY=VALUE pairs (bool/int/float/json auto-parsed). Example: thinking_level=high tool_process=full"}),
	}}
	computer := &Command{Name: "computer", Help: "Computer-use host status and CLI host control", Run: cmdComputer, Sub: []*Command{
		leaf("status", "Show host_connected, pending jobs, CLI host state", cmdComputer, nil),
		leaf("host", "Start/stop the in-process CLI computer host (system browser + desktop)", cmdComputer,
			nil,
			Arg{Name: "action", Optional: true, Default: "start", Help: "start (background), stop, or run (foreground until Ctrl+C)"}),
	}}
	provider := opt("provider", "xai", "Provider id (default: xai)")
	auth := group("auth", "Provider authentication (OAuth / API keys)", true,
		leaf("login", "Sign in to a provider (device-code OAuth)", cmdAuth, nil, provider),
		leaf("logout", "Sign out / clear stored credentials", cmdAuth, nil, provider),
		leaf("status", "Show auth status for a provider", cmdAuth, nil, provider),
		leaf("apikey", "Store a console API key for a provider", cmdAuth, nil, provider, opt("api_key", "", "API key (prompted if omitted)")),
	)
	chat := leaf("chat", "Launch interactive chat with the Remedy agent", cmdChat, []Flag{
		str("config", "", ""), str("session", "", "Resume an existing session"),
		boolf("no-memory", "Don't persist conversation to memory"),
		boolf("skip-setup", "Skip first-run setup wizard and remember choice (won't ask again)"),
		boolf("force-setup", "Force the setup wizard even if setup was completed"),
		boolf("no-computer-host", "Do not start the in-process CLI computer host (desktop tools still work)"),
		boolf("computer-host", "Start CLI computer host (default: off so Desktop owns the rail)"),
	})
	open := leaf("open", "Open the WebUI of a running `remedy serve` with a one-time link", cmdOpen, []Flag{
		str("host", "127.0.0.1", "Loopback host of remedy serve"), intf("port", "7400", ""),
		boolf("no-browser", "Print the link instead of opening it"),
	})
	serve := leaf("serve", "Start remedy-runtime on the local API (default 127.0.0.1:7400)", cmdServe, []Flag{
		str("host", "127.0.0.1", "Loopback bind host only (remedy-runtime refuses non-loopback)"),
		intf("port", "7400", ""),
		// Accepted no-ops: older Desktop builds and scripts still pass these.
		hidden(boolf("skip-setup", "")), hidden(boolf("force-setup", "")), hidden(str("config", "", "")),
		hidden(boolf("computer-host", "")), hidden(boolf("no-computer-host", "")),
	})
	mcp := group("mcp", "MCP host: expose local skills to external apps (stdio)", true,
		leaf("serve", "Run MCP server on stdio, or Streamable HTTP with --http (not :7400)", cmdMCP, []Flag{
			boolf("http", "Serve Streamable HTTP MCP (and A2A) on a loopback port; default 127.0.0.1:7401"),
			str("host", "127.0.0.1", "Bind host for --http (loopback default)"),
			intf("port", "7401", "Bind port for --http (must not be 7400)"),
		}),
	)
	desktop := group("desktop", "Desktop app management", true,
		leaf("install", "Install desktop Node dependencies", cmdDesktop, nil),
		leaf("dev", "Start desktop dev server", cmdDesktop, []Flag{boolf("open", "Open browser")}),
		leaf("build", "Build desktop for production", cmdDesktop, nil),
		leaf("launch", "Launch the installed desktop app (Windows only)", cmdDesktop, nil),
		leaf("status", "Check if the desktop server is running", cmdDesktop, nil),
	)
	uninstall := leaf("uninstall", "Uninstall Remedy", cmdUninstall, []Flag{
		boolf("purge", "Full wipe: delete entire ~/.remedy/ (config + skills + memory)"),
		boolf("config", "Remove configuration / auth (config.toml, desktop.json, auth/)"),
		boolf("skills", "Remove ~/.remedy/skills"),
		boolf("dry-run", "Show what would be removed"),
	})
	update := leaf("update", "Check for and apply updates", cmdUpdate, []Flag{boolf("check", "Check only, don't apply")})
	relay := leaf("connect-relay", "Owner-run Grove Connect relay (forwards framed blobs, does not decrypt)", cmdConnectRelay, []Flag{
		str("host", "127.0.0.1", "Chosen IPv4 to bind (not 0.0.0.0 / * / ::)"), intf("port", "7402", ""),
	})
	setup := leaf("setup", "Interactive setup wizard", cmdSetup, []Flag{
		boolf("quick", "Minimal prompts, use defaults"),
		boolf("skip-providers", "Skip LLM provider configuration"),
		boolf("skip-messaging", "Skip messaging app configuration"),
		boolf("skip-skills", "Skip skill discovery configuration"),
	})

	return &Command{
		Name: "remedy",
		Help: "Remedy: The self-improving, multi-channel AI agent framework.\n\n" +
			"Start the local API with:\n  remedy serve\n" +
			"Desktop app (recommended for daily use):\n  https://github.com/AhmiDarrow/RemedyAI/releases/latest",
		Flags: []Flag{
			{Name: "home", Kind: FlagString, Default: DefaultHome(), Help: "Remedy home directory (default: $REMEDY_HOME or ~/.remedy)"},
		},
		Sub: []*Command{
			memory, user, session, skill, tool, execCmd, learn, handoff, migrate, gateway,
			config, settings, computer, auth, chat, open, serve, mcp, desktop, uninstall,
			update, relay, setup,
		},
	}
}
