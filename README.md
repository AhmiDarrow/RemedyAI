# Remedy

**The self-improving, multi-channel AI agent framework that grows with you.**

Remedy is a standalone AI agent framework designed for autonomous, long-running projects. It combines:

- **Depth** — A self-improving learning loop that distills task traces into reusable skills
- **Memory** — Persistent SQLite+FTS5 knowledge store with structured handoff notes and session continuity
- **Breadth** — Multi-channel gateway (CLI, REST API, Telegram, Discord, Slack, webhooks)
- **Compatibility** — Native [agentskills.io](https://agentskills.io) support, plus adapters for Hermes and OpenClaw/ClawHub

```bash
pip install remedy
# or: git clone https://github.com/AhmiDarrow/Remedy && uv sync
```

---

## Quick Start

```bash
remedy --help                    # See all 16 commands
remedy config init               # Create ~/.remedy/config.toml
remedy skill discover ./skills   # Load bundled & custom skills
remedy memory add "test" "Hello, Remedy!"

# Launch interactive chat with the agent
remedy chat
# Type /help for commands, /exit to quit

# Start the API server
remedy serve --host 127.0.0.1 --port 8000
# Dashboard at http://127.0.0.1:8000/dashboard
# OpenAPI docs at http://127.0.0.1:8000/docs
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
| `remedy serve` | | Full API server (config-aware) |
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

## Memory & Handoff

Remedy's memory system provides companion continuity across sessions:

- **Full-text search** (FTS5) with relevance scoring
- **Structured handoff notes** with action items, decisions, and context summaries
- **Session summaries** for tracking progress
- **Importance scoring** for prioritized recall
- **User profile** with persistent traits and facts

```bash
remedy memory add "milestone" "Phase 3 learning loop shipped"
remedy memory search "learning loop"
remedy handoff create "Context Transfer" "Working on Phase 4. Next: gateway channels."
```

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
git clone https://github.com/AhmiDarrow/Remedy.git
cd Remedy
uv sync --group dev
uv run pytest -q     # 307 tests
uv run remedy --help
```

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package management

### Project structure

```
src/remedy/
├── core/
│   ├── runtime.py          # AgentRuntime
│   ├── learning_loop.py    # Learning orchestration
│   └── learning/           # Reflection, refiner, procedural memory
│   ├── errors.py           # Error hierarchy + retry policies
│   ├── logging.py          # Structured logging with context
│   ├── metrics.py          # Counters, gauges, histograms, health checks
│   └── security.py         # Input validation, path traversal guards
├── memory/
│   ├── store.py            # SQLite + FTS5 backend
│   ├── profile.py          # User profile, traits, facts
│   ├── consolidator.py     # Auto-summarization & dedup
│   ├── handoff.py          # Session-boundary handoff generation
│   └── repair.py           # Integrity, vacuum, backup
├── skills/
│   ├── loader.py           # SKILL.md parser
│   ├── registry.py         # Skill registry
│   ├── executor.py         # Script runner
│   ├── validator.py        # Metadata, deps, tests
│   ├── exporter.py         # Multi-format export
│   ├── tool_registry.py    # Builtin + MCP tool registry
│   └── adapters/           # Hermes, OpenClaw, MCP adapters
├── gateway/
│   ├── router.py           # Event router + heartbeat
│   ├── cli.py              # Gateway daemon CLI
│   └── channels/           # Channel adapters (CLI, Telegram, Discord, Slack, Web)
├── tools/
│   └── mcp_client.py       # MCP JSON-RPC stdio client
├── execution/
│   ├── sandbox.py          # Async subprocess sandbox
│   ├── docker.py           # Docker sandbox with resource limits
│   ├── policy.py           # Execution policy engine (allow/deny/approval)
│   └── runtime.py          # Tool runtime with provenance
├── interfaces/
│   ├── cli.py              # All CLI dispatch
│   ├── api.py              # FastAPI server
│   ├── config.py           # TOML/YAML config + env overrides
│   └── plugin.py           # Hook + plugin system
└── migrate/
    ├── from_hermes.py      # Hermes -> Remedy migration
    └── from_openclaw.py    # OpenClaw -> Remedy migration
```

---

## License

MIT — see [LICENSE](./LICENSE).
