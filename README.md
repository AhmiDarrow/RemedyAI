# Remedy

**Your personal AI partner — knowledge, design, code, and get-it-done on your machine.**

Self-improving multi-channel agent for real work on **your PC**. Not a medical product — the name means unsticking problems and finishing requests.

**[Download Windows installer](https://github.com/AhmiDarrow/RemedyAI/releases/latest)** · **F1** = offline owner’s manual · Current series **0.15.x**

---

## Contents

| | |
|--|--|
| [What makes Remedy different](#what-makes-remedy-different) | Continuity, not a thin chat wrapper |
| [Local brain (Qwen on this PC)](#local-brain-qwen-on-this-pc) | Vision decoder, harness assist, free efficiency |
| [Download & install](#download--install) | Desktop app (recommended) |
| [Desktop features](#desktop-features) | Chat, workspace, skills, messengers, security |
| [Slash commands](#slash-commands) | Everyday `/` commands |
| [Architecture](#architecture) | Desktop shell + local API |
| [Security (local-first)](#security-local-first) | Owner power, not a network doorway |
| [Skills & library](#skills--library) | Learn from work + signed community catalog |
| [Memory & harness](#memory--harness) | Durable facts + lean long chats |
| [CLI & API](#cli--api) | Power users and automation |
| [Development](#development) | Tests, docs gate, desktop release |
| [Support](#support) | Patreon |
| [License](#license) | Source-available terms |

Full manuals: [`docs/manual/`](docs/manual/) · Changelog: [`CHANGELOG.md`](CHANGELOG.md) · Agents: [`AGENTS.md`](AGENTS.md)

---

## What makes Remedy different

Remedy is **not** a thin wrapper around a chat API and **not** a cast of competing bots.

It is a **local continuity system**: silent workers keep context lean, memory accurate, and skills improving so **you + this PC + any model you choose** feel like one partner.

```text
You  →  Continuity (brief, memory, skills, budget)  →  Your model  →  Tools
              ↑__________ learn / compress / remember __________|
```

| Outcome | How |
|---------|-----|
| **Fast** | Hot path stays cheap; heavy work is background |
| **Cheaper** | Less tool sludge re-sent; local Qwen where it saves paid calls |
| **Same partner** | Continuity lives on disk — switch Grok / Claude / GPT / Ollama freely |

Deep dive: [Continuity philosophy](docs/manual/16-continuity-philosophy.md) · In-app: **F1** → *How Remedy works*.

---

## Local brain (Qwen on this PC)

Remedy ships pride in its **on-device** stack — not as a second chat persona, but as a **shared efficiency engine**.

| Role | What local **Qwen2.5-VL 3B** does |
|------|-----------------------------------|
| **Visual decoder** | Screenshots & photos → structured **text briefs** so text-only chat models can still reason about images |
| **Prefer-local vision** | Optional: decode here first and **save provider vision tokens** even when the cloud model can see |
| **Memory Harness** | Background **Session Brief** refresh without another paid API round-trip |
| **Continuity assist** | Optional nano refine when llama-server is already up (never blocks the hot path) |

**How it ships**

- **Not** in the installer (keeps the `.exe` small)  
- Downloaded once on first run (Setup or **Settings → Vision & nano swarm**) into `~/.remedy/vision/`  
- Runtime: **llama.cpp** `llama-server` on **127.0.0.1** only · CPU default · CUDA when NVIDIA is present  
- **Auto-starts with Remedy** when installed and enabled  

Manual: [Local vision & nano swarm](docs/manual/14-visual-decoder.md) · Operators: [Continuity workers](docs/manual/17-nanoswarm.md)

Your **chat** model remains whatever you configure (xAI, OpenAI, Ollama, …). Local Qwen **assists** — it does not replace your partner voice.

---

## Download & install

Recommended: **native desktop** — no Python/Node/Rust required.

1. Get the installer from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest)  
2. Run it → Start Menu → **Setup wizard** (provider + optional workspace)  
3. **F1** = Help wiki · `/help` = command card  

> **SmartScreen / Defender?** Solo builds are not Authenticode-signed yet. Prefer **More info → Run anyway**. Install **only** from this repo’s Releases. Autostart uses the **Startup folder** (not registry Run). In-app updates are **minisign**-verified.

The desktop app runs a local sidecar API on **127.0.0.1:7400**.

---

## Desktop features

| Area | Highlights |
|------|------------|
| **Chat** | Streaming markdown; stick-to-bottom; image attach + lightbox; empty-state monogram |
| **Workspace** | Left · chat · right rails; sessions, files, terminal, embedded browser, scratch |
| **Plan / Build** | Plan explores safely; Build can edit/run tools (approvals) |
| **Sessions** | Project folders, pin/search, messenger origins, realtime list via SSE |
| **Messengers** | Settings connectors (Telegram live path; Discord, Slack, Mattermost, Matrix, WhatsApp/Teams/GChat/Signal modular) |
| **Memory** | Durable store + Progress snapshots + plans (calm wording, not scare-logs) |
| **Skills** | **Installed \| Library** · Trust / Promote / Quarantine · signed community catalog |
| **Local Qwen** | Visual decoder + harness assist ([above](#local-brain-qwen-on-this-pc)) |
| **ComfyUI** | Bundled skill for local image generation into chat |
| **Providers** | OpenAI, Anthropic, Google, DeepSeek, xAI (OAuth/key), Groq, Mistral, OpenRouter, Ollama, Custom |
| **Web UI** | Same SPA at `http://127.0.0.1:7400/` (Switch to WebUI → tray) |
| **Time travel** | Restore chat (+ best-effort files) to an earlier step |
| **Usage** | Live tokens / estimated cost ticker |
| **Updates** | Signed `latest.json` → install → relaunch |

More detail: [docs/DESKTOP.md](docs/DESKTOP.md) · [Owner’s manual](docs/manual/00-overview.md)

---

## Slash commands

| Command | Purpose |
|---------|---------|
| `/help` | Commands + shortcuts |
| `/new` · `/sessions` · `/models` · `/thinking` | Session, model, thinking toggle |
| `/memory` · `/remember` · `/forget` · `/pin` · `/whoami` | Memory & profile |
| `/goals` · `/goal` · `/plans` · `/plan` … | Goals & plans |
| `/compact` · `/harness` | Compress / show Session Brief |
| `/approve` · `/deny` | High-impact tool approvals |
| `/export` · `/import` · `/import-session` | Chat & knowledge I/O |
| `/skills` · `/handoff` · `/init` | Skills, handoffs, project scan |
| `/helper` · `/tip` | Offline tips (or `/helper error <text>`) |

Full list: [Commands](docs/manual/11-reference-commands.md)

---

## Architecture

```text
┌─ Remedy Desktop (Tauri 2) ─────────────────────┐
│  React SPA  ·  tray  ·  updates  ·  WebView2   │
│           │ spawn / IPC                          │
│  remedy serve (Python) · FastAPI :7400           │
│    gateway · core · memory · skills · vision     │
└──────────────────────────────────────────────────┘
        CLI · WebUI · Telegram · other messengers
```

| Package area | Role |
|--------------|------|
| `gateway` | Channels, rate limits, messenger adapters |
| `core` | Agent runtime, ReAct, learning, providers |
| `memory` | SQLite+FTS5, harness, sessions |
| `skills` | agentskills.io + Library install |
| `vision` / local Qwen | Decoder + shared llama-server |
| `interfaces` | CLI, API, desktop contract |

---

## Security (local-first)

**Owner power on this PC — not an open LAN doorway.**

| Layer | Default |
|-------|---------|
| API bind | **127.0.0.1** + **Bearer** auth |
| CORS | No `*` while auth is on |
| Secrets | `~/.remedy/auth/` (DPAPI on Windows when available) |
| Scope | Project / home / full (opt-in) |
| Approvals | **Ask** default; **Auto** is an owner choice |
| Skills | Quarantine until **Trust** |
| Messengers | Empty allowlist = ignore (unless allow-all) |

Chat still goes to **your** configured provider (or local Ollama). No Remedy cloud account for core use.

Details: [Security & data](docs/manual/04-security-and-data.md)

---

## Skills & library

- **Learn from real work** — multi-step success → probation skills → promote over sessions  
- **agentskills.io** native format + Hermes / OpenClaw adapters  
- **Library:** signed catalog at [remedy-skills](https://github.com/AhmiDarrow/remedy-skills) · Desktop **Skills → Library** · Ed25519 + SHA-256  

See [SKILL_LIFECYCLE.md](docs/SKILL_LIFECYCLE.md) · [Skills manual](docs/manual/07-skills.md)

---

## Memory & harness

- SQLite + **FTS5** search · user profile · handoffs  
- **Memory Harness** — lean *send-view* for the model; full transcript kept  
- **Session Brief** — intent, decisions, files, next steps (local Qwen can refresh in background)  

`/compact` · `/harness` · [Memory manual](docs/manual/06-memory-and-harness.md)

---

## CLI & API

```bash
# PyPI package name is remedy-ai ("remedy" on PyPI is a different package)
pip install remedy-ai
# or: git clone … && uv sync && pip install -e .

remedy chat
remedy serve --host 127.0.0.1 --port 7400 --skip-setup
# Web UI: http://127.0.0.1:7400/   Docs: /docs   Dashboard: /dashboard
```

| Surface | Notes |
|---------|--------|
| `remedy chat` | Interactive agent REPL |
| `remedy serve` | Full API + WebUI (needs `desktop/dist` for SPA) |
| `remedy gateway` | Multi-channel (Telegram, etc.) |
| `remedy auth` | xAI OAuth / keys |
| `remedy desktop` | Dev launch / status |
| `remedy mcp serve` | MCP host for external clients |

WebUI load paths & rebuild notes: **[AGENTS.md](AGENTS.md)** (*Desktop SPA vs WebUI*).

---

## Development

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync --group dev
uv run pytest -q          # 560+ tests; currently ~870
cd desktop && npm test && npm run build
python scripts/check_docs.py
```

| Need | Command |
|------|---------|
| Desktop dev | `cd desktop && npm run tauri:dev` (set `REMEDY_DEV_ROOT` to repo) |
| Version bump | `python scripts/sync_version.py 0.15.x` |
| Help wiki sync | `python scripts/sync_help_manual.py` |
| Release tag | `vX.Y.Z` → GitHub Actions desktop-release |

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/); desktop: Node 20+, Rust for Tauri.

Signing / update asset names: [AGENTS.md](AGENTS.md) · [WINDOWS_SIGNING.md](docs/WINDOWS_SIGNING.md)

---

## Support

If Remedy helps you: **[patreon.com/cw/AhmiDarrow](https://www.patreon.com/cw/AhmiDarrow)** — appreciated.

---

## License

**Source-available** — [LICENSE](./LICENSE) (binding) · [COMMERCIAL.md](./COMMERCIAL.md) (summary).

| Who | Terms |
|-----|--------|
| Solo / small indies (&lt; $1M revenue **and** &lt; 20 FTE) | Free under LICENSE |
| Personal / education / research | Free |
| Larger orgs, SaaS hosting, commercial resale | Written license — **ahmitdarrow@gmail.com** |

Copyright © 2025–2026 **Ahmi Darrow**. No license-key phone-home in the app today.

---

*My name is Ahmi, I hope you enjoy my Remedy.*
