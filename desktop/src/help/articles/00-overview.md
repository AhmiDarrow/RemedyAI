# Remedy — Owner’s Manual Overview

![Remedy wordmark](assets/hero_logo_color_on_dark.png)

**Remedy** is a partner for **any ability level**. You say a goal in your own words — shop, fill a form, research a paper, ship a game, write code — and she drives **this computer** to finish it. One continuous voice. **Not** a multi-agent farm, **not** a thin chat wrapper, **not** a medical product.

**Feel of the product:** easy, sleek, familiar, and powerful — **Grove** by default, **Studio** / Advanced when you want full rails.

Offline Help: **F1** or **Ctrl+/** · Same chapters live in the repo under `docs/manual/`.
The agent can read them anytime with **`help_list`** / **`help_read`** (not limited by
project access scope).

![How Remedy fits together](assets/remedy-partner-architecture.jpg)

---

## About

| | |
|--|--|
| **Product** | Windows + Linux desktop partner + local API (`127.0.0.1:7400`) |
| **Home surface** | **Grove** (partner). **Studio** is one tap away (workbench). |
| **Data home** | `~/.remedy` (config, memory, voice, Vault, skills; DPAPI on Windows) |
| **Models** | *Your* provider keys or Ollama / RMB — continuity stays on disk |
| **Current** | **v0.41.7** — life-task owner card, recipes, verify after writes. Public: GitHub **v0.41.5** · PyPI **`remedy-ai==0.41.5`** |
| **Install** | [Windows](01-install-windows.md) · [Linux](01-install-linux.md) · [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) · PyPI `remedy-ai` |

In-app: title-bar / tray → **About Remedy** · **Settings → About**.  
Creator note: *My name is Ahmi, I hope you enjoy my Remedy.*

---

## What’s new (start here)

**v0.41** is **multilingual**: Language pins chrome *and* replies (default Auto
= this PC + what you type; many languages + RTL). Verified Plan steps and
memory as context not a grant. Hive cannot write parent Partner Memory.
Payment checkpoints still cannot be recovered around.

**v0.38.1** closes gates that were still open in 0.38.0: hired helpers cannot
read your mail or calendar, one skill yes is not every skill, Autonomous still
asks in an untrusted folder, and Trust no longer resets when you save Settings.

**v0.38.0** is the capability architecture: policy owns authority, generic
shell does not inherit GitHub/SSH tokens, and Settings → Security & power has
a **Trust** control (Conservative / Balanced / Autonomous). Mail and payment
still always stop.

**v0.31.2** is the previous partner-line public release: thinking is this round’s scratchpad, and
she can open a folder in the Files rail (and read scratch). **v0.31.1** lets her
open Grove, Alongside, Settings, and rails herself. **v0.31.0** is a new Remedy,
not a patch on the old coding-agent line.

- **Grove** is home; she **speaks and hears** locally; Studio keeps the workbench
- **Life tasks** finish with observed success, owner checkpoints, and the **Vault**
- Silent **hive** daughters, a **research** studio, and a **game** studio
- Web fetch/search on after install; robots.txt; third-party notices; colorblind themes
- RMB defaults to **Qwen3.5-9B** — a local model that can actually call tools

**v0.30.0** (local) put Grove, voice, life tasks and the Vault on the machine.

**v0.26.2** was the last public coding-agent ship: work turns drive this PC; Build no longer hangs.

Full list: **[What’s new](13-whats-new)** · engineering: repo `CHANGELOG.md`.

---

## Contents

| | Topic |
|--|--------|
| [About](#about) · [What’s new](#whats-new-start-here) | Who / version / highlights |
| [What you get](#what-you-get) | Product at a glance |
| [Life on this computer](#life-on-this-computer) | Goals, checkpoints, Vault |
| [Grove and voice](#grove-and-voice) | Partner home |
| [Workspace tools](#workspace-tools) | Files, Terminal, Browser, Scratch, Computer |
| [Local brain](#local-brain) | SmolVLM2 + RMB |
| [Partner metabolism](#partner-metabolism) | L0–L3 speed / accuracy / trust |
| [Always ready](#always-ready-desktop) | Close → tray / taskbar |
| [Quick start](#quick-start-60-seconds) | First hour |
| [How pieces fit](#how-the-pieces-fit) | Architecture sketch |
| [Manual map](#manual-map) | All chapters |
| [Day-1 tips](#day-1-tips) | Habits |
| [License](#license-source-available) · [From the creator](#from-the-creator) | |

---

## What you get

| Area | Meaning |
|------|---------|
| **Grove** | Default partner surface — speakable, one question at a time |
| **Voice** | Speaks and hears on this PC. One voice; you can hold it. |
| **Life tasks** | Shop, book, forms, pay — act → verify → retry. Success = the goal done. |
| **Vault** | Cards and logins; typed only into verified fields |
| **Hive** | Silent daughters; they report packets, never extra chats |
| **Research** | Literature, citations that resolve, analysis ledger, field packs |
| **Game studio** | Godot-first engine verify, playtest, export |
| **Workspace** | **Files** · **Terminal** · **Browser** · **Scratch** · **Computer use** |
| **Local brain** | **SmolVLM2 2.2B** vision + **RMB** (Qwen3.5-9B default) |
| **Continuity** | Session Brief, partner memory, skills, silent nano swarm |
| **Metabolism** | Turn tiers L0–L3, evidence ledger, governor, portable identity |
| **Messengers** | Telegram (live) + Discord / Slack / Mattermost / Matrix / WhatsApp / Teams / Google Chat / Signal |
| **Skills** | Bundled + learned + **Library** (signed catalog) |
| **Web** | Fetch and search on after install; robots.txt; OpenSERP on loopback |
| **Safety** | Loopback API + Bearer, non-waivable money/send, write jail, SSRF |
| **Web UI** | Same SPA at `http://127.0.0.1:7400/` |

No Remedy cloud account for core use. Your **chat model** is yours (xAI, OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral, OpenRouter, Ollama, Custom, RMB). Continuity, voice, Vault, and vision stay under `~/.remedy`.

---

## Life on this computer

```text
GOAL  →  PLAN  →  DRIVE  →  HANDOFF (sometimes)  →  DONE
```

Anyone can tell Remedy a goal. She proposes **one plan**, then drives this PC: see the screen, use local apps, browse, fill forms, buy things. Money, passwords, submit, send, and delete **stop for you** — no mode can waive them. 2FA, CAPTCHA, and the last payment click are **designed owner moments**: she pauses, says what’s needed, and resumes.

See [Personal assistant](21-personal-assistant) · [Vault](23-vault).

---

## Grove and voice

**Grove** is the partner home (default). **Studio** is the workbench. The same status bar lives on both. She speaks locally (Chatterbox Nano / Kokoro); hearing uses whisper. Desktop downloads a pinned Python into `~/.remedy/voice/` so the installed app can actually speak.

See [Grove](22-grove).

---

## Workspace tools

Icon rails open real tools on this PC — not separate apps to juggle.

| Tool | Role |
|------|------|
| **Files** | Browse the project / session tree; open; drag into chat |
| **Terminal** | In-app PowerShell (ConPTY) |
| **Browser** | Embedded Chromium (WebView2 / WebKitGTK); **↗** for full system browser |
| **Scratch** | Session-linked notes |
| **Computer** | Optional desktop/computer-use path (navigate, click, type) with host bridge + safety |

Sessions, Settings, and these tools live on the left/right rails. See [Chat & sessions](05-chat-and-sessions).

---

## Local brain

When installed (Setup or **Settings → Advanced → Local model** — download not in the tiny installer):

- **Visual decoder** — screenshots become text briefs for any chat model
- **Prefer-local** — can decode on-device first to save provider vision tokens
- **RMB** — on-device llama.cpp chat; default **Qwen3.5-9B Q6_K**
- Binds to **127.0.0.1** only; auto-starts with Remedy when installed

Details: [Local model (SmolVLM2)](14-visual-decoder) · [RMB](20-rmb-local-agent) · Workers: [Continuity workers](17-nanoswarm)

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

**Since 0.20.0** (title-bar ✕ is always hide-to-tray on Windows — not a Settings opt-out).

| Action | Result |
|--------|--------|
| **✕ / Alt+F4** (Windows) | **Always hides to the system tray** — local API stays up (Web UI + continuity warm) |
| **✕** (Linux / WSLg) | Minimizes to the **taskbar** (WSLg has no tray) |
| **Tray → Show** (or click tray icon) | Restores the window |
| **Tray → Quit** | Full exit — stops the local server (browser WebUI dies). Warning dialog unless you opted out |

On Windows you cannot turn “close kills the app” back on for the title-bar ✕ — full stop is intentionally **Quit only**. See [Desktop notes](../DESKTOP.md).

---

## Quick start (60 seconds)

1. Install from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) (Windows NSIS or Linux `.deb` / AppImage).
2. Setup wizard: provider + workspace; install local model / voice when prompted.
3. Chat from **Grove** — try **“what model am I using?”** or **“review this project”**.
4. **F1** anytime for this wiki. Bottom bar: **Simple UI** / **Advanced UI**. Studio is one tap away.
5. Windows: close with ✕ to tray. Linux: ✕ minimizes. **Quit** only when you want a full stop.

---

## How the pieces fit

```text
You → Grove / Studio (chat + Files / Terminal / Browser / Scratch)
         → API 127.0.0.1:7400
              → Continuity (brief, memory, skills, budget)
              → Voice · Vault · hive
              → Local SmolVLM2 / RMB (when installed)
              → Your LLM provider (chat + tool results)
              → Tools, messengers, Google APIs (if connected)
         → ~/.remedy
```

---

## Manual map

| Chapter | Topic |
|---------|--------|
| [How Remedy works](16-continuity-philosophy) | Continuity philosophy |
| [Grove](22-grove) | Partner home (default surface) |
| [Personal assistant](21-personal-assistant) | Reminders, mail, calendar, life tasks |
| [Vault](23-vault) | Cards and credentials |
| [Hive](28-hive) | Silent daughters |
| [Research](27-research) | Papers, analysis, citations |
| [Game dev](26-game-dev) | Godot, web, Bevy, Pygame, Love2D studio |
| [Local vision & SmolVLM2](14-visual-decoder) | On-device efficiency |
| [RMB](20-rmb-local-agent) | Local llama.cpp chat |
| [Continuity workers](17-nanoswarm) | Nano swarm (operators) |
| [Install (Windows)](01-install-windows) | Installer & SmartScreen |
| [Install (Linux)](01-install-linux) | `.deb` / AppImage / WSLg |
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
| [Telephony](24-telephony) | Phase 0 bench |
| [Coordination](25-coordination) | Several sessions, one repo |

---

## Day-1 tips

- **Enter** send · **Shift+Enter** new line · **↑/↓** prompt history
- **@** files · **/** commands · rails for **Files / Terminal / Browser / Scratch**
- **Plan** explores · **Build** can change the machine (approvals)
- **Grove** for partner talk · **Studio** for the workbench
- **Skills → Library** for the catalog · **Installed** for what’s already trusted
- **Settings → Messengers** for Telegram and friends
- Data: `C:\Users\<you>\.remedy` on Windows · `~/.remedy` on Linux

---

## License (source-available)

| Who | Terms |
|-----|--------|
| Solo / small indies (&lt; $1M revenue **and** &lt; 20 FTE) | Free to use and modify (this copy) |
| Personal / education / research | Free (this copy) |
| Larger orgs, SaaS, commercial resale, or a paid deal | Written license — **ahmitdarrow@gmail.com** |

Binding: repo `LICENSE` (also shown by the Windows installer, and under
Settings → License) · Summary: `COMMERCIAL.md`. You are responsible for how
you use Remedy, including sites and accounts you point it at. The free grant
is not a promise Remedy stays free; paid licenses are available.

---

## From the creator

My name is Ahmi, I hope you enjoy my Remedy.

(App: title-bar menu → **About Remedy** · **Settings → About**.)
