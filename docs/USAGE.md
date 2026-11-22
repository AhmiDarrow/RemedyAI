# Remedy User Guide

Detailed help for each command, configuration, and usage pattern.

---

## Installation

### From PyPI

```bash
pip install remedy-ai
# NOTE: do NOT `pip install remedy` — that is an unrelated package on PyPI.
```

`remedy serve` needs Go `remedy-runtime` plus Zig `remedy_core`. On a PyPI
install, the first serve downloads the pair for this OS from the matching
`runtime-v<version>` GitHub release, verifies each file against the sha256
pinned inside the wheel at build time, and caches them in `~/.remedy/runtime/`.
It never falls back to another version: if this version's files are missing or
do not match, serve stops with an error. A source checkout has no pins and
builds its own runtime (`REMEDY_NATIVE_ALLOW_UNPINNED=1` lets it trust this
exact version's `remedy-native-SHA256SUMS.txt` instead).
Set `REMEDY_NATIVE_BOOTSTRAP_DISABLE=1` to skip the download and require a
local binary. The packaged Desktop installer already includes both binaries.

### From source

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync
# or: pip install -e .
```

### Verify

```bash
remedy --version   # prints the installed package version
remedy --help      # Lists all commands
```

### Desktop: tool process visibility

In **Settings → Tool process** (or status bar **Min / Med / Full**):

| Mode | What you see |
|------|----------------|
| **Min** (default) | Answer always complete · process step names only |
| **Med** | Answer always complete · path/command + short results |
| **Full** | Answer + full tool args/results (expanded by default) |

The chat **answer is never truncated** by this setting. After a turn, process stays under the assistant message — **expanded** in Full, collapsed in Med until you open it. The feed auto-scrolls unless you scroll up; **↓** resumes follow.

### Desktop: your name

**Settings → You & Agent → Your name** is what Remedy calls you (synced to profile). Set during setup or when prompted after first run.

First launch: the app gets Remedy’s mind ready, then she talks you through house rules (Traditional setup is always a click away). See [SETUP.md](SETUP.md) and [HOUSE_RULES.md](HOUSE_RULES.md).

---

## Getting Started

### 1. Start the local API

```bash
pip install remedy-ai
# not: pip install remedy  (unrelated package)
remedy serve
```

First run downloads a checksum-verified `remedy-runtime` from GitHub Releases
and caches it in `~/.remedy/runtime/`. Open http://127.0.0.1:7400/ in a
browser, or use the Desktop app from GitHub Releases. The API is loopback only
(`127.0.0.1` / `::1`).

A default `~/.remedy/config.toml` is created on first run. To inspect or
recreate it:

```bash
remedy config show
remedy config path
remedy config init
```

### 2. Connect a provider (xAI example)

**Desktop:** Settings or first-run wizard → choose **xAI (Grok)** → **Sign in with xAI**
(or paste a console API key). Known providers do not need a manual base URL.

**CLI OAuth (device code):**

```bash
remedy auth login xai      # Opens browser / shows user code; polls until approved
remedy auth status xai
remedy auth logout xai
```

**CLI / env API key:**

```bash
remedy auth apikey xai xai-your-console-key

# Any other provider — same command, no OAuth involved
remedy auth apikey anthropic        # prompts, hidden
remedy auth status all              # which providers hold a key (fingerprints, never the key)
remedy auth logout openai           # clear one; `all` clears every stored key
# or:
set XAI_API_KEY=xai-your-console-key   # Windows
export XAI_API_KEY=xai-your-console-key  # Unix
# Clean configs with XAI_API_KEY set preselect the xAI provider automatically.
```

Tokens live in `~/.remedy/auth/xai.json` (mode 600 when the OS allows).
Other key-only brands: set `llm_provider` / `llm_api_key` in config, or use
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`,
`MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, etc.

### Reasoning effort and context

Each model can run with its own reasoning effort, thinking budget, output
ceiling, and context window. Leave a setting unset (the default) and the
provider keeps its built-in behavior.

| Key | Values | What it changes |
|-----|--------|-----------------|
| `reasoning_effort` | `none` `minimal` `low` `medium` `high` `xhigh` (unset = provider default) | OpenAI o-series and GPT-5: `reasoning_effort`. Grok 3 mini: `reasoning_effort` (`low`/`high`). Gemini 2.5+: `reasoning_effort`. Claude 4.6+: adaptive effort. Older Claude thinking models: a thinking budget (low 2048, medium 8192, high 16384, xhigh 32000). Models without the control never receive it. |
| `thinking_budget_tokens` | 1024–128000 | Claude budget thinking (`budget_tokens`) and Gemini `thinking_budget`. Wins over the effort for those models. |
| `max_output_tokens` | 256–256000 | Per-reply ceiling (`max_completion_tokens` on OpenAI reasoning models, `max_tokens` elsewhere). |
| `context_window_tokens` | 4096–2000000 | History window used for trimming and compaction. A local server's real window stays the ceiling. |

`0` or an empty string clears a value. Out-of-range values are refused when you
save, and ignored (with a log line) if hand-edited into the config.

**Desktop:** Settings → Thinking level → **Reasoning and context**. Pick **All
models** or **This model**, then save. Changes apply from the next message.

**CLI:**

```bash
remedy settings set reasoning_effort=high max_output_tokens=16000
remedy settings set --json '{"model_settings": {"anthropic/claude-sonnet-4-5": {"thinking_budget_tokens": 8000}}}'
remedy settings get model_settings
remedy settings set reasoning_effort=default   # back to the provider default
```

**Config (`~/.remedy/config.toml`):**

```toml
reasoning_effort = "medium"
context_window_tokens = 128000

[model_settings."o3"]            # or "<provider>/<model>"
reasoning_effort = "high"
max_output_tokens = 32000
```

A per-model entry wins over the top-level values; any key it leaves out falls
back to them. Saving one model's entry leaves the other models' entries alone.

### 3. Skills and memory

Bundled skills are discovered automatically from the package (and from
`~/.remedy/skills` for your own). After a pip install you do not need
`remedy skill discover skills/` — that path only exists in a source checkout.

```bash
remedy skill list
remedy skill info memory-backup
remedy skill discover /path/to/your/skills   # optional extra roots
remedy memory add "start" "Remedy initial setup complete"
remedy memory list --limit 5
```

### Desktop partner commands

In chat (desktop or `POST /api/sessions/{id}/command`):

| Command | Purpose |
|---------|---------|
| `/remember …` | Save a durable fact |
| `/forget …` | Remove a matching Partner Memory fact |
| `/pin …` | Pin a fact so it always injects |
| `/whoami` | Show profile facts |
| `/goal …` / `/goals` | Goals checklist |
| `/compact` / `/harness` | Memory Harness |
| `/approve` / `/deny` | High-impact shell approvals |
| `/import <folder>` | Import markdown/text notes into memory |
| `/security-status` / `/security` / `/secstatus` | Show partner control settings (approvals mode, web tools, execution timeout, scope) |

Composer: **↑** previous prompt, **↓** next (stored locally).

---

## Memory Commands

The memory system is the persistent knowledge store. All memory entries are indexed with FTS5 for full-text search.

### `remedy memory add <title> <content>`

Add a new memory entry.

```bash
remedy memory add "bug-472" "Null pointer in login handler" --tags "bug,p0"
remedy memory add "decision" "Use SQLite over PostgreSQL for portability" --importance 0.9
```

- `--tags` — comma-separated tags
- `--importance` — float 0.0 to 1.0 (higher = more relevant in searches)

### `remedy memory search <query>`

Full-text search across all memory entries.

```bash
remedy memory search "database"
remedy memory search "login bug" --limit 10
```

### `remedy memory list`

List recent memory entries.

```bash
remedy memory list --limit 25
```

### `remedy memory consolidate`

Run the consolidator to auto-summarize, deduplicate, and boost important entries.

```bash
remedy memory consolidate <session_id> --max-entries 50
```

### `remedy memory repair`

Run integrity checks and vacuum the database.

```bash
remedy memory repair           # Check integrity
remedy memory repair --vacuum  # Vacuum after repair
```

### `remedy memory backup`

Create a timestamped backup of the entire memory database.

```bash
remedy memory backup
# Creates ~/.remedy/backups/memory_backup_20250722_080130.db
```

---

## Handoff Commands

Handoff notes preserve context across sessions — what was done, what's next, decisions made.

### `remedy handoff create <title> <content>`

Create a handoff note. This automatically saves both the handoff note and a memory entry.

```bash
remedy handoff create "Phase 2 Done" "Completed skill compatibility layer. All adapters working."
remedy handoff create "Context Switch" "Handing off API work to focus on gateway." --tags "handoff,context"
```

### `remedy handoff list`

```bash
remedy handoff list --limit 20
```

### `remedy handoff search <query>`

```bash
remedy handoff search "gateway"
```

### `remedy handoff show <id>`

Display a specific handoff note in full detail.

```bash
remedy handoff show 550e8400-e29b-41d4-a716-446655440000
```

---

## Skill Commands

Skills are portable packages of instructions, scripts, and references that teach Remedy how to perform tasks.

### `remedy skill discover <path>`

Recursively scan a directory for `SKILL.md` files and register them.

```bash
remedy skill discover ./skills
remedy skill discover ~/my-custom-skills
```

### `remedy skill list`

List registered skills. **Auto-learned probation** (tool-chain noise) is hidden by
default so coding workflows stay usable.

```bash
remedy skill list              # curated + active skills
remedy skill list --all        # include auto-learned probation
remedy skill list --learned    # only auto-learned skills
```

### `remedy skill info <name>`

```bash
remedy skill info memory-backup
```

### `remedy skill load <path>`

Load a single skill from its directory.

```bash
remedy skill load ./skills/memory-backup
```

### `remedy skill run <name>`

Execute a skill's bundled scripts. Can run all scripts or a specific one.

```bash
remedy skill run memory-backup
remedy skill run my-skill --script backup.py
```

### `remedy skill test <name>`

Run the full validation pipeline on a skill: metadata, dependencies, scripts, and bundled tests.

```bash
remedy skill test my-skill
```

### `remedy skill export <name> <output_dir>`

Export a skill to another format for interoperability.

```bash
remedy skill export my-skill ./exports --format hermes
remedy skill export my-skill ./exports --format openclaw
remedy skill export my-skill ./exports --format native
remedy skill export my-skill ./exports --format zip
```

---

## Learning Commands

The learning loop observes task execution and distills patterns into reusable skills.
Learned skills start on **probation** (never ACTIVE from one lucky run); multi-session
proof promotes them. Hard-won multi-attempt traces get effort weight so they are
protected from casual demote/prune. At chat time the agent sees a ranked catalog and
loads full procedures via `skill_activate` (see [SKILL_LIFECYCLE.md](SKILL_LIFECYCLE.md)).

Desktop **Skills** panel: status, hard-won badge, search, activate/disable/trust,
feedback. HTTP: `GET/POST /api/skills…` (status, feedback, export/import packs).

### `remedy learn reflect <title> --steps_json <json>`

Analyze a completed task trace and generate a new skill if patterns are found.

```bash
remedy learn reflect "Database Migration" --steps_json '[
  {"step": 1, "tool": "read_file", "description": "Read schema", "success": true},
  {"step": 2, "tool": "edit_file", "description": "Add migration", "success": true},
  {"step": 3, "tool": "bash_exec", "description": "Run migrate", "success": true}
]'
```

To reflect on a failed task and capture error patterns:

```bash
remedy learn reflect "Failed Deploy" --steps_json '[
  {"step": 1, "tool": "read_file", "success": true},
  {"step": 2, "tool": "bash_exec", "success": false, "error": "permission denied"},
  {"step": 3, "tool": "bash_exec", "success": false, "error": "connection refused"}
]'
```

### `remedy learn history`

Show all recorded learning events (skill creations, refinements, status changes).

```bash
remedy learn history --limit 20
```

### `remedy learn changelog <skill_name>`

View the refinement history for a specific skill — version bumps, instruction changes, confidence adjustments.

```bash
remedy learn changelog memory-backup
```

### `remedy learn stats`

Show execution statistics for all skills, or drill into one.

```bash
remedy learn stats
remedy learn stats --skill memory-backup
```

Output includes: success rate, total executions, avg duration, reliability assessment.

### `remedy learn sync`

Persist all learning events to the memory store as `SKILL_LEARNED` memory entries.

```bash
remedy learn sync
```

---

## Tool Commands

Tools are the Go Tool ABI surface served by `remedy-runtime` (requires
`remedy serve` or Desktop). Python BasicRuntime is not used.

### `remedy tool list`

```bash
remedy tool list
```

Lists Tool ABI ids such as `runtime.probe`, `workspace.read`, `workspace.list`,
`shell.exec`. For memory / shell without the runtime, use `remedy memory` and
`remedy exec`.

### `remedy tool search <query>`

```bash
remedy tool search workspace
```

### `remedy tool stats`

Show Tool ABI registration counts by runtime (Go / Zig / Python worker).

```bash
remedy tool stats
```

### `remedy tool run <name>`

Invoke a Tool ABI tool through Go `POST /api/tools/invoke` (workspace-jailed /
capability-gated on the runtime).

```bash
remedy tool run runtime.probe --args '{}'
remedy tool run workspace.read --args '{"path": "README.md"}' --timeout 10.0
```

- `--args` — JSON object of Tool ABI input
- `--timeout` — seconds (default 30)
- `--retries` — accepted for compatibility; retries are not applied by the HTTP path

---

## Execution Commands

### `remedy exec <command...>`

Execute a command in the async subprocess sandbox.

```bash
remedy exec python --version
remedy exec pytest tests/ -q
remedy exec echo hello world
```

- `--timeout` — seconds (default 30)
- `--workdir` — working directory
- `--shell` — use shell interpreter

---

## Session Commands

### `remedy session start`

Start a new session. This loads pending handoffs, loads the user profile, and initializes session tracking.

```bash
remedy session start
```

### `remedy session end`

End the current session. This auto-generates a handoff note, saves a session summary, updates user profile stats, and clears session state.

```bash
remedy session end
```

---

## User Profile Commands

### `remedy user show`

Display your user profile — sessions recorded, skills used, traits, and facts.

```bash
remedy user show
```

### `remedy user facts`

Search through your stored facts.

```bash
remedy user facts python
```

---

## Gateway Commands

### `remedy gateway start`

Start the gateway daemon with optional channel tokens.

```bash
remedy gateway start --heartbeat 30
remedy gateway start --telegram-token "12345:abcde" --discord-token "fghij"
```

### `remedy gateway status`

Show gateway statistics (events processed, uptime, connected channels).

```bash
remedy gateway status
```

### `remedy gateway serve`

Same API server as `remedy serve` (instance lock, config, messengers, bind policy).
Prefer `remedy serve` when you need flags (`--host`, `--port`). `--skip-setup` and `--config` are accepted for older scripts but are no-ops.

```bash
remedy gateway serve
```

### `remedy gateway channels`

List all available channel types.

```bash
remedy gateway channels
# Outputs: cli, telegram, discord, slack, web, api
```

---

## Server Commands

### `remedy serve`

Starts Go **`remedy-runtime`** on the local API (default `127.0.0.1:7400`).
Python no longer binds uvicorn on `:7400`. Loopback only.

```bash
remedy serve                                    # Defaults: 127.0.0.1:7400
remedy serve --port 7410                        # Custom loopback port
# Config is read from $REMEDY_HOME or ~/.remedy; --config is a no-op.
```

Looks for `remedy-runtime` on `REMEDY_NATIVE_RUNTIME_BIN` / `REMEDY_RUNTIME`,
PATH, `desktop/bin`, then a verified cache under `~/.remedy/runtime/`. If
none is present, first serve downloads the prebuilt for this OS from GitHub
Releases and checksum-verifies it. If no asset exists, it prints build
instructions (`go build` / `zig build` into `desktop/bin`).

The browser UI ships inside the PyPI wheel (0.69.2+, `remedy/webui/dist`), so a
pip install serves the full app at `/`. `REMEDY_WEBUI_DIR` or a live
`desktop/dist` checkout takes precedence over the bundled copy. The served UI
carries a Content-Security-Policy (inline scripts allowed by hash only, no
framing) and the same token handout as Desktop's browser mode: the same OS user
is let in directly, and `remedy serve` / `remedy open` print a one-time link
where the OS cannot tell who is asking. After starting, access:
- **Web UI / Dashboard**: http://127.0.0.1:7400/
- **API**: http://127.0.0.1:7400/api/ping  (and the rest of the local API)

---

## Migration Commands

Import skills from other frameworks.

### `remedy migrate hermes <path>`

Import skills from a Hermes Agent installation.

```bash
remedy migrate hermes ~/.hermes/skills
remedy migrate hermes ~/.hermes/skills --no-copy   # Register only, don't copy files
```

### `remedy migrate openclaw <path>`

Import skills from an OpenClaw/ClawDocker installation.

```bash
remedy migrate openclaw ~/openclaw/skills
remedy migrate openclaw ~/openclaw/skills --no-copy
```

---

## Configuration Reference

### Config file (`~/.remedy/config.toml`)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `name` | string | `"Remedy"` | Agent name |
| `persona` | string | `"default"` | Personality preset |
| `home_dir` | string | `"~/.remedy"` | Data directory |
| `skills_dir` | list | `[]` | Additional skill search paths |
| `memory_db_path` | string | (computed) | Custom SQLite path |
| `enabled_channels` | list | `["cli"]` | Active channel backends |
| `mcp_servers` | list | `[]` | MCP server configs |
| `allow_skill_creation` | bool | `true` | Permit learning-generated skills |
| `auto_approve_threshold` | float | `0.8` | Confidence threshold for auto-approval |
| `log_level` | string | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `llm_provider` | string | `"openai"` | `openai`, `anthropic`, `google`, `deepseek`, `xai`, `groq`, `mistral`, `openrouter`, `ollama`, `custom` |
| `llm_model` | string | provider default | Model id for the provider |
| `llm_base_url` | string | provider default | Usually auto-filled; required mainly for `custom` |
| `llm_api_key` | string | `""` | Cloud API key (xAI may use OAuth instead) |
| `project_path` | string | `""` | Default workspace for tools / `@file`. Empty / `.` = no project → **full** access (warns in Settings; pick a folder for focused coding) |
| `setup_completed` | bool | `false` | First-run wizard gate |

### Gateway section `[gateway]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `heartbeat_interval` | float | `60` | Seconds between heartbeats |
| `rate_limit` | int | `120` | Max events/minute per channel |

### Execution section `[execution]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_timeout` | int | `30` | Default sandbox timeout (seconds) |
| `max_retries` | int | `3` | Max tool execution retries |
| `retry_backoff` | float | `1.0` | Exponential backoff multiplier |

### Channel sections `[telegram]`, `[discord]`, `[slack]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `bot_token` | string | `""` | Bot API token |
| `channel_id` | string | `""` | Target channel ID |

### Environment Variables

Any config key can be overridden with `REMEDY_<key>`:

```bash
REMEDY_LOG_LEVEL=DEBUG          # Simple key
REMEDY_EXECUTION__MAX_RETRIES=5 # Nested key (double underscore)
REMEDY_TELEGRAM__BOT_TOKEN=abc  # Channel config
REMEDY_LLM_PROVIDER=xai
REMEDY_LLM_MODEL=grok-3-mini
XAI_API_KEY=xai-…               # Also accepted (preselects xAI)
REMEDY_XAI_OAUTH_CLIENT_ID=…    # Optional override for device OAuth client
```

---

## Auth commands

| Command | Description |
|---------|-------------|
| `remedy auth login xai` | Device-code OAuth (Sign in with xAI) |
| `remedy auth status xai` | Show connected method / expiry |
| `remedy auth apikey xai [key]` | Store console API key |
| `remedy auth logout xai` | Clear `~/.remedy/auth/xai.json` |

---

## API Reference

Full API documentation is available at `/docs` when the server is running.

### Example: Chat

```bash
curl -X POST http://127.0.0.1:7400/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What skills are available?"}'
```

Response:

```json
{
  "response": "I found 3 skills: memory-backup, data-export, code-review",
  "request_id": "550e8400-...",
  "session_id": null,
  "processing_time_ms": 12.5
}
```

### Example: SSE Streaming

```bash
curl -X POST http://127.0.0.1:7400/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Search memory for database"}'
```

### Example: Memory Search

```bash
curl "http://127.0.0.1:7400/api/memory/search?query=database&limit=10"
```

### Example: Webhook

```bash
curl -X POST http://127.0.0.1:7400/api/webhook/github \
  -H "Content-Type: application/json" \
  -d '{"action": "push", "repository": "Remedy"}'
```

---

## Writing a Skill

Skills are directories containing a `SKILL.md` manifest. The full structure:

```
my-skill/
├── SKILL.md          # Required: YAML frontmatter + markdown body
├── scripts/          # Optional: executable scripts
│   └── run.py
└── references/       # Optional: reference documents
    └── api-spec.md
```

### SKILL.md Frontmatter Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Unique skill identifier (lowercase, hyphens) |
| `version` | Yes | string | SemVer version (`1.0.0`) |
| `description` | Yes | string | What the skill does |
| `kind` | No | enum | `tool`, `workflow`, `reference` (default: `tool`) |
| `tags` | No | list | Categorization tags |
| `requires` | No | list | Dependency skill names |
| `tools` | No | list | Tools this skill uses |

---

## Writing a Plugin

Create a Python file and call `setup_plugin(hooks)`:

```python
# my_monitor.py
import time

_start = None

def setup_plugin(hooks):
    hooks.register("on_startup", _on_start, priority=100)
    hooks.register("on_shutdown", _on_stop, priority=0)
    hooks.register("post_tool_exec", _record_latency, priority=5)

def _on_start():
    global _start
    _start = time.time()

def _on_stop():
    elapsed = time.time() - _start
    print(f"Session lasted {elapsed:.0f}s")

def _record_latency(tool_name, result, context):
    print(f"Tool {tool_name} took {result.duration_ms:.0f}ms")

def teardown_plugin():
    pass
```

Available hooks: `on_startup`, `on_shutdown`, `pre_tool_exec`, `post_tool_exec`, `on_event`, `on_memory_save`, `on_skill_loaded`.

---


## Talking with other bots

Remedy can talk with your other bots (Claude, ChatGPT, Cursor, Grok Bot, and
anyone who speaks A2A or MCP) as a peer, not as a model she runs. Inbound is
off until you enable it in Settings → Advanced (`interop_inbound`). When it is
on, it stays on localhost and needs a token. `remedy mcp serve` is the
opt-in stdio server other apps can add as "talk to Remedy".

Details, knobs, and the Agent Card path: [docs/AGENT_INTEROP.md](AGENT_INTEROP.md).

## Troubleshooting

### `[LLM ERROR — HTTP 400] … tool_calls must be followed by tool messages`

OpenAI-compatible providers require every assistant `tool_calls` entry to have a matching
`role: tool` message with the same `tool_call_id` before the next model request.

Remedy’s ReAct loop (v0.10.4+) always pairs results for every call id, even when:

- many tools run in parallel (over the concurrency cap),
- identical calls are fingerprint-deduped,
- a tool raises an exception mid-batch.

If you still see this on an older install, upgrade (`pip install -U remedy-ai` or install
the latest desktop release), restart the app/server, and prefer a new chat session for
large multi-tool turns such as “review project”.

### Long answers cut off mid-stream

The stream loop auto-continues when the provider returns `finish_reason=length` /
`max_tokens` (up to a few continuations). If answers still stop early, check provider
token limits and model `max_tokens` settings in `~/.remedy/config.toml`.

### Check for Updates / one-click install

In Settings → About, **Check for Updates** should always show a result:

- **Current / Latest** versions
- **You’re up to date**, or **Update & Relaunch**
- Or a red **error** (network, GitHub, permissions)

**Update & Relaunch** (one click):

1. Downloads the NSIS installer (progress UI)
2. Installs silently (`/S`)
3. App exits; installer finishes; **Remedy reopens** on the new version

Windows may show a single UAC/elevation prompt — approve it, then the rest is automatic.

The desktop shell checks GitHub Releases (`latest.json`, then the Releases API)
from the Rust side with a 15s timeout. If that fails, it falls back to
`GET /api/updates/check` on the local sidecar.

If you’re on an older build that swallowed errors, download the latest installer
from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).
