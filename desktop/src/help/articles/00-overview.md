# Remedy — Owner’s Manual Overview

![Remedy wordmark](assets/hero_logo_color_on_dark.png)

**Remedy** is your personal AI partner for knowledge, design, code, computer use, and get-it-done work on **your machine**. One continuous voice — **not** a multi-agent farm, **not** a thin chat wrapper, **not** a medical product.

**Feel of the product:** easy, sleek, familiar, and powerful — Simple UI by default, Advanced when you want full rails, metabolism, and process detail.

Offline Help: **F1** or **Ctrl+/** · Same chapters live in the repo under `docs/manual/`.
The agent can read them anytime with **`help_list`** / **`help_read`** (not limited by
project access scope).

![How Remedy fits together](assets/remedy-partner-architecture.jpg)

---

## About

| | |
|--|--|
| **Product** | Windows + Linux desktop partner + local API (`127.0.0.1:7400`) |
| **Data home** | `~/.remedy` (config, memory, skills; DPAPI on Windows) |
| **Models** | *Your* provider keys or Ollama / RMB — continuity stays on disk |
| **Current** | **v0.30.0** — Grove, voice, life tasks and the Vault; telephony Phase 0 |
| **Install** | [Windows](01-install-windows.md) · [Linux](01-install-linux.md) · [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) · PyPI `remedy-ai` |

In-app: title-bar / tray → **About Remedy** · **Settings → About**.  
Creator note: *My name is Ahmi, I hope you enjoy my Remedy.*

---

## What’s new (start here)

**v0.30.0** highlights:

- Grove is the default surface; Remedy speaks and hears locally
- Life tasks with observed success, owner checkpoints, and the Remedy Vault
- Reminders, mail, calendar, documents; telephony bench-only (Phase 0)

**v0.26.2** highlights:

- Work turns drive this PC (no Ask pause); knowledge follow-ups keep tools
- Build no longer hangs on `pytest --lf` / stale profile todos

**v0.26.1** highlights:

- Build drives the host (no Ask pause) and cannot claim done on empty or missing files
- Frustrated follow-ups stay armed; sidecar path is not a false jail dest

**v0.26.0** highlights:

- Linux / WSLg desktop (work-area maximize, taskbar close, no fake Start-with-Windows)
- Plan mode cannot write; `/reset` and Stop leave a clean session
- Windows host rewrite + write jail; Settings no longer stall the API

**v0.21.1** highlights:

- Multi-tab **turn-local continuity** (Session Brief / Partner State isolated)  
- Self-inject **snapshot restore** (no wipe of unrelated dirty work)  
- Nested shell **privilege hard-blocks**; webhook secret path fixed  
- Partner **metabolism** (L0–L3), always-ready tray, privacy, browser rail (0.20 line)

Full list: **[What’s new](13-whats-new)** · engineering: repo `CHANGELOG.md`.

---

## Contents

| | Topic |
|--|--------|
| [About](#about) · [What’s new](#whats-new-start-here) | Who / version / highlights |
| [What you get](#what-you-get) | Product at a glance |
| [Workspace tools](#workspace-tools) | Files, Terminal, Browser, Scratch, Computer |
| [Local brain](#local-brain) | SmolVLM2 on this PC |
| [Partner metabolism](#partner-metabolism) | L0–L3 speed / accuracy / trust |
| [Always ready](#always-ready-desktop) | Close → tray, local API |
| [Quick start](#quick-start-60-seconds) | First hour |
| [How pieces fit](#how-the-pieces-fit) | Architecture sketch |
| [Manual map](#manual-map) | All chapters |
| [Day-1 tips](#day-1-tips) | Habits |
| [License](#license-source-available) · [From the creator](#from-the-creator) | |

---

## What you get

| Area | Meaning |
|------|---------|
| **Chat partner** | Streaming markdown, Plan/Build, multi-provider tabs, attachments, image markup |
| **Workspace** | **Files** · **Terminal** · **Browser** · **Scratch** · **Computer use** rails beside chat |
| **Local brain** | **SmolVLM2 2.2B** on this PC — vision briefs + harness assist (not a second persona) |
| **Continuity** | Session Brief, partner memory, skills, silent nano swarm |
| **Metabolism** | **0.20.0+** turn tiers L0–L3, evidence ledger, shadow, Action IR, governor, portable identity |
| **Messengers** | Telegram (live) + modular Discord / Slack / Mattermost / Matrix / WhatsApp / Teams / Google Chat / Signal |
| **Skills** | Bundled + learned + **Library** (signed catalog); progressive disclosure (`skill_activate`) |
| **Agency** | `file_edit`, repo search, shell (write jail), missions, `spread_run`, `web_search` / `web_fetch` |
| **Safety** | Loopback API + Bearer token, approvals, access scope, SSRF, secret redaction, quarantine until Trust |
| **Web UI** | Same SPA at `http://127.0.0.1:7400/` (Switch to WebUI keeps the server alive in the tray) |

No Remedy cloud account for core use. Your **chat model** is yours (xAI, OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral, OpenRouter, Ollama, Custom). Continuity and vision weights stay under `~/.remedy`.

---

## Workspace tools

Icon rails open real tools on this PC — not separate apps to juggle.

| Tool | Role |
|------|------|
| **Files** | Browse the project / session tree; open; drag into chat |
| **Terminal** | In-app PowerShell (ConPTY) |
| **Browser** | Embedded Chromium (WebView2); **↗** for full system browser |
| **Scratch** | Session-linked notes |
| **Computer** | Optional desktop/computer-use path (navigate, click, type) with host bridge + safety |

Sessions, Settings, and these tools live on the left/right rails. See [Chat & sessions](05-chat-and-sessions).

---

## Local brain

When installed (Setup or **Settings → Advanced → Local model** — download not in the tiny installer):

- **Visual decoder** — screenshots become text briefs for any chat model  
- **Prefer-local** — can decode on-device first to save provider vision tokens  
- **Shared weights** — vision + nano assist share **SmolVLM2 2.2B** (Apache 2.0)  
- Binds to **127.0.0.1** only; auto-starts with Remedy when installed  

Details: [Local model (SmolVLM2)](14-visual-decoder) · Workers: [Continuity workers](17-nanoswarm)

---

## Partner metabolism

**Since 0.20.0.** Silent local “partner OS” so any frontier model acts faster, leaner, and safer — still **one voice**.

| Tier | When | Behavior |
|------|------|----------|
| **L0** | “What model…?”, skills list, version, whoami | Instant **local** answer — no provider tokens |
| **L1** | Pure chat | Lean context; tools off unless the message needs them |
| **L2** | Review / implement / files / shell / browse | Full tools, evidence ledger, shadow on high-blast |
| **L3** | Work alone / full suite / partitionable work | Deep agency + force-spread muscle |

Also: evidence/decision currency, machine map, Action IR, Time Crystal, skill genome, quality governor, portable encrypted identity export. Operator: `/harness` · **F1 → Partner Metabolism** · [19-metabolism](19-metabolism).

---

## Always-ready desktop

**Since 0.20.0** (title-bar ✕ is always hide-to-tray — not a Settings opt-out).

| Action | Result |
|--------|--------|
| **✕ / Alt+F4** | **Always hides to the system tray** — local API stays up (Web UI + continuity warm) |
| **Tray → Show** (or click tray icon) | Restores the window |
| **Tray → Quit** | Full exit — stops the local server (browser WebUI dies). Warning dialog unless you opted out |

You cannot turn “close kills the app” back on for the title-bar ✕ — full stop is intentionally **Quit only**. See [Desktop notes](../DESKTOP.md).

---

## Quick start (60 seconds)

1. Install from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).  
2. Setup wizard: provider + workspace; install local model when prompted.  
3. Chat — try **“what model am I using?”** (L0) or **“review project”** (tools + agency).  
4. **F1** anytime for this wiki. Bottom bar: **Simple UI** / **Advanced UI**.  
5. Close with ✕ to tray when you step away; **Quit** from the tray only when you want a full stop.

---

## How the pieces fit

```text
You → Desktop (chat + Files / Terminal / Browser / Scratch)
         → API 127.0.0.1:7400
              → Continuity (brief, memory, skills, budget)
              → Local SmolVLM2 (dependency when installed) — vision · nano
              → Your LLM provider (chat + tool results)
              → Tools, messengers, Google APIs (if connected)
         → ~/.remedy
```

---

## Manual map

| Chapter | Topic |
|---------|--------|
| [How Remedy works](16-continuity-philosophy) | Continuity philosophy |
| [Local vision & SmolVLM2](14-visual-decoder) | On-device efficiency |
| [Continuity workers](17-nanoswarm) | Nano swarm (operators) |
| [Install (Windows)](01-install-windows) | Installer & SmartScreen |
| [First run](02-first-run) | Setup wizard |
| [Providers & auth](03-providers-and-auth) | Keys, OAuth, Ollama |
| [Security & data](04-security-and-data) | Tokens, scope, approvals |
| [Chat & sessions](05-chat-and-sessions) | UI, rails, Plan/Build |
| [Memory & harness](06-memory-and-harness) | `/compact`, Progress |
| [Coding agency](18-agency) | Build-class tools |
| [Skills](07-skills) | Trust, Library |
| [Updates & uninstall](08-updates-and-uninstall) | Updates, wipe |
| [Troubleshooting](09-troubleshooting) | When things fail |
| [CLI & API](10-cli-and-api) | Power users |
| [Commands](11-reference-commands) | Slash reference |
| [Shortcuts](12-reference-shortcuts) | Keyboard |
| [What’s new](13-whats-new) | Recent changes |
| [Free providers](15-free-providers) | Free / demo options |
| [Game dev](26-game-dev) | Godot, web, Bevy, Pygame, Love2D studio |

---

## Day-1 tips

- **Enter** send · **Shift+Enter** new line · **↑/↓** prompt history  
- **@** files · **/** commands · rails for **Files / Terminal / Browser / Scratch**  
- **Plan** explores · **Build** can change the machine (approvals)  
- **Skills → Library** for the catalog · **Installed** for what’s already trusted  
- **Settings → Messengers** for Telegram and friends  
- Data: `C:\Users\<you>\.remedy` on Windows  

---

## License (source-available)

| Who | Terms |
|-----|--------|
| Solo / small indies (&lt; $1M revenue **and** &lt; 20 FTE) | Free to use and modify |
| Personal / education / research | Free |
| Larger orgs, SaaS, commercial resale | Written license — **ahmitdarrow@gmail.com** |

Binding: repo `LICENSE` · Summary: `COMMERCIAL.md`.

---

## From the creator

My name is Ahmi, I hope you enjoy my Remedy.

(App: title-bar menu → **About Remedy** · **Settings → About**.)
