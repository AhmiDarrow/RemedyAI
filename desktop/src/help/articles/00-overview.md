# Remedy — Owner’s Manual Overview

**Remedy** is your personal AI partner for knowledge, design, code, and get-it-done work on **your machine**. It is **not** a medical or clinical product.

This Help wiki is the full technical owner’s manual, available offline inside the desktop app (**F1** or **Ctrl+/**).

## What you get

| Area | What it means |
|------|----------------|
| **Desktop app** | Chat UI + local server (sidecar) — recommended for everyone |
| **Local data** | Config, memory, skills, and auth under `~/.remedy` |
| **Providers** | OpenAI, Anthropic, Google, DeepSeek, xAI, Groq, Mistral, OpenRouter, Ollama, Custom |
| **Skills** | Portable instruction packs the agent can load on demand |
| **Memory** | Durable facts, profile, session continuity, Session Brief |
| **Safety** | Approvals for high-impact tools, access scope, local API auth |

## Quick start (60 seconds)

1. Install Remedy Desktop from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).
2. Finish the **Setup wizard** (provider + optional workspace).
3. Type a question in chat, or try `/help` for the command card.
4. Press **F1** anytime to return to this wiki.

## How the pieces fit

```
You → Remedy Desktop (Tauri) → local API on 127.0.0.1:7400 → LLM provider
                ↓
         Continuity (Session Brief, memory, skills, context budget)
                ↓
         ~/.remedy (config, memory.db, skills, auth, project learning)
```

The **continuity layer** is silent: it keeps long work coherent and cheaper without
feeling like a swarm of agents. See [How Remedy works (continuity)](16-continuity-philosophy).

Nothing in this architecture requires a Remedy cloud account. Chat content goes to **your chosen LLM provider** (or local Ollama). Secrets stay on disk (DPAPI-encrypted on Windows when available).

## License (source-available)

| Who | Terms |
|-----|--------|
| **Solo / small indies** (&lt; $1M revenue **and** &lt; 20 FTE) | Free to use and modify |
| **Personal / education / research** | Free |
| **Larger orgs, SaaS hosting, commercial resale** | Written license — email **ahmitdarrow@gmail.com** |

Copyright **Ahmi Darrow**. Binding text: repo `LICENSE`; summary: `COMMERCIAL.md`. No license keys or phone-home in the app. Settings → **License** shows the same summary offline.

## From the creator

My name is Ahmi, I hope you enjoy my Remedy.

(Also in the app: title-bar menu → **About Remedy**, and **Settings → About**.)

## Manual map

| Chapter | Topic |
|---------|--------|
| [How Remedy works](16-continuity-philosophy) | Continuity philosophy (partner, not bot farm) |
| [Continuity workers](17-nanoswarm) | Nano swarm internals (operator guide — not chat branding) |
| [Install (Windows)](01-install-windows) | Installer, paths, SmartScreen |
| [First run](02-first-run) | Setup wizard, Skip, re-setup |
| [Providers & auth](03-providers-and-auth) | Keys, xAI OAuth, Ollama |
| [Security & data](04-security-and-data) | Tokens, scope, approvals |
| [Chat & sessions](05-chat-and-sessions) | UI map, Plan/Build, export |
| [Memory & harness](06-memory-and-harness) | `/remember`, `/compact` |
| [Coding agency](18-agency) | `file_edit`, missions, work alone |
| [Skills](07-skills) | Lifecycle, Trust, panel |
| [Updates & uninstall](08-updates-and-uninstall) | Auto-update, wipe options |
| [Troubleshooting](09-troubleshooting) | Server, OAuth, Defender |
| [CLI & API](10-cli-and-api) | Power-user surfaces |
| [Commands](11-reference-commands) | Full slash reference |
| [Shortcuts](12-reference-shortcuts) | Keyboard map |
| [What’s new](13-whats-new) | Recent product changes |

## Day-1 tips

- **Enter** sends · **Shift+Enter** new line · **↑/↓** prompt history  
- **@** attach project files · **/** slash commands  
- **Plan** mode explores without editing · **Build** can change files  
- Status bar **Min / Med / Full** controls tool-process detail (answer always full)  
- Data lives under `C:\Users\<you>\.remedy` on Windows  

Continue with [Install (Windows)](01-install-windows) or jump to [Troubleshooting](09-troubleshooting) if something already failed.
