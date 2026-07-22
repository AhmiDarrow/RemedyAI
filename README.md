# Remedy

**The self-improving, multi-channel AI agent framework that grows with you.**

Remedy combines the depth of autonomous learning and persistent memory (Hermes-inspired) with the breadth of an always-on multi-channel gateway (OpenClaw-inspired), while adding native agentskills.io + MCP support and an explicit handoff/notes system for seamless cross-session continuity.

---

## Architecture

```
                   Channels (CLI · Telegram · Discord · ...)
                                   |
                           remedy-gateway
                         (event router + heartbeat)
                                   |
                            remedy-core
                    (ReAct runtime + learning loop)
                         /        |         \
              remedy-skills   remedy-tools   remedy-execution
            (unified engine)   (MCP client)    (sandbox)
                         \        |         /
                          remedy-memory
                   (SQLite+FTS5 · handoff · sessions)
```

**Eight modules, clean separation:**

| Module | Role |
|--------|------|
| `remedy-gateway` | Multi-channel router + heartbeat daemon |
| `remedy-core` | Intelligent runtime, ReAct loop, learning engine |
| `remedy-memory` | Persistent SQLite+FTS5 store with handoff/notes API |
| `remedy-skills` | Unified skill engine (agentskills.io native + Hermes/OpenClaw adapters) |
| `remedy-tools` | MCP client/server integration |
| `remedy-execution` | Sandboxed runners (subprocess, Docker planned) |
| `remedy-interfaces` | CLI (rich) and API surfaces |
| `remedy-migrate` | Import tools from Hermes and OpenClaw setups |

## Quick Start

```bash
# Install
git clone <repo-url> && cd remedy
uv sync

# CLI
uv run remedy --help
uv run remedy skill discover ./skills
uv run remedy memory add "hello" "First memory entry"

# Tests
uv run pytest tests/ -v
```

## Skill Format

Remedy natively supports **[agentskills.io](https://agentskills.io)** — `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: What this skill does
version: 1.0.0
tags:
  - utility
requires: []
tools: []
---

# Instructions

Step-by-step guidance for the agent.
```

Skills can bundle `scripts/` and `references/` directories. Adapters exist for Hermes and OpenClaw/ClawHub formats.

## Memory & Handoff

Remedy's memory system is built for companion continuity:

- **Full-text search** (FTS5) across all memory entries
- **Structured handoff notes** with action items, decisions, and context summaries
- **Session summaries** for tracking progress across sessions
- **Importance scoring** for prioritized recall

```bash
remedy handoff create "Phase 0 Done" \
  "Completed scaffolding. Moving to Phase 1." \
  --tags "milestone,phase-0"
remedy handoff search "scaffolding"
```

## Learning Loop

After complex tasks, Remedy can:
1. **Distill** successful execution traces into reusable `SKILL.md` files
2. **Self-refine** skills based on feedback and repeated use
3. **Store** skills as procedural memory for future sessions

## Compatibility

| Source | Support |
|--------|---------|
| **agentskills.io** | Native, full compliance |
| **Hermes** | Adapter for SKILL.md + scripts |
| **OpenClaw / ClawHub** | Adapter for YAML/MD manifests |
| **MCP** | Native client; tools auto-exposed as skills |

## Development

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run ruff check src/
```

### Requirements

- Python 3.12+
- `uv` for package management

### Project Structure

```
src/remedy/
├── core/           # Runtime, learning loop, orchestration
├── memory/         # SQLite+FTS5 store, handoff, session summaries
├── skills/         # Loader, registry, adapters (Hermes/OpenClaw)
│   └── adapters/
├── gateway/        # Event router, heartbeat, channel adapters
│   └── channels/
├── tools/          # MCP client
├── execution/      # Sandbox (subprocess, Docker planned)
├── interfaces/     # CLI, TUI (rich)
└── migrate/        # Hermes/OpenClaw import tools
```

## Roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| **0** | Scaffolding, core interfaces, models, loader, memory, CLI | Done |
| **1** | Memory & explicit handoff system (enhanced) | Next |
| **2** | Skill compatibility layer (full Hermes + OpenClaw) | Planned |
| **3** | Learning loop & self-improvement | Planned |
| **4** | Gateway & multi-channel support | Planned |
| **5** | Orchestration, safety & execution backends | Planned |
| **6** | Interfaces & MVP integration | Planned |
| **7** | Polish, personality, custom plugins, migration tools | Planned |

## License

MIT
