# Remedy

**Your personal AI partner — knowledge, design, code, and get-it-done on your machine.**

Remedy is a self-improving multi-channel agent for real work: research, writing,
design, software engineering, and (with permission) tasks across your PC. It is
**not** a medical, clinical, or healthcare product — the name means unsticking
problems and finishing requests, not medicine.

It combines:

- **Depth** — A self-improving learning loop that distills task traces into reusable skills
- **Memory** — Persistent SQLite+FTS5 knowledge store with structured handoff notes and session continuity
- **Breadth** — Multi-channel gateway (CLI, REST API, Telegram, Discord, Slack, webhooks)
- **Compatibility** — Native [agentskills.io](https://agentskills.io) support, plus adapters for Hermes and OpenClaw/ClawHub

---

## Download the Desktop App

The recommended way to use Remedy is the native desktop application — no Python, Node, or Rust toolchain required.

**[Download the latest installer](https://github.com/AhmiDarrow/RemedyAI/releases/latest)** (Windows)

1. Download the `.exe` installer from GitHub Releases
2. Run the installer — Remedy Desktop installs to your local app folder
3. Launch from the Start Menu — the SetupWizard guides you through provider and model configuration
4. Start chatting with `/help` to see available commands

The desktop app bundles the full Remedy server as a sidecar, so everything runs locally on your machine.

### Desktop Features

| Feature | Description |
|---------|-------------|
| **Chat UI** | Streaming markdown bubbles (you right / Remedy left); shrink-wrap size; stick-to-bottom unless you scroll up (**↓** resumes) |
| **Your name** | Settings + first-run: what Remedy calls you; avatar initials in chat |
| **Tool process** | **Off / Medium / Full** — minimal progress, labeled steps, or full raw tool args/stdout (Settings + status bar **Proc**) |
| **Icons** | Copy / edit / send / attach as icons (language-neutral); image lightbox |
| **Prompt history** | **↑ / ↓** in the composer for previous prompts (shell-style) |
| **Sessions** | Tabs; auto-title from first prompt; rename; search / pin / tags |
| **Attachments** | Drag-and-drop, paste, or attach files/images |
| **Plan/Build mode** | Toggle plan (no tools) vs build |
| **@file / /** | `@` file search; `/` slash-command menu while typing |
| **First-run setup** | Provider, workspace, persona, optional always-ready |
| **xAI Sign-in** | Device-code OAuth for Grok + console API key (per-provider secure store) |
| **Providers** | OpenAI, Anthropic, Google, DeepSeek, xAI, Groq, Mistral, OpenRouter, Ollama; Custom under Advanced |
| **Settings** | Logo menu + panel: You & Agent, project, scope, harness, tool process, themes, density, accent |
| **Access scope** | Project only / home / full user machine (opt-in) |
| **Always ready** | Startup folder (not registry Run); tray Show / **Settings** / Updates / About / Quit; close-to-tray |
| **Memory Harness** | Context prune + Session Brief + `/compact` |
| **Approvals** | High-impact shell: Approve/Deny |
| **Goals** | `/goal`, `/goals`, goal tools |
| **ComfyUI skill** | Portable local discovery + image generation into chat |
| **Themes** | System, Dark, **Neutral Dark**, Light, Emerald, Amethyst, Amber, Ocean |
| **Side panels** | Memory · Skills · Settings (status bar) |
| **Tray** | Circuit-R icon; right-click Settings and more |
| **Auto-update** | Check → download → install → relaunch (signed releases) |

### Slash commands (desktop & API)

| Command | Purpose |
|---------|---------|
| `/help` | Commands + keyboard shortcuts |
| `/new` | New session |
| `/sessions` | Recent sessions |
| `/models` | Model picker guidance |
| `/memory <q>` | Search durable memory |
| `/remember <fact>` | Store a fact in memory/profile |
| `/whoami` | What Remedy knows about you |
| `/goals` · `/goal <title>` | List / add goals |
| `/compact` · `/harness` | Memory Harness compress / show Session Brief |
| `/approve` · `/deny` | High-impact command approvals |
| `/import <folder>` | Import `.md`/`.txt` knowledge pack into memory |
| `/skills` · `/handoff` | Skills list · handoff notes |
| `/init` | Project scan helpers |

### Architecture

```
┌─────────────────────────────────┐
│        Remedy Desktop            │
│  ┌───────────────────────────┐  │
│  │   Tauri 2 Shell (Rust)    │  │
│  │   • Server lifecycle       │  │
│  │   • System tray            │  │
│  │   • Auto-updater           │  │
│  └──────────┬────────────────┘  │
│             │ spawn sidecar     │
│  ┌──────────▼────────────────┐  │
│  │   remedy serve (Python)   │  │
│  │   FastAPI on :7400        │  │
│  └──────────┬────────────────┘  │
│  ┌──────────▼────────────────┐  │
│  │   React 19 + Vite (JS)   │  │
│  │   REST + SSE client       │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

---

## Advanced / Power Users

For users who prefer CLI, custom deployments, or development:

```bash
# PyPI name is remedy-ai (the name "remedy" is a different, unrelated package)
pip install remedy-ai
# or from source:
git clone https://github.com/AhmiDarrow/RemedyAI && cd RemedyAI && uv sync
# editable local install:
pip install -e .
```

### CLI Quick Start

```bash
remedy --help                    # See all commands
remedy config init               # Create ~/.remedy/config.toml
remedy skill discover ./skills   # Load bundled & custom skills

# Launch interactive chat with the agent
remedy chat
# Type /help for commands, /exit to quit

# xAI auth (OAuth device-code or console API key)
remedy auth login xai          # Sign in with xAI (opens browser / shows code)
remedy auth status xai
remedy auth apikey xai xai-…   # Or store a console key
remedy auth logout xai
# XAI_API_KEY=… also preselects xAI on a clean config

# Start the API server
remedy serve --host 127.0.0.1 --port 7400
# Dashboard at http://127.0.0.1:7400/dashboard
# OpenAPI docs at http://127.0.0.1:7400/docs

# Desktop app management (for devs)
remedy desktop launch            # Launch the installed desktop app
remedy desktop status            # Check if the server is running
remedy desktop install           # Install Node deps (for dev)
remedy desktop dev               # Start desktop dev server
```

### Desktop Dev

```bash
remedy desktop install    # Install Node.js deps (one-time)
remedy desktop dev        # Start dev server at http://localhost:5173
# Requires: remedy serve running in another terminal

# Or full Tauri desktop build (requires Rust toolchain):
cd desktop && npm run tauri:dev
```

---

## Architecture

```
                  Channels (CLI · API · Telegram · Discord · ...)
                                   |
                           remedy-gateway
                       (event router + heartbeat)
                                   |
                            remedy-core
                   (runtime + learning loop)
                         /        |         \
              remedy-skills  remedy-tools  remedy-execution
             (unified engine)  (MCP client)   (sandbox)
                         \        |         /
                          remedy-memory
                   (SQLite+FTS5 · handoff · sessions)
```

| Module | Role |
|--------|------|
| `remedy-gateway` | Multi-channel event router with rate limiting, heartbeat, session-aware routing |
| `remedy-core` | Agent runtime, learning engine, hook/plugin system, metrics, logging |
| `remedy-memory` | Persistent SQLite+FTS5 store with handoff notes, session summaries, user profiles |
| `remedy-skills` | agentskills.io loader + Hermes/OpenClaw adapters, executor, validator, exporter |
| `remedy-tools` | MCP client (JSON-RPC stdio), tool registry with invocation stats |
| `remedy-execution` | Sandboxed runners (async subprocess, Docker), execution policy engine |
| `remedy-interfaces` | CLI (rich), FastAPI server, config system, plugin manager |
| `remedy-migrate` | Import tools from Hermes and OpenClaw setups |

---

## CLI Commands

| Command | Subcommands | Description |
|---------|------------|-------------|
| `remedy chat` | | Interactive REPL chat with the agent |
| `remedy memory` | `search`, `list`, `add`, `consolidate`, `repair`, `backup` | Persistent knowledge store with FTS5 search |
| `remedy skill` | `list`, `discover`, `info`, `load`, `run`, `test`, `export` | Skill lifecycle management |
| `remedy learn` | `reflect`, `history`, `changelog`, `stats`, `sync` | Self-improvement loop |
| `remedy handoff` | `create`, `list`, `search`, `show` | Cross-session handoff notes |
| `remedy tool` | `list`, `search`, `stats`, `run` | Tool invocation and stats |
| `remedy session` | `start`, `end` | Session lifecycle tracking |
| `remedy user` | `show`, `facts` | User profile and traits |
| `remedy gateway` | `start`, `status`, `serve`, `channels` | Multi-channel gateway |
| `remedy config` | `init`, `show`, `path` | TOML/YAML configuration |
| `remedy auth` | `login`, `logout`, `status`, `apikey` | Provider OAuth / API keys (xAI) |
| `remedy serve` | | Full API server (config-aware) |
| `remedy desktop` | `install`, `dev`, `build`, `launch`, `status` | Desktop app management |
| `remedy migrate` | `hermes`, `openclaw` | Import from other frameworks |
| `remedy exec` | | Execute commands in sandbox |

---

## Skill Format

Remedy natively supports **[agentskills.io](https://agentskills.io)** — `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
version: 1.0.0
description: What this skill does
kind: tool
tags:
  - utility
requires: []
tools: []
---

# Instructions

Step-by-step guidance for the agent.

```python
# scripts/run.py is discoverable
print("Skill executed")
```
```

Skills can bundle `scripts/` and `references/` directories for code and documentation.

### Export Formats

```bash
remedy skill export my-skill --format hermes   # Hermes-compatible
remedy skill export my-skill --format openclaw # OpenClaw/ClawHub
remedy skill export my-skill --format native   # agentskills.io
remedy skill export my-skill --format zip      # Portable archive
```

---

## Memory, Harness & Handoff

Remedy's memory system provides companion continuity across sessions:

- **Full-text search** (FTS5) with relevance scoring
- **User profile** with persistent traits and facts (injected into the agent)
- **Memory Harness** — send-view prune + Session Brief so long chats stay sharp
- **Knowledge packs** — import a folder of notes (`/import` or `POST /api/memory/import`)
- **Structured handoff notes** with action items, decisions, and Session Brief context
- **Session summaries** and importance scoring for prioritized recall

```bash
remedy memory add "milestone" "Phase 3 learning loop shipped"
remedy memory search "learning loop"
remedy handoff create "Context Transfer" "Working on Phase 4. Next: gateway channels."
```

Partner APIs (when `remedy serve` is running): `GET /api/partner/status`,  
`GET /api/approvals`, `GET /api/goals`, `POST /api/memory/import`.

---

## Learning Loop

Remedy self-improves by distilling task traces into reusable skills:

```
Task completes
  -> ExecutionTrace extracted (steps, tools, errors)
  -> ReflectionEngine analyzes patterns
  -> GeneratedSkill proposed (auto-named, auto-tagged)
  -> SKILL.md saved to skills directory
  -> LearningHistory records event

Skill refines:
  -> SkillRefiner tracks success/failure
  -> Auto-promote: 3+ successes at >=80% -> ACTIVE
  -> Auto-demote: 5+ failures <50% -> DISABLED
  -> Changelog tracked across versions
```

```bash
remedy learn reflect "My Task" --steps_json '[...]'
remedy learn stats --skill my-skill
remedy learn changelog my-skill
```

---

## Configuration

Config files live at `~/.remedy/config.toml` (TOML or YAML):

```toml
name = "Remedy"
home_dir = "~/.remedy"
enabled_channels = ["cli", "web"]
log_level = "INFO"

[gateway]
heartbeat_interval = 60
rate_limit = 120

[execution]
default_timeout = 30
max_retries = 3
retry_backoff = 1.0
```

Environment overrides use the `REMEDY_` prefix:

```bash
REMEDY_LOG_LEVEL=DEBUG remedy serve
REMEDY_EXECUTION__MAX_RETRIES=5 remedy exec python --version
```

---

## API Server

`remedy serve` launches a FastAPI server with:

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System status and health |
| `POST /api/chat` | Send message, get response |
| `POST /api/chat/stream` | SSE streaming chat |
| `GET /api/memory/search` | Full-text memory search |
| `POST /api/memory/add` | Add memory entry |
| `GET /api/skills` | List available skills |
| `POST /api/webhook/{source}` | Receive external webhooks |
| `GET /api/sessions` | List session history |
| `GET /api/handoffs` | List handoff notes |
| `GET /api/openapi.json` | OpenAPI schema |
| `GET /dashboard` | HTML dashboard |

Full session management, streaming SSE events, file search, and command execution — see the [desktop API docs](docs/DESKTOP.md).

---

## Plugin System

Plugins are Python modules that register hooks in the Remedy lifecycle:

```python
# my_plugin.py
def setup_plugin(hooks):
    hooks.register("on_startup", lambda: print("Plugin loaded!"), priority=10)
    hooks.register("pre_tool_exec", log_tool_call)

def teardown_plugin():
    print("Plugin unloaded")
```

---

## Compatibility

| Source | Support |
|--------|---------|
| **agentskills.io** | Native, full compliance |
| **Hermes Agent** | Deep adapter — `hermes_config.yaml` parsing, tool mapping, batch migration |
| **OpenClaw / ClawHub** | Deep adapter — SKILL.md, skill.yaml, claw.yaml, MCP extraction, channel config |
| **MCP (Model Context Protocol)** | Native JSON-RPC stdio client — connect, discover tools, call tools |

---

## Development

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync --group dev
uv run pytest -q     # 375 tests
uv run remedy --help
```

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package management
- (Desktop) Node 20+, Rust toolchain for Tauri builds

### Project structure

```
RemedyAI/
├── src/remedy/
│   ├── core/           # Runtime, ReAct policy/stream helpers, providers, metrics
│   ├── memory/         # SQLite+FTS5 store, handoff, profiles
│   ├── skills/         # Loader, registry, executor, adapters
│   ├── gateway/        # Event router, channels
│   ├── tools/          # MCP client
│   ├── execution/      # Sandbox, hidden process helpers, Docker
│   ├── interfaces/     # CLI, API (models/support/routes/*), plugins
│   ├── bundled_skills/ # Default skills shipped with the package
│   └── migrate/        # Hermes/OpenClaw importers
├── desktop/
│   ├── src/            # React + Vite frontend
│   ├── src-tauri/      # Tauri 2 shell (Rust)
│   └── package.json
├── examples/           # demo_plugin and sample scripts
├── scripts/            # build_desktop, sync_version, signing helpers
├── tests/
├── skills/
└── docs/
```

### Desktop release (maintainers)

Signed Windows installers are built by GitHub Actions on version tags (`v*`):

```bash
# bump version across pyproject / package.json / tauri / Cargo / latest.json
python scripts/sync_version.py patch   # or: 0.10.5 | minor | major

git add -A && git commit -m "chore: release vX.Y.Z"
git push origin desktop-primary
git tag vX.Y.Z && git push origin vX.Y.Z
# → .github/workflows/desktop-release.yml builds sidecar + NSIS, signs, publishes
# Optional: publish Python package — uv build && uv publish
```

See [CHANGELOG.md](CHANGELOG.md) for release notes.

**Signing (required for in-app auto-update):**

| Item | Where |
|------|--------|
| Public key | `desktop/src-tauri/tauri.conf.json` → `plugins.updater.pubkey` (committed) |
| Private key | Local only: `~/.tauri/remedy.key` — **never commit** (see `.gitignore`) |
| CI secrets | `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` |
| Artifacts flag | `bundle.createUpdaterArtifacts: true` in `tauri.conf.json` |

Re-upload secrets after rotating a key:

```bash
uv run python scripts/set_tauri_signing_secrets.py
```

Local signed build (optional):

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content "$env:USERPROFILE\.tauri\remedy.key" -Raw).Trim()
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
python scripts/build_desktop.py --clean
cd desktop; npm run tauri build
```

See [docs/DESKTOP.md](docs/DESKTOP.md) for API contract and full desktop notes.

---

## License

Custom proprietary license — see [LICENSE](./LICENSE). Non-commercial personal use only; commercial use and redistribution require written permission.
