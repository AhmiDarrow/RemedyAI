# Remedy

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/previews/hero_banner_win_linux.png" alt="Remedy — your personal AI partner on Windows &amp; Linux" width="800" />
</p>

<p align="center">
  <kbd>Windows 11</kbd>&nbsp;&nbsp;<kbd>Linux</kbd>&nbsp;&nbsp;<kbd>WSLg</kbd>&nbsp;&nbsp;<kbd>PyPI</kbd>&nbsp;&nbsp;<kbd>CLI</kbd>
</p>

<p align="center">
  <em>One continuous partner</em> — not a thin chat wrapper, not a farm of bots.
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

**Remedy** is a **true AI partner** — more than a coder/builder — that helps you **track and complete your goals** through research, design, code, and most importantly **action**. It’s a **Windows + Linux** desktop partner (Tauri + local FastAPI) that keeps **continuity on disk** under `~/.remedy` while **you** pick the chat model (xAI, OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral, OpenRouter, Ollama, Custom).

| | |
|--|--|
| **Who it’s for** | Owners who want power without multi-agent theater — chat, files, this-PC shell, browser rail, computer use |
| **What stays local** | Memory, Session Brief, skills, approvals, DPAPI secrets, optional SmolVLM2 vision |
| **What you bring** | Your API keys / local Ollama — no Remedy cloud account for core use |
| **Current** | **v0.26.2** on [PyPI](https://pypi.org/project/remedy-ai/) · [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases) |

From the creator: *My name is Ahmi, I hope you enjoy my Remedy.*  
In-app: title-bar / tray → **About Remedy** · **Settings → About**.

---

## What’s new

**Latest: [v0.26.2](https://github.com/AhmiDarrow/RemedyAI/releases/tag/v0.26.2)** — Host in Remedy's hands; Build does not stick on ledger. (0.26.1: Build finishes pages.)

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/docs/manual/assets/remedy-partner-architecture.jpg" alt="How Remedy fits together" width="640" />
</p>

| Highlight | Why it matters |
|-----------|----------------|
| **Host Bridge + `/stretch`** | POSIX→cmd, `pwsh -File`, `host_run` / `host_which`; `/stretch` maps this PC; `/whoami` includes the census |
| **Agency that runs tools** | Review / implement keep tools on; work turns cannot finish as chat; Settings no longer steal the active session model |
| **Vendor-neutral GPU** | NVIDIA / AMD / Intel probe; RMB autofit sizes context from **VRAM**, not a logo |
| **✕ → tray always** | Title-bar close hides to tray; local API stays warm. **Tray Quit** for full stop |
| **Write jail + security** | Project write roots, `C:/` + `$HOME` shell dests, files API refuse for `SAM`/`hosts`, packaged self-inject **off** |
| **Browser rail polish** | Video fullscreen stays **in-rail**; mobile/desktop site toggle; chat images with Bearer media; same-window OAuth |

**Also in 0.20–0.23:** L0–L3 tiers, `build_drive` + companion, Soul Field, evidence ledger, Time Crystal, messengers, signed Skills Library, `Remedy Desktop.exe` (not generic `app.exe`).

Full owner notes → **[docs/manual/13-whats-new.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md)** · engineering detail → **[CHANGELOG.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CHANGELOG.md)**  
Earlier: [0.19.0 parallel multi-provider](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md#0190---parallel-multi-provider--background-turns) · [0.18.x](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md)

---

## Two desktops, one partner

v0.26.0 made Remedy **truly multiplatform** — the same partner on Windows and Linux, the same `~/.remedy` home, the same local API on `127.0.0.1:7400`. 0.26.1 is the Build finish/drive fix on that line.

| | **Windows** | **Linux** |
|--|-------------|-----------|
| **Install** | NSIS installer from [Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) | [PyPI / source](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md) · packaged `.deb` / AppImage once a Linux desktop asset is published |
| **Runs on** | Windows 10 / 11 | Native Linux, incl. **WSLg** |
| **Close ✕** | Hides to the **tray** (API stays warm) | Minimizes to the **taskbar** (WSLg has no tray) |
| **Maximize** | Standard | **Work-area** — avoids covering the taskbar / panels |
| **Autostart** | **Start with Windows** | Not yet — no toggle on Linux |
| **CLI** | `remedy` / `Remedy Desktop.exe` | `python -m remedy` ≡ `remedy` |

Same skills, same messengers, same continuity — pick your shell.

---

## Contents

1. [About](#about) · [What’s new](#whats-new) · [Two desktops, one partner](#two-desktops-one-partner)  
2. [What you get](#what-you-get) — product at a glance  
3. [Why it’s different](#why-its-different) — local continuity + metabolism  
4. [Workspace on your PC](#workspace-on-your-pc) — files, terminal, browser, computer  
5. [Local brain (SmolVLM2)](#local-brain-smolvlm2) — vision + efficiency without a second persona  
6. [Continuity workers](#continuity-workers) — silent nano swarm  
7. [Messengers](#messengers) — chat where you already are  
8. [Skills & Library](#skills--library)  
9. [Memory & long work](#memory--long-work)  
10. [Install](#install)  
11. [Security](#security)  
12. [Slash commands](#slash-commands)  
13. [Architecture](#architecture)  
14. [CLI & API](#cli--api)  
15. [Development](#development)  
16. [Support](#support) · [License](#license)  

### Owner’s manual (GitHub + F1)

| | |
|--|--|
| **Start here** | [Overview](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md) |
| **What’s new** | [13-whats-new](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md) |
| **All chapters** | [docs/manual/](https://github.com/AhmiDarrow/RemedyAI/tree/master/docs/manual/) · [index](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/README.md) |
| **Continuity** | [How Remedy works](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/16-continuity-philosophy.md) |
| **Local SmolVLM2** | [Vision decoder](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/14-visual-decoder.md) |
| **This PC / Host** | [Coding agency](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/18-agency.md) · [RMB](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/20-rmb-local-agent.md) |
| **Metabolism** | [Partner metabolism](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/19-metabolism.md) |
| **Security** | [Security & data](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/04-security-and-data.md) |

Also: [CHANGELOG.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CHANGELOG.md) · [AGENTS.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/AGENTS.md)

---

## What you get

One partner, two desktops. One local API. Your data under `~/.remedy`.

| | Capability |
|--|------------|
| **Chat partner** | Streaming markdown, Plan/Build, multi-provider parallel tabs, attachments, image markup |
| **Workspace** | **Files** · **Terminal** · **Browser** · **Scratch** · **Computer use** — rails beside chat |
| **This PC** | **Host Bridge** (POSIX→cmd, `host_run`) · `/stretch` home census · vendor-neutral GPU/VRAM |
| **Local brain** | **SmolVLM2 2.2B** visual decoder · optional **RMB** llama.cpp host (autofit from this PC) |
| **Continuity** | Session Brief, partner memory, skills, context budget — silent workers, one voice |
| **Metabolism** | **0.22.0+** Soul Field + organism pulse, L0–L3 tiers, evidence, governor ([manual](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/19-metabolism.md)) |
| **Messengers** | Telegram, Discord, Slack, Mattermost, Matrix, WhatsApp, Teams, Google Chat, Signal (Settings) |
| **Skills** | Progressive disclosure · Installed \| Library · signed community catalog |
| **Memory** | Durable facts · this-home census · Progress snapshots · plans — calm UI, not scare-logs |
| **Agency** | `file_edit`, `build_drive`, companion, Host Bridge, write jail, `spread_run`, web tools, approvals |
| **Always ready** | **0.20.0+** title-bar **✕ → tray** (API stays up); **tray Quit** for full stop |
| **Web UI** | Same SPA at `http://127.0.0.1:7400/` (Switch to WebUI → tray) |
| **Updates** | Minisign-signed auto-update from GitHub Releases |

Your **chat model** is yours: xAI, OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral, OpenRouter, **Ollama**, or Custom. Continuity and local SmolVLM2 live **on disk**, not in a Remedy cloud.

---

## Why it’s different

```text
You  →  Continuity (brief · memory · skills · budget · local SmolVLM2)  →  Your model  →  Tools
              ↑________________ learn / compress / remember ________________|
```

| You feel | What’s actually happening |
|----------|---------------------------|
| **Fast** | Hot path stays cheap; heavy work is background |
| **Cheaper** | Less tool sludge re-sent; local SmolVLM2 where it saves paid calls |
| **Same partner** | Switch providers anytime — identity is local |

Deep dive: [Continuity philosophy](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/16-continuity-philosophy.md) · In-app **F1**.

---

## Workspace on your PC

Remedy is a **workbench**, not only a chat box. Icon rails open real tools next to the conversation:

| Tool | What it is |
|------|------------|
| **Files** | Project / session file browser — open, copy path, drag into chat |
| **Terminal** | In-app **PowerShell** (ConPTY). Agent shell uses **Host Bridge** — POSIX rewritten to cmd, PowerShell via `pwsh -File` |
| **Browser** | Embedded **WebView2** (Chromium) research pane; **↗** opens system browser when you need full Chrome |
| **Scratch** | Quick notes pad bound to the session |
| **Computer use** | Click / type / screenshot this desktop when you enable it |

Left · chat · right layout; popout / fullscreen for Terminal, Browser, Scratch.  
Manual: [Chat & sessions](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/05-chat-and-sessions.md) · [Desktop notes](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/DESKTOP.md)

---

## Local brain (SmolVLM2)

**On-device efficiency** — not a second chatbot.

| Job | Local **SmolVLM2 2.2B** |
|-----|-------------------------|
| **Visual decoder** | Images → structured text briefs so **any** chat model can reason about screenshots |
| **Prefer-local vision** | Decode here first; save provider vision tokens when you want |
| **Harness assist** | Session Brief can refresh in the background without another paid API call |
| **Continuity assist** | Optional nano refine when the server is already up (never blocks the hot path) |

**How it ships:** not in the installer → one first-run download → `~/.remedy/vision/` → **llama-server** on **127.0.0.1** → auto-starts with Remedy. CPU by default; CUDA when NVIDIA is available.

Optional **RMB** (Remedy Muscle Bridge) is a separate on-device llama.cpp chat host — Settings → local models. Autofit sizes context and GPU layers from this PC’s VRAM (NVIDIA / AMD / Intel), not a vendor logo.

Manual: [Local vision & on-device SmolVLM2](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/14-visual-decoder.md) · [RMB](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/20-rmb-local-agent.md)

---

## Continuity workers

Silent local workers (sometimes called the **nano swarm** in code). They measure, prune, rank, and distill. They **do not** take the microphone.

| Worker | Job |
|--------|-----|
| Token | Context fill, compress nudge, usage calibration |
| Router | Intent → policy (memory / skill / plan / tool / chat) |
| Memory | Session Brief touch |
| Pattern | Tool sequences, stuck signals, learn pre-gate |
| Skill | Ranking and feedback for procedures |

Heuristics first; local SmolVLM2 only when already running and useful.  
Operators: [Continuity workers](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/17-nanoswarm.md) · `/harness`

---

## Messengers

Talk to Remedy where you already chat. Sessions show up in the desktop list (e.g. `TG · …`) with shared history.

| Platform | Status (catalog) |
|----------|------------------|
| **Telegram** | Ready — long-poll bot (allowlist recommended) |
| **Discord · Slack · Mattermost · Matrix** | Ready adapters |
| **WhatsApp · Teams · Google Chat · Signal** | Partial (webhooks / signal-cli / setup-dependent) |

Configure under **Settings → Messengers**. Empty allowlist = ignore inbound (unless you allow all).  
Security defaults stay local-first: no public doorway by accident.

---

## Skills & Library

| Path | Meaning |
|------|---------|
| **Learn from work** | Multi-step success → probation skills → promote over sessions |
| **Installed** | Bundled + learned + trusted library packs on this machine |
| **Library** | Signed community catalog ([remedy-skills](https://github.com/AhmiDarrow/remedy-skills)) — install → quarantine → **Trust** |

Format: [agentskills.io](https://agentskills.io) · Lifecycle: [SKILL_LIFECYCLE.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/SKILL_LIFECYCLE.md) · Manual: [Skills](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/07-skills.md)

---

## Memory & long work

- **Durable memory** — SQLite + FTS5, profile, handoffs, living partner facts  
- **This home** — `/stretch` (alias `/home`) maps hardware, PATH tools, rooms, local ports; `/whoami` includes the census  
- **Memory Harness** — lean *send-view* for the model; full transcript kept  
- **Progress** — mid-task snapshots (calm wording: progress, not “the app crashed”)  
- **Plans** — Plan mode outlines; Build executes with approvals  
- **Time travel** — restore chat (+ best-effort files) to an earlier step  

`/compact` · `/harness` · `/stretch` · `/whoami` · [Memory manual](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/06-memory-and-harness.md)

---

## Install

| | |
|--|--|
| **Windows** | [Download the **latest** Windows installer](https://github.com/AhmiDarrow/RemedyAI/releases/latest) → run Setup |
| **Linux** | [Install from PyPI / source](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md) — `.deb` / AppImage once a Linux desktop asset is published |
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
| Scope | Project / home / full machine (opt-in) |
| Write jail | Shell dests stay in write roots (`C:/`, `$HOME`, python/node oneshots) |
| Approvals | **Ask** default |
| Skills | Quarantine until Trust |
| Messengers | Allowlist-first |

No Remedy cloud account for core use. Chat goes to **your** provider (or local Ollama).  
[Security & data](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/04-security-and-data.md)

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
┌─ Desktop (Tauri 2) ─────────────────────────────────────┐
│  React SPA · tray · updates · Files/Terminal/Browser   │
│              │                                           │
│  remedy serve · FastAPI :7400                            │
│    gateway · core · host bridge · memory · skills · vision │
└──────────────────────────────────────────────────────────┘
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
uv run pytest -q          # 560+ tests; currently ~2716
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
| Solo / small indies (&lt; $1M revenue **and** &lt; 20 FTE) | Free under LICENSE |
| Personal / education / research | Free |
| Larger orgs, SaaS, commercial resale | Written license — **ahmitdarrow@gmail.com** |

Copyright © 2025–2026 **Ahmi Darrow**.

---

*My name is Ahmi, I hope you enjoy my Remedy.*
