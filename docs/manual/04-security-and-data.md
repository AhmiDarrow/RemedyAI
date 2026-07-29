# Security & data map

Remedy is **local-first**. Understand what is stored where and what leaves your machine.

## What stays on your PC

| Path | Contents |
|------|----------|
| `~/.remedy/config.toml` | Non-secret settings (provider id, model, persona, paths, flags) |
| `~/.remedy/auth/` | Local API token, provider keys (DPAPI when available), xAI OAuth |
| `~/.remedy/memory.db` | Chat sessions, memories, profile, handoffs (SQLite) |
| `~/.remedy/skills/` | User / learned skill packs |
| `~/.remedy/skill_stats.json` | Skill success / lifecycle stats |
| `~/.remedy/backups/` | Optional memory backups |

On Windows, `~` is your user profile (`C:\Users\<you>`).

## What leaves your machine

- **Chat prompts and tool results** are sent to the **LLM provider you configured** (OpenAI, xAI, etc.).  
- **Ollama** keeps inference local if you use only local models.  
- **Update checks** contact GitHub Releases (version metadata / installer download).  
- There is **no** Remedy cloud account required for core chat.

## Personal assistant (Gmail / Calendar)

| Item | Where it goes |
|------|----------------|
| OAuth tokens | **This PC only** (`~/.remedy/auth/google.json`, DPAPI on Windows) |
| Client secrets | Same auth dir — never in chat or model requests |
| Mail/calendar **API** calls | This PC ↔ Google (official APIs) |
| Mail/calendar **content in chat** | Only as **tool results** for turns you trigger → your **chosen LLM provider** |
| Remedy cloud mailbox | **None** |

**Consent:** Settings → Personal assistant requires accepting **Privacy & AI** and **account access** before Connect. Disconnect clears local tokens. Tools prefer short snippets; full body only via explicit read tools. Drafts do not auto-send.

## Design goal

**Maximum power for you on this PC** — shell, files, skills, full scope when you enable them.  
**Not a doorway for others** — no open LAN API by default, no website token theft, no untrusted skill packs until you Trust them.

## Local API protection

- Default: API requires Bearer token (see [Providers & auth](03-providers-and-auth)).  
- Bound to **127.0.0.1** — not exposed to your LAN by default.  
- Token file is ACL-hardened (no “Everyone” write).  
- Desktop prefers OS/IPC token; browser WebUI uses loopback bootstrap only.  
- **Browser token bootstrap** (Settings → Security & power, or `http_bootstrap` / `REMEDY_HTTP_BOOTSTRAP`):  
  - **On** (default) — browser Web UI can obtain the loopback token (full Web UI power).  
  - **Off** — desktop IPC only (safer against lesser local processes; desktop app power unchanged).  
- CORS `*` is **refused** while auth is on.  
- Auth-off + non-loopback bind requires `REMEDY_ALLOW_INSECURE_BIND=1` (owner escape hatch).  

## Web tools (`web_fetch`)

- **Off by default** (`web_tools_enabled = false`). Turn on in Settings when you want online fetch.  
- **SSRF protection**: private/localhost/metadata hosts blocked; DNS is resolved once and the connection is **pinned** to a public IP (mitigates DNS rebinding). Redirects re-validated per hop.  
- Does **not** remove public-web fetch power when enabled — only blocks non-public targets.  

## WebUI vs quit

- **Switch to WebUI** / hide-to-tray keeps the local server running.  
- **Quit** stops the API — browser WebUI disconnects. You get a warning (can disable in Settings).  

## Access scope

Settings → **Access scope** limits where tools may operate:

| Scope | Meaning |
|-------|---------|
| **Project** (default when a folder is set) | Project folder **plus** Desktop / Documents / Downloads |
| **Home** | Full user home profile |
| **Full** | Broader user-machine access (opt-in) |
| **Untrusted** | Project root only (strict) |

**No project folder** (empty / `.`): tools use **full** access automatically so you are not jailed to the install directory. Settings shows a warning — pick a project folder for focused coding (narrower jail + clearer defaults).

Always prefer the narrowest scope that still works for your task.

## Approvals

High-impact tools (shell, file write/edit, skill scripts) use **approval mode**.  
**Power is never stripped for the owner** — only the default prompt surface changes:

| Mode | Behavior |
|------|----------|
| **Ask** (default, safe) | Banner: Approve / Deny for high-impact tools; soft-risk shell patterns are labeled |
| **Auto** | **Work until done** — no prompts on trusted scopes (project/home/full). Remedy runs shell/write/skills to finish. |

**Untrusted** access scope still always asks, even in Auto (downloaded folders).  
Hard wipe/privilege blocks (`check_dangerous_command`) still apply in every mode — those are safety rails, not “power stripped.”

Commands: `/approve`, `/deny` (when an id is shown). Status bar thumbs toggle Ask/Auto.

## License (not a security control)

Remedy is **source-available** (see repo `LICENSE` / `COMMERCIAL.md`). Free for solo and small indies under the published threshold; larger commercial use needs written permission. This is **not** enforced by license keys in the app — ownership and terms live in the license text.

## Skills security

- Imported skill zips are checked for **Zip Slip**, path escape, and **streamed size caps** (decompression bombs).  
- Quarantined skills **cannot** run until you **Trust** them in the Skills panel.  
- Prefer bundled / reviewed skills for production workflows.  

## Secrets hygiene

- Never paste long-lived keys into chat if you can use Settings.  
- `config.toml` should not contain raw API keys after modern saves.  
- Rotate provider keys if a machine is shared or compromised.  
- Full uninstall wipe removes `~/.remedy` when you choose **full wipe**.

## Plan vs Build

- **Plan mode** — explore and answer without applying project edits.  
- **Build mode** — tools may edit files / run commands (subject to approvals).  

Toggle from the status bar or **Ctrl+B**.

## Related

- [Providers & auth](03-providers-and-auth) · [Skills](07-skills) · [Updates & uninstall](08-updates-and-uninstall)
