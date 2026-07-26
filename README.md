# Remedy

**Your personal AI partner — knowledge, design, code, and get-it-done on your machine.**

Remedy is a self-improving multi-channel agent for real work: research, writing,
design, software engineering, and (with permission) tasks across your PC. It is
**not** a medical, clinical, or healthcare product — the name means unsticking
problems and finishing requests, not medicine.

## Continuity (the product idea)

Remedy is **not** a thin wrapper around a chat API and **not** a cast of competing bots.
It is a **local continuity system**: silent workers keep context lean, memory accurate,
and skills improving so **you + this PC + any model you choose** feel like one partner.

```text
You  →  Remedy continuity (brief, memory, skills, budget)  →  Provider model  →  Tools
              ↑________________ learn / compress / remember ________________|
```

- **Fast** — hot path stays cheap; heavy work runs in the background  
- **Cheaper** — less re-sending of tool sludge; compress with fidelity checks  
- **Same Remedy on any provider** — continuity lives on your machine  

Read more: [`docs/manual/16-continuity-philosophy.md`](docs/manual/16-continuity-philosophy.md)  
(In-app: **F1** → *How Remedy works (continuity)*.)

It combines:

- **Depth** — A self-improving learning loop that distills task traces into reusable skills
- **Memory** — Persistent SQLite+FTS5 knowledge store with structured handoff notes and session continuity
- **Continuity layer** — Session Brief, context budget, quality remedies, project-level learning (silent local workers; see `docs/manual/17-nanoswarm.md`)
- **NanoToken BPE** — Remedy-owned byte-level token estimates (`remedy-bbpe-v2`); provider usage remains billing truth; auto-calibrates per model
- **Breadth** — Multi-channel gateway (CLI, REST API, Telegram, Discord, Slack, webhooks)
- **Compatibility** — Native [agentskills.io](https://agentskills.io) support, plus adapters for Hermes and OpenClaw/ClawHub
- **Security** — Local-first by design: full power for you on this PC, not an open doorway for others

---

## Security — local-first by design

Remedy is built so **you** can run shell, files, skills, and a full agent on your machine — while **strangers on your network, random websites, and untrusted skill packs** are not handed the keys by default.

**Design goal:** maximum capability for the owner; no accidental LAN/public doorway; secrets stay off plaintext config when the secure store is available.

| Layer | What we ship |
|-------|----------------|
| **Local API** | Bound to **127.0.0.1** by default; **Bearer** auth on by default; constant-time token compare |
| **No open CORS** | Wildcard CORS is **refused** while API auth is on (blocks browser token theft from other sites) |
| **Hard bind rule** | Auth-off + non-loopback bind is refused unless you set an explicit owner escape hatch (`REMEDY_ALLOW_INSECURE_BIND=1`) |
| **Desktop vs browser** | Desktop prefers OS/IPC token; Settings **Allow browser token bootstrap** (default on) for Web UI; set off or `REMEDY_HTTP_BOOTSTRAP=0` for IPC-only |
| **Secrets** | Provider keys and local API token under `~/.remedy/auth/` — **DPAPI**-protected on Windows when available; `config.toml` is for non-secrets |
| **Access scope** | Project / home / full machine (opt-in); Desktop · Documents · Downloads always usable for common work |
| **Approvals** | **Ask** by default (safe); **Auto** = work-until-done full owner power on trusted scope (never stripped) |
| **Web tools** | `web_fetch` opt-in; public HTTP only with DNS pin / SSRF guards (power kept for the open web) |
| **Skills** | Imported packs land in **quarantine** (Zip Slip + stream size caps); cannot activate or run scripts until you **Trust**; skill script env is scrubbed of provider keys |
| **Tool sandbox** | Subprocess environment scrubbed of secrets; Windows dangerous-command guards; clear security-blocked results |
| **Messaging channels** | e.g. Telegram ignores chats when the allowlist is empty (unless you explicitly allow all) |
| **Updates** | In-app updates use **minisign**-signed `latest.json` (publisher URL match); installers from this repo’s GitHub Releases — see [`docs/WINDOWS_SIGNING.md`](docs/WINDOWS_SIGNING.md) for pubkey |
| **Web UI quit** | Full quit warns that the local server (and browser Web UI) stop; hide-to-tray does not |

Chat content still goes to **the LLM provider you configure** (or stays local with Ollama). There is **no** Remedy cloud account required for core desktop use.

**Owner’s manual (offline in-app):** **F1** → *Security & data*, or read [`docs/manual/04-security-and-data.md`](docs/manual/04-security-and-data.md). Release notes for the hardening pass: [`CHANGELOG.md`](CHANGELOG.md).

---

## Download the Desktop App

The recommended way to use Remedy is the native desktop application — no Python, Node, or Rust toolchain required.

**[Download the latest installer](https://github.com/AhmiDarrow/RemedyAI/releases/latest)** (Windows)

1. Download the `.exe` installer from GitHub Releases
2. Run the installer — Remedy Desktop installs to your local app folder
3. Launch from the Start Menu — the SetupWizard guides you through provider and model configuration
4. Press **F1** for the offline Help wiki (owner's manual), or type `/help` for the command card

> **Windows SmartScreen / Defender?** Solo builds are not Authenticode-signed yet (costly for indie). If Windows says “Unknown publisher”, click **More info → Run anyway**. Defender ML may rarely mislabel **new** unsigned desktop apps — install **only** from this GitHub repo’s Releases, then **Protection history → Allow** if needed. Autostart never uses the registry Run key (Startup folder only). In-app updates are **minisign**-verified.

The desktop app bundles the full Remedy server as a sidecar, so everything runs locally on your machine.

### Desktop Features

| Feature | Description |
|---------|-------------|
| **Chat UI** | Streaming markdown bubbles (you right / Remedy left); shrink-wrap size; stick-to-bottom unless you scroll up (**↓** resumes) |
| **Your name** | Settings + first-run: what Remedy calls you; avatar initials in chat |
| **Tool process** | **Min / Med / Full / Full+** — answer always complete; Full shows all tool args/results (Settings + status bar) |
| **Icons** | Copy / edit / send / attach as icons (language-neutral); image lightbox |
| **Prompt history** | **↑ / ↓** in the composer for previous prompts (shell-style) |
| **Sessions** | Tabs; auto-title from first prompt; rename; search / pin / tags |
| **Attachments** | Drag-and-drop, paste, or attach files/images |
| **Plan/Build mode** | Toggle plan (no tools) vs build |
| **@file / /** | `@` file search; `/` slash-command menu while typing |
| **First-run setup** | Provider, workspace, persona, optional always-ready |
| **Providers** | OpenAI, Anthropic, Google, DeepSeek, xAI, Groq, Mistral, OpenRouter, Ollama; Custom under Advanced. **OAuth** (device code) and/or API keys where supported; keys in the secure store (not plaintext config) |
| **Help wiki** | Offline owner’s manual (**F1** / **Ctrl+/**) — install, security, tools, troubleshooting |
| **Web UI** | **Switch to Web UI** hides desktop to tray and opens `http://127.0.0.1:7400/` (same local API + chat) |
| **Settings** | Logo menu + panel: You & Agent, project, scope, harness, tool process, themes, density, accent |
| **Access scope** | Project only / home / full user machine (opt-in); Desktop/Documents/Downloads always allowed |
| **Always ready** | Startup folder (not registry Run); tray Show / **Settings** / Updates / About / Quit; close-to-tray |
| **Quit safety** | Full quit warns that the local server (and browser Web UI) will stop; “don’t warn again” option |
| **Memory Harness** | Context prune + Session Brief + `/compact` |
| **Approvals** | High-impact shell / write / skill scripts: Approve/Deny (auto mode remains an owner choice) |
| **Coding tools** | `file_edit` (search/replace), `repo_search`, missions + silent `job_run` for Build-class agency |
| **Diff view** | Unified diffs and `file_write` process dumps: red removals / green additions |
| **Goals & plans** | `/goal`, `/goals`, `/plan`, `/plans`, goal/plan tools |
| **ComfyUI skill** | From-scratch install guide + portable discovery + generate images into chat (Flux.2 Klein) |
| **Visual decoder** | Opt-in local image→text (llama.cpp + Qwen2.5-VL 3B) so text-only chat models can use screenshots/OCR; prefer-local to save provider vision tokens |
| **Themes** | System, Dark, **Neutral Dark**, Light, Emerald, Amethyst, Amber, Ocean |
| **Side panels** | Memory · Skills · Settings (status bar) |
| **Skills HITL** | Force-promote / quarantine toggles; CodeMirror `SKILL.md` editor; **Export/Import Pack** (ZIP) |
| **Time Travel** | Status-bar timeline: restore chat + best-effort files to an earlier step |
| **Token & cost ticker** | Hideable live run/session tokens + estimated API cost |
| **Tray** | Circuit-R icon; right-click Settings and more |
| **Auto-update** | Check → download → install → relaunch (signed `latest.json` releases) |
| **Local-first security** | Loopback API + Bearer auth by default; power for the owner, not a LAN doorway |

### Slash commands (desktop & API)

| Command | Purpose |
|---------|---------|
| `/help` | Commands + keyboard shortcuts |
| `/new` | New session |
| `/sessions` | Recent sessions |
| `/models` | Model picker guidance |
| `/thinking` | Toggle thinking visibility |
| `/memory <q>` | Search durable memory |
| `/remember <fact>` | Store a fact in memory/profile |
| `/forget <text>` | Remove a matching Partner Memory fact |
| `/pin <text>` | Pin a fact so it always injects |
| `/whoami` | What Remedy knows about you |
| `/goals` · `/goal <title>` | List / add goals |
| `/plans` · `/plan` · `/plan new <title>` · `/plan approve` | Structured task plans (Plan mode) |
| `/compact` · `/harness` | Memory Harness compress / show Session Brief |
| `/approve` · `/deny` | High-impact command approvals |
| `/import <folder>` | Import `.md`/`.txt` knowledge pack into memory |
| `/export` | Export this chat session as plain-text `.txt` |
| `/import-session` | Import a session from `.txt` / `.md` (file picker or path) |
| `/skills` · `/handoff` | Skills list · handoff notes |
| `/helper` · `/tip` | Offline help tips (or `/helper error <text>`) |
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

# Start the API server (loopback + auth on by default)
remedy serve --host 127.0.0.1 --port 7400 --skip-setup
# Web UI (chat SPA, when desktop/dist is built): http://127.0.0.1:7400/
# API dashboard:  http://127.0.0.1:7400/dashboard
# OpenAPI docs:   http://127.0.0.1:7400/docs

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
| `remedy-tools` | MCP client + MCP host (`remedy mcp serve`), tool registry with invocation stats |
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
| `remedy mcp serve` · `remedy-mcp` | | MCP stdio host (skills/plans for external apps) |
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

## Learning Loop & Skills

**Skills are a core strength of Remedy.** Unlike static skill packs, Remedy
*earns* procedures from real work, protects hard-won multi-attempt solutions, and
loads them with [agentskills.io progressive disclosure](https://agentskills.io)
(catalog → `skill_activate` → optional `skill_run`).

```
Multi-step turn succeeds
  -> Tool steps recorded (auto-learn hook)
  -> Lifecycle gates (no lucky one-off ACTIVE)
  -> Effort score (hard-won protected from prune)
  -> ReflectionEngine: trigger-oriented description + failure protocol
  -> Probation SKILL.md (merge if name exists)
  -> Durable stats (~/.remedy/skill_stats.json)
  -> Multi-session promote / demote / prune

At chat time:
  -> Ranked catalog in system prompt (status, effort badges)
  -> skill_search / skill_activate / skill_run tools
  -> Feedback closes the loop on activation
```

```bash
remedy learn reflect "My Task" --steps_json '[...]'
remedy learn stats --skill my-skill
remedy learn changelog my-skill
```

See [docs/SKILL_LIFECYCLE.md](docs/SKILL_LIFECYCLE.md) for gates, ranking, API,
quarantine import, and the desktop Skills panel.

### Local API auth & security (0.10.33+)

The agent HTTP API enables a **Bearer token by default** (file:
`~/.remedy/auth/local_api_token`). The desktop shell prefers Tauri IPC for the
token; the browser Web UI uses loopback bootstrap. Set `REMEDY_API_AUTH=0` only
for local tests. High-impact tools (`bash_exec`, `file_write`, `skill_run`)
require approval in **ask** mode (status bar); **auto** remains an owner choice.

Defaults aim at **owner power, not a network doorway**: loopback bind, no CORS
`*`, quarantined skills cannot load until Trust. Advanced open-bind needs
`REMEDY_ALLOW_INSECURE_BIND=1`. See [docs/manual/04-security-and-data.md](docs/manual/04-security-and-data.md).

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

`remedy serve` launches a FastAPI server (default **127.0.0.1:7400**, auth on).

| Endpoint | Description |
|----------|-------------|
| `GET /` | Browser Web UI (chat SPA when `desktop/dist` is present) |
| `GET /api/status` | System status and health |
| `GET /api/auth/local-bootstrap` | Loopback-only token bootstrap (desktop/browser) |
| `POST /api/chat` | Send message, get response |
| `POST /api/chat/stream` | SSE streaming chat |
| `GET /api/sessions` | List chat sessions |
| `POST /api/sessions/{id}/messages/stream` | Desktop SSE chat |
| `GET /api/memory/search` | Full-text memory search |
| `POST /api/memory/add` | Add memory entry |
| `GET /api/skills` | List available skills |
| `GET/PUT /api/settings` | Settings (desktop) |
| `POST /api/webhook/{source}` | Receive external webhooks |
| `GET /api/handoffs` | List handoff notes |
| `GET /api/openapi.json` | OpenAPI schema |
| `GET /dashboard` | Lightweight HTML endpoint map |
| `GET /docs` | Swagger UI |

Full session management, streaming SSE, file search, and tools — see [docs/DESKTOP.md](docs/DESKTOP.md) and the offline Help wiki (`docs/manual/`).

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
| **MCP (Model Context Protocol)** | Client (use external tools) + host (`remedy mcp serve` exports skills to Cursor/Claude) |

---

## Development

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync --group dev
uv run pytest -q          # full suite (560+ tests; currently ~812)
cd desktop && npm test    # frontend unit tests (vitest)
python scripts/check_docs.py  # docs stay synced with code (help, cmds, versions)
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
│   ├── tools/          # MCP client, ComfyUI helpers
│   ├── execution/      # Sandbox, env scrub, hidden process, Docker
│   ├── interfaces/     # CLI, FastAPI (models/support/routes/*), plugins, auth
│   ├── bundled_skills/ # Default skills shipped with the package
│   └── migrate/        # Hermes/OpenClaw importers
├── desktop/
│   ├── src/            # React + Vite frontend (Help wiki, chat, settings)
│   ├── src-tauri/      # Tauri 2 shell (Rust) — tray, sidecar, updates
│   └── package.json
├── docs/
│   ├── manual/         # Owner’s manual (also bundled into desktop Help wiki)
│   ├── DESKTOP.md      # Desktop architecture / API contract
│   ├── USAGE.md        # CLI / operator guide
│   └── SKILL_LIFECYCLE.md
├── examples/           # demo_plugin and sample scripts
├── scripts/            # build_desktop, sync_version, sync_help_manual, check_docs, signing
├── tests/              # pytest suite
└── skills/             # Extra / example skill packs
```

### Desktop release (maintainers)

Signed Windows installers are built by GitHub Actions on version tags (`v*`):

```bash
# bump version across pyproject / package.json / tauri / Cargo.lock / latest.json
python scripts/sync_version.py patch   # or: 0.10.37 | minor | major
python scripts/sync_help_manual.py     # keep docs/manual ↔ desktop help articles in sync
python scripts/check_docs.py           # CI gate: versions, help, slash cmds, hotkeys, test count

git add -A && git commit -m "chore: release vX.Y.Z"
git push origin master
git tag vX.Y.Z && git push origin vX.Y.Z
# → .github/workflows/desktop-release.yml builds sidecar + NSIS, signs, publishes
# Optional: publish Python package — uv build && uv publish
```

See [CHANGELOG.md](CHANGELOG.md) for release notes. Current desktop series: **0.14.x**.

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

**Source-available** — see [LICENSE](./LICENSE) (binding) and [COMMERCIAL.md](./COMMERCIAL.md) (summary).

| Who | Terms |
|-----|--------|
| **Solo / small indies** (&lt; $1M revenue **and** &lt; 20 FTE) | Free to use and modify under the LICENSE |
| **Personal / education / research** | Free |
| **Larger orgs, SaaS hosting, commercial resale** | Contact for a written commercial license |

**Copyright (c) 2025–2026 Ahmi Darrow.** Enterprise / commercial: `ahmitdarrow@gmail.com` (subject: `RemedyAI commercial license — [org]`).

The GitHub source is published for transparency and issue tracking. Contributions are welcome under the LICENSE unless otherwise agreed in writing. There is no license-key or phone-home enforcement in the app today.
