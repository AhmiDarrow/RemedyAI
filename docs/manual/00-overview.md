# Remedy — Owner’s Manual Overview

**Remedy** is your personal AI partner for knowledge, design, code, and get-it-done work on **your machine**. It is **not** a medical product.

**Feel of the product:** easy, sleek, beautiful, familiar, and powerful — simple by default, full power when you want it (Simple / Advanced on the bottom bar and in Settings).

Offline Help: **F1** or **Ctrl+/**.

---

## Contents

| | Topic |
|--|--------|
| [What you get](#what-you-get) | Product at a glance |
| [Workspace tools](#workspace-tools) | Files, Terminal, Browser, Scratch |
| [Local brain](#local-brain) | Qwen on this PC |
| [Quick start](#quick-start-60-seconds) | First hour |
| [How pieces fit](#how-the-pieces-fit) | Architecture sketch |
| [Manual map](#manual-map) | All chapters |
| [Day-1 tips](#day-1-tips) | Habits |
| [License](#license-source-available) · [From the creator](#from-the-creator) | |

---

## What you get

| Area | Meaning |
|------|---------|
| **Chat partner** | Streaming chat, Plan/Build, attachments, image markup |
| **Workspace** | **Files** · **Terminal** · **Browser** · **Scratch** beside chat |
| **Local brain** | **SmolVLM2 2.2B** (Apache 2.0) on this PC — vision briefs + local assist (not a second persona) |
| **Continuity** | Session Brief, memory, skills, silent workers (nano swarm) |
| **Messengers** | Telegram and modular connectors in **Settings → Messengers** |
| **Skills** | Bundled + learned + **Library** (signed catalog) |
| **Memory** | Durable store · Progress snapshots · plans |
| **Safety** | Loopback API, approvals, scope, quarantine until Trust |
| **Web UI** | Same app in the browser at `http://127.0.0.1:7400/` |

No Remedy cloud account for core use. Your chat model is yours (xAI, OpenAI, Ollama, …).

---

## Workspace tools

Icon rails open real tools on this PC — not separate apps to juggle.

| Tool | Role |
|------|------|
| **Files** | Browse the project / session tree; open; drag into chat |
| **Terminal** | In-app PowerShell (ConPTY) |
| **Browser** | Embedded Chromium (WebView2); **↗** for full system browser |
| **Scratch** | Session-linked notes |

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

## Quick start (60 seconds)

1. Install from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).  
2. Setup wizard: provider + workspace; install local model when prompted.  
3. Chat, or try `/help`.  
4. **F1** anytime for this wiki. Bottom bar: **Simple UI** / **Advanced UI**.

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
