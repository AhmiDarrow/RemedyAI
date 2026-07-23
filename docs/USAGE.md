# Remedy User Guide

Detailed help for each command, configuration, and usage pattern.

---

## Installation

### From PyPI

```bash
pip install remedy-ai
# NOTE: do NOT `pip install remedy` — that is an unrelated package on PyPI.
```

### From source

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync
# or: pip install -e .
```

### Verify

```bash
remedy --version   # remedy 0.10.4 (or current package version)
remedy --help      # Lists all commands
```

---

## Getting Started

### 1. Initialize configuration

```bash
remedy config init
```

This creates `~/.remedy/config.toml` with sensible defaults. View it:

```bash
remedy config show
remedy config path
```

### 2. Discover skills

Remedy ships with bundled skills in the `skills/` directory:

```bash
remedy skill discover skills/
remedy skill list
remedy skill info memory-backup
```

### 3. Add your first memory

```bash
remedy memory add "start" "Remedy initial setup complete"
remedy memory list --limit 5
```

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

```bash
remedy skill list
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

Tools are the atomic operations Remedy can perform — both built-in and MCP-exposed.

### `remedy tool list`

```bash
remedy tool list
```

Built-in tools include: `memory_search`, `memory_add`, `skill_load`, `skill_list`, `file_read`, `file_write`, `bash_exec`.

### `remedy tool search <query>`

```bash
remedy tool search memory
```

### `remedy tool stats`

Show invocation statistics for all tools — counts, success rates.

```bash
remedy tool stats
```

### `remedy tool run <name>`

Execute a tool through the full runtime pipeline (policy check -> validation -> execute -> provenance recording).

```bash
remedy tool run memory_search --args '{"query": "database"}'
remedy tool run bash_exec --args '{"command": "python --version"}' --timeout 10.0
```

- `--args` — JSON string of arguments
- `--timeout` — seconds (default 30)
- `--retries` — retry count on failure (default 0)

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

Start the REST API server (simpler alternative to `remedy serve` without config integration).

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

Start the full API server with configuration integration. This is the recommended way to run Remedy as a server.

```bash
remedy serve                                    # Defaults: 127.0.0.1:7400
remedy serve --host 0.0.0.0 --port 3000        # Custom host/port
remedy serve --config ./custom.toml             # Custom config file
```

After starting, access:
- **Dashboard**: http://127.0.0.1:7400/dashboard
- **API Docs**: http://127.0.0.1:7400/docs
- **Redoc**: http://127.0.0.1:7400/redoc
- **OpenAPI JSON**: http://127.0.0.1:7400/api/openapi.json
- **OpenAPI YAML**: http://127.0.0.1:7400/api/openapi.yaml

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
```

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

### Check for Updates does nothing

In Settings → About, **Check for Updates** should always show a result:

- **Current / Latest** versions
- **You’re up to date**, or **Update & Relaunch**
- Or a red **error** (network, GitHub, permissions)

The desktop shell checks GitHub Releases (`latest.json`, then the Releases API)
from the Rust side with a 15s timeout. If that fails, it falls back to
`GET /api/updates/check` on the local sidecar.

If you’re on an older build that swallowed errors, download the latest installer
from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).
