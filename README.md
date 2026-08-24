# Remedy

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/previews/hero_banner_win_linux.png" alt="Remedy — your personal AI partner on Windows &amp; Linux" width="800" />
</p>

<p align="center">
  <kbd>Windows 11</kbd>&nbsp;&nbsp;<kbd>Linux</kbd>&nbsp;&nbsp;<kbd>WSLg</kbd>&nbsp;&nbsp;<kbd>PyPI</kbd>&nbsp;&nbsp;<kbd>CLI</kbd>
</p>

<p align="center">
  <em>A partner for any ability level</em> — she drives this computer to finish the goal you set.
</p>

<p align="center">
  <a href="https://github.com/AhmiDarrow/RemedyAI/releases/latest"><strong>Download for Windows</strong></a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md">Install on Linux</a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md">Owner’s manual</a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md">What’s new</a>
  ·
  <code>pip install remedy-ai</code>
</p>

**Not** a medical product; the name means unsticking problems and finishing requests.  
**F1** opens the same Help wiki offline inside the app.

---

## About

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/previews/hero_icon_color_on_dark.png" alt="Remedy icon" width="96" />
</p>

**Remedy is not a coding agent with extras.** She is a partner who lives on **this computer** and works as your hands: see the screen, use local apps, fill forms, shop, research, write code, ship a game, keep going while you step away.

Owners span every ability level — non-technical, low-vision, limited motor control, cognitively loaded, power user. Approvals, progress, and errors are in **plain language**. Money, passwords, submit, send, and delete **stop for you** in every mode. Handoffs (2FA, CAPTCHA, the last payment click) are designed owner moments, not failures.

**Grove** is home. She **speaks and hears** on this machine. **Studio** is one tap away when you want rails, Build, and the full workbench. Continuity lives under `~/.remedy`. You bring the chat model (xAI, OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral, OpenRouter, Ollama, Custom, or **RMB** on this PC). There is no Remedy cloud account for core use.

| | |
|--|--|
| **Who it’s for** | Anyone who wants a partner that *finishes* — errands, research, games, and code |
| **What stays local** | Memory, voice, Vault, skills, approvals, DPAPI secrets, optional SmolVLM2 vision |
| **What you bring** | Your API keys / local models — Remedy does not hold a cloud of you |
| **Current** | **v0.31.0** on [PyPI](https://pypi.org/project/remedy-ai/) · [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases) |

From the creator: *My name is Ahmi, I hope you enjoy my Remedy.*  
In-app: title-bar / tray → **About Remedy** · **Settings → About**.

---

## What’s new

**Latest: [v0.31.0](https://github.com/AhmiDarrow/RemedyAI/releases/tag/v0.31.0)** — a whole new Remedy. Grove is the partner home. She speaks. Life tasks finish with evidence and a Vault for cards. Silent **hive** daughters report packets, not extra chats. **Research** and **game studio** are first-class. Web search is on after install. Windows and Linux share one home.

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/docs/manual/assets/remedy-partner-architecture.jpg" alt="How Remedy fits together" width="640" />
</p>

| Highlight | Why it matters |
|-----------|----------------|
| **Grove + voice** | A calm partner surface. She speaks and hears locally (Chatterbox Nano / Kokoro / whisper). One speak-aloud control. Mid-turn steering so you can talk while she works. |
| **Life tasks + Vault** | “Order my usual” is a job with a plan, observed success, and non-waivable checkpoints. Cards and logins live in the **Remedy Vault** — never typed by guesswork. |
| **Hive** | Silent foragers and standing posts. They never appear as extra chats. They report a capped packet; she still talks to you. |
| **Research studio** | Literature, a citation library that has to resolve, analysis in *your* environment with a run ledger, power and effect sizes, reporting checklists. Fourteen field packs route themselves. |
| **Game studio** | Godot 4, Phaser/Pixi, Bevy, Pygame, Love2D — engine detection, headless verification, playtest, export. Unity/Unreal knowledge. Optional MCP editor bridge. |
| **Web, licence, colour** | Fetch and search on after install (OpenSERP locally; robots.txt respected). Installer carries third-party notices and the product terms. Three colorblind-safe themes. |
| **Local model that tools** | RMB defaults to **Qwen3.5-9B (Q6_K)** on measurements — a 9B at 6-bit holds tool-call structure that larger 4-bit models drop. |

Earlier public line: [0.26.2 Host in Remedy's hands](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md#0262---host-in-remedys-hands) · [0.30.0 Grove, voice, Vault](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md#0300---grove-voice-life-tasks-and-the-vault) (local). Full notes → **[What’s new](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md)** · **[CHANGELOG](https://github.com/AhmiDarrow/RemedyAI/blob/master/CHANGELOG.md)**.

---

## Two desktops, one partner

Same partner on Windows and Linux. Same `~/.remedy` home. Same local API on `127.0.0.1:7400`.

| | **Windows** | **Linux** |
|--|-------------|-----------|
| **Install** | NSIS from [Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) | `.deb` / AppImage from the **same** tag, or [PyPI / source](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md) |
| **Runs on** | Windows 10 / 11 | Native Linux, incl. **WSLg** |
| **Close ✕** | Hides to the **tray** (API stays warm) | Minimizes to the **taskbar** (WSLg has no tray) |
| **Maximize** | OS work area | Work area of **the monitor the window is on** |
| **Autostart** | **Start with Windows** | Not on Linux |
| **CLI** | `remedy` / `Remedy Desktop.exe` | `python -m remedy` ≡ `remedy` |

Same skills, same messengers, same continuity — pick your shell.

---

## Contents

1. [About](#about) · [What’s new](#whats-new) · [Two desktops, one partner](#two-desktops-one-partner)
2. [What you get](#what-you-get)
3. [Life on this computer](#life-on-this-computer)
4. [Grove, voice, Studio](#grove-voice-studio)
5. [Hive, research, games](#hive-research-games)
6. [Workspace on your PC](#workspace-on-your-pc)
7. [Local brain](#local-brain-smolvlm2--rmb)
8. [Why it’s different](#why-its-different)
9. [Messengers](#messengers) · [Skills](#skills--library) · [Memory](#memory--long-work)
10. [Install](#install) · [Security](#security)
11. [Slash commands](#slash-commands)
12. [Architecture](#architecture) · [CLI & API](#cli--api) · [Development](#development)
13. [Support](#support) · [License](#license)

### Owner’s manual (GitHub + F1)

| | |
|--|--|
| **Start here** | [Overview](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md) · [Grove](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/22-grove.md) |
| **What’s new** | [13-whats-new](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md) |
| **Life** | [Personal assistant](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/21-personal-assistant.md) · [Vault](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/23-vault.md) |
| **Studios** | [Game dev](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/26-game-dev.md) · [Research](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/27-research.md) · [Hive](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/28-hive.md) |
| **This PC** | [Coding agency](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/18-agency.md) · [RMB](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/20-rmb-local-agent.md) |
| **All chapters** | [docs/manual/](https://github.com/AhmiDarrow/RemedyAI/tree/master/docs/manual/) · [index](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/README.md) |

Also: [CHANGELOG.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CHANGELOG.md) · [AGENTS.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/AGENTS.md)

---

## What you get

One partner. Two desktops. One local API. Your data under `~/.remedy`.

| | Capability |
|--|------------|
| **Grove** | Default partner home — speakable, one question at a time, Studio one tap away |
| **Voice** | Speaks and hears locally. Identity drifts with the relationship; you can hold it. GPU when you have one. |
| **Life tasks** | Shop, book, fill forms, pay — act → verify → retry → escalate. Success is the goal done, not a green tool call. |
| **Vault** | Cards and credentials; typed only into verified fields. Payment / send / delete cannot be waived. |
| **Hive** | Foragers and standing posts with their own sessions; packets, not transcripts |
| **Research** | Literature, citations that resolve, analysis ledger, stats, field packs |
| **Game studio** | Engine-native verify, playtest, export — Godot first, other engines known |
| **Workbench** | **Files** · **Terminal** · **Browser** · **Scratch** · **Computer use** beside chat |
| **Build** | Long jobs keep going. Frontier models are not taught a syllabus. Enter jumps to latest. |
| **Local brain** | **SmolVLM2** vision + **RMB** llama.cpp chat (Qwen3.5-9B default) |
| **Web** | `web_fetch` / `web_search` on after install; robots.txt; OpenSERP on loopback |
| **Messengers** | Telegram, Discord, Slack, Mattermost, Matrix, WhatsApp, Teams, Google Chat, Signal |
| **Skills** | Progressive disclosure · Installed \| Library · signed community catalog |
| **Always ready** | Windows ✕ → tray; Linux ✕ → taskbar. Local API stays warm until Quit. |
| **Web UI** | Same SPA at `http://127.0.0.1:7400/` |
| **Updates** | Minisign-signed auto-update from GitHub Releases (Windows) |

Your **chat model** is yours. Continuity, voice, Vault, and vision stay **on disk**.

---

## Life on this computer

Anyone can say a goal in their own words. Remedy drives this PC to finish it.

```text
GOAL  →  PLAN  →  DRIVE  →  HANDOFF (sometimes)  →  DONE
"order      "here's what     act · verify ·        login / 2FA /       "done — here's
 my usual    I'll do —        retry · narrate       CAPTCHA / payment    what I did"
 groceries"  okay?"
```

- **One plan-level approval** plus non-waivable checkpoints (money, credentials, submit, send, delete). Prompt fatigue is a safety failure.
- **Secrets only type into verified fields.** The Vault never guesses a card box.
- **Evidence afterwards.** Plain steps + snapshots. A half-done task can resume.
- **Handoffs are designed.** “Kroger wants your password. Type it — I’ll wait.”

Manual: [Personal assistant](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/21-personal-assistant.md) · [Vault](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/23-vault.md)

---

## Grove, voice, Studio

**Grove** is the default surface: partner first, chrome quiet. **Studio** keeps Files, Terminal, Browser, Scratch, Computer use, and Build. The same status bar — provider, model, thinking, approvals, privacy, theme, usage, speak-aloud — sits on both.

She has **one voice**. Chatterbox Nano clones a bundled public-domain reference per gender; Kokoro covers the first minutes while the full voice downloads. Traits (pace, pitch, warmth, articulation) actually reach the speakers. `voice_hold` keeps a voice you like. Desktop fetches a pinned CPython into `~/.remedy/voice/` so the installed app can speak — no `pip install` for the owner.

Manual: [Grove](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/22-grove.md)

---

## Hive, research, games

| Studio | What she does |
|--------|----------------|
| **Hive** | Hire a silent forager for one bounded job, or a standing post that pulses on an interval. They never show in the sidebar. Packets become evidence. Money / send still stop at Remedy. [Hive](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/28-hive.md) |
| **Research** | Notebooks, R, Julia, manuscripts: `lit_search`, a citation library that must resolve, analysis in your env with a run ledger, a priori power, CONSORT / PRISMA / STROBE. Domain packs stay out of ordinary coding turns. [Research](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/27-research.md) |
| **Games** | Detects Godot 4, Phaser/Pixi, Bevy, Pygame, Love2D. Headless `--check-only`, scene refs, playtest with screenshots, export presets. Optional Godot MCP. [Game dev](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/26-game-dev.md) |

Skills she learns are graded by how the turn went; unused ones retire. A saved custom endpoint becomes a provider of its own.

---

## Workspace on your PC

Icon rails open real tools next to the conversation:

| Tool | What it is |
|------|------------|
| **Files** | Project / session file browser — open, copy path, drag into chat |
| **Terminal** | In-app **PowerShell** (ConPTY). Agent shell uses **Host Bridge** — POSIX rewritten to cmd |
| **Browser** | Embedded Chromium research pane; **↗** opens the system browser |
| **Scratch** | Quick notes pad bound to the session |
| **Computer use** | Click / type / screenshot this desktop when you enable it |

Left · chat · right; popout / fullscreen for Terminal, Browser, Scratch.  
Manual: [Chat & sessions](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/05-chat-and-sessions.md) · [Desktop notes](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/DESKTOP.md)

---

## Local brain (SmolVLM2 + RMB)

**On-device efficiency** — not a second chatbot.

| Job | On this PC |
|-----|------------|
| **Visual decoder** | **SmolVLM2 2.2B** — images → structured briefs so *any* chat model can reason about screenshots |
| **RMB chat** | llama.cpp host. Default **Qwen3.5-9B Q6_K** (best local tool-loop score on a 12 GB card). Qwen3.6-35B-A3B stays in the catalog for VRAM-scarce setups. |
| **Harness assist** | Session Brief can refresh without another paid API call |

Vision is **not** in the installer: first-run download → `~/.remedy/vision/` → `llama-server` on **127.0.0.1**. CPU by default; CUDA when NVIDIA is available. RMB autofit sizes context from **VRAM**, not a logo (NVIDIA / AMD / Intel).

Manual: [Local vision](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/14-visual-decoder.md) · [RMB](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/20-rmb-local-agent.md)

---

## Why it’s different

```text
You  →  Continuity (brief · memory · skills · budget · local vision)  →  Your model  →  Tools
              ↑________________ learn / compress / remember ________________|
```

| You feel | What’s actually happening |
|----------|---------------------------|
| **Finished** | Act → verify → retry → escalate. Unobserved success is not claimed. |
| **Speakable** | Grove, voice, Yes-No-Explain. Advanced never strips capability. |
| **Cheap & local** | Hot path stays cheap; voice, vision, and RMB run here. |
| **Same partner** | Switch providers anytime — identity is on disk. |

Deep dive: [Continuity philosophy](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/16-continuity-philosophy.md) · In-app **F1**.

Silent local workers (the **nano swarm** in code) measure, prune, rank, and distill. They **do not** take the microphone. Operator: `/harness` · [Continuity workers](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/17-nanoswarm.md).

---

## Messengers

Talk to Remedy where you already chat. Sessions show up in the desktop list (e.g. `TG · …`) with shared history.

| Platform | Status (catalog) |
|----------|------------------|
| **Telegram** | Ready — long-poll bot (allowlist recommended) |
| **Discord · Slack · Mattermost · Matrix** | Ready adapters |
| **WhatsApp · Teams · Google Chat · Signal** | Partial (webhooks / signal-cli / setup-dependent) |

Configure under **Settings → Messengers**. Empty allowlist = ignore inbound (unless you allow all).

---

## Skills & Library

| Path | Meaning |
|------|---------|
| **Learn from work** | Multi-step success → probation skills → promote over sessions; unused learned skills retire |
| **Installed** | Bundled + learned + trusted library packs on this machine |
| **Library** | Signed community catalog ([remedy-skills](https://github.com/AhmiDarrow/remedy-skills)) — install → quarantine → **Trust** |

Format: [agentskills.io](https://agentskills.io) · Lifecycle: [SKILL_LIFECYCLE.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/SKILL_LIFECYCLE.md) · Manual: [Skills](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/07-skills.md)

---

## Memory & long work

- **Durable memory** — SQLite + FTS5, profile, handoffs, living partner facts
- **This home** — `/stretch` (alias `/home`) maps hardware, PATH tools, rooms, local ports; `/whoami` includes the census
- **Memory Harness** — lean *send-view* for the model; full transcript kept
- **Progress** — mid-task snapshots (calm wording)
- **Plans** — Plan mode outlines; Build executes with approvals
- **Time travel** — restore chat (+ best-effort files) to an earlier step

`/compact` · `/harness` · `/stretch` · `/whoami` · [Memory manual](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/06-memory-and-harness.md)

---

## Install

| | |
|--|--|
| **Windows** | [Download the **latest** Windows installer](https://github.com/AhmiDarrow/RemedyAI/releases/latest) → run Setup |
| **Linux** | [`.deb` / AppImage](https://github.com/AhmiDarrow/RemedyAI/releases/latest) or [PyPI / source](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md) |
| **Anywhere** | `pip install remedy-ai` → `remedy chat` (CLI · WebUI) |

Then **F1** Help · `/help` commands.

> **SmartScreen?** Solo builds are not Authenticode-signed yet — **More info → Run anyway**. Install only from this repo’s Releases. Updates are **minisign**-verified. Autostart = Startup folder (not registry Run).

Local API: **127.0.0.1:7400** (sidecar).

---

## Security

**Maximum capability for you. No accidental LAN doorway.**

| Layer | Default |
|-------|---------|
| API | Loopback + Bearer token |
| CORS | No wildcard while auth is on |
| Secrets | `~/.remedy/auth/` (DPAPI on Windows when available) |
| Vault | Cards / logins; type only into verified fields |
| Scope | Project / home / full machine (opt-in) |
| Write jail | Shell dests stay in write roots (`C:/`, `$HOME`, python/node oneshots) |
| Approvals | **Ask** default. Money / send / close **cannot** be waived by auto/full |
| Web | On after install; robots.txt; OpenSERP on `127.0.0.1`. Turn off in Settings → Security |
| Skills | Quarantine until Trust |
| Messengers | Allowlist-first |

No Remedy cloud account for core use. Chat goes to **your** provider (or local Ollama / RMB).  
[Security & data](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/04-security-and-data.md) · [Web etiquette](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/WEB_ETIQUETTE.md)

---

## Slash commands

| | |
|--|--|
| `/help` · `/new` · `/reset` · `/clear` · `/sessions` · `/models` · `/thinking` | Session & UI |
| `/memory` · `/remember` · `/forget` · `/pin` · `/whoami` · `/stretch` · `/home` | Memory |
| `/goals` · `/goal` · `/plans` · `/plan` … | Plans |
| `/compact` · `/harness` | Harness |
| `/approve` · `/deny` | Approvals |
| `/export` · `/import` · `/import-session` | I/O |
| `/skills` · `/handoff` · `/security-status` · `/init` · `/helper` · `/tip` | Skills & tips |

Full list: [Commands](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/11-reference-commands.md)

---

## Architecture

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/docs/manual/assets/remedy-partner-architecture.jpg" alt="Remedy partner architecture" width="560" />
</p>

```text
┌─ Desktop (Tauri 2) ─────────────────────────────────────────┐
│  Grove · Studio · voice · tray · Files/Terminal/Browser    │
│              │                                              │
│  remedy serve · FastAPI :7400                               │
│    core · hive · vault · host · memory · skills · vision    │
└─────────────────────────────────────────────────────────────┘
     CLI · WebUI · Telegram · Discord · Slack · …
```

---

## CLI & API

```bash
# Package name on PyPI is remedy-ai
pip install remedy-ai

remedy chat
remedy serve --host 127.0.0.1 --port 7400 --skip-setup
# Web UI http://127.0.0.1:7400/  ·  /docs  ·  /dashboard
```

WebUI is the **same SPA** as desktop (`desktop/dist`). After UI changes:  
`cd desktop && npm run build`, then restart serve if needed — see [AGENTS.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/AGENTS.md) (*Desktop SPA vs WebUI*).

---

## Development

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git && cd RemedyAI
uv sync --group dev
uv run pytest -q          # ~8875 tests
cd desktop && npm test && npm run build
python scripts/check_docs.py
cd desktop && npm run tauri:dev   # full shell (set REMEDY_DEV_ROOT to repo)
```

The test suite ships with the tree (write jail, auth, Build, desktop vitest).
Live/soak scripts and the community catalog stay on the maintainer clone.
See [CONTRIBUTING.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CONTRIBUTING.md)
— source-available, not a drive-by PR project.

Release: `python scripts/sync_version.py X.Y.Z` · `python scripts/sync_help_manual.py` · `python scripts/check_docs.py` · tag `vX.Y.Z` · GitHub Actions.  
Signing: [AGENTS.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/AGENTS.md) · [WINDOWS_SIGNING.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/WINDOWS_SIGNING.md)

---

## Support

[patreon.com/cw/AhmiDarrow](https://www.patreon.com/cw/AhmiDarrow) — thank you.

---

## License

**Source-available** — [LICENSE](https://github.com/AhmiDarrow/RemedyAI/blob/master/LICENSE) · [COMMERCIAL.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/COMMERCIAL.md)

| Who | Terms |
|-----|--------|
| Solo / small indies (&lt; $1M revenue **and** &lt; 20 FTE) | Free under LICENSE (this copy) |
| Personal / education / research | Free (this copy) |
| Larger orgs, SaaS, commercial resale, or a paid deal | Written license — **ahmitdarrow@gmail.com** |

Use is at your own risk; the LICENSE is the binding document for warranty,
liability, and owner responsibility (including third-party sites and
accounts). Third-party components have their own licences
(`desktop/public/THIRD_PARTY_NOTICES.txt`).

Copyright © 2025–2026 **Ahmi Darrow**.

---

*My name is Ahmi, I hope you enjoy my Remedy.*
