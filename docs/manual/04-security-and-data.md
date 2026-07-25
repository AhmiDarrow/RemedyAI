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

## Design goal

**Maximum power for you on this PC** — shell, files, skills, full scope when you enable them.  
**Not a doorway for others** — no open LAN API by default, no website token theft, no untrusted skill packs until you Trust them.

## Local API protection

- Default: API requires Bearer token (see [Providers & auth](03-providers-and-auth)).  
- Bound to **127.0.0.1** — not exposed to your LAN by default.  
- Token file is ACL-hardened (no “Everyone” write).  
- Desktop prefers OS/IPC token; browser WebUI uses loopback bootstrap only.  
- Optional: `REMEDY_HTTP_BOOTSTRAP=0` disables HTTP bootstrap (desktop IPC only).  
- CORS `*` is **refused** while auth is on.  
- Auth-off + non-loopback bind requires `REMEDY_ALLOW_INSECURE_BIND=1` (owner escape hatch).  

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

High-impact tools (e.g. shell) use **approval mode**:

| Mode | Behavior |
|------|----------|
| **Ask** (default) | Banner: Approve / Deny for sensitive actions |
| **Auto** | Fewer prompts — use only if you trust the workspace |

Commands: `/approve`, `/deny` (when an id is shown).

## Skills security

- Imported skill zips are checked for **Zip Slip** / unsafe paths.  
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
