# Security & data map

Remedy is **local-first**. Understand what is stored where and what leaves your machine.

## What stays on your PC

| Path | Contents |
|------|----------|
| `~/.remedy/config.toml` | Non-secret settings (provider id, model, persona, paths, flags) |
| `~/.remedy/auth/` | Local API token, provider keys (DPAPI when available), xAI OAuth, Google OAuth |
| `~/.remedy/memory.db` | Chat sessions, messages, tool results, memories, profile (SQLite) |
| `~/.remedy/assistant.json` | PA prefs, budget/debts/bills (local; not OAuth tokens) |
| `~/.remedy/vision/` | Local model weights (SmolVLM2) + llama-server runtime |
| `~/.remedy/skills/` | User / learned skill packs |
| `~/.remedy/skill_stats.json` | Skill success / lifecycle stats |
| `~/.remedy/backups/` | Optional memory backups |

On Windows, `~` is your user profile (`C:\Users\<you>`).  
**Note:** Chat history is not encrypted beyond your Windows user account. Anyone with your login can read `memory.db`.

## What leaves your machine (30-second version)

| Leaves? | What | When |
|---------|------|------|
| **Yes → your LLM provider** | Chat text + **tool results** for that turn | Every cloud-model reply |
| **Yes → Google (if connected)** | Mail/calendar API traffic | When PA tools run |
| **Yes → messenger platforms** | Messages you enable | When channels are on |
| **Yes → public web** | URLs you fetch | Only if `web_fetch` enabled |
| **Yes → GitHub** | Version check / update download | Update UI |
| **No Remedy cloud** | No multi-tenant Remedy mailbox or chat cloud | — |
| **Local only** | OAuth tokens, API keys, local VL inference | Stays on PC |

**Outbound chat sanitization:** before each provider HTTP call, Remedy **redacts secret-like strings/keys** and **caps oversized tool payloads**. This reduces accidental key leakage; it does **not** remove mail subjects or code you asked the agent to handle.

### Privacy mode (opt-in)

| Control | Where |
|---------|--------|
| **Privacy** chip | Bottom status bar (Simple **and** Advanced UI) |
| **Privacy** section | Settings (Simple + Advanced) |
| Also | Settings → Advanced → Security & power |

| Mode | Behavior |
|------|----------|
| **Off** (default) | Lightning path — secret scrub + normal tool caps. Best for capable coding. |
| **On** | Email / phone / SSN shapes redacted · shorter tool results (especially mail/page/computer) · still secret-safe |

API keys and OAuth tokens never leave as model input either way. Env override: `REMEDY_PRIVACY_MODE=1`.

## Personal assistant (Gmail / Calendar)

| Item | Where it goes |
|------|----------------|
| OAuth tokens | **This PC only** (`~/.remedy/auth/google.json`, DPAPI on Windows) |
| Client secrets | Same auth dir — never in chat or model requests |
| Mail/calendar **API** calls | This PC ↔ Google (official APIs) |
| Mail/calendar **content in chat** | Tool results you trigger → **chosen LLM provider** (snippets preferred) |
| Remedy cloud mailbox | **None** |

**Consent:** **Connect** opens a dialog for **Privacy & AI** + account access (not a Settings wall). Disconnect clears local tokens. Drafts do not auto-send.

## Computer-use (Browser rail)

| Item | Where it goes |
|------|----------------|
| Page text / DOM actions | On this PC via desktop host (loopback) |
| Tool results to the model | May include page text → **LLM provider** for that turn |
| Password fields | Snapshot **does not** send password/OTP values (shows `[filled]`) |
| Prefer | DOM/UIA over screenshots; confirm form with snapshot before typing secrets |

## Simple / Advanced (UI)

| Control | Where | Meaning |
|---------|--------|---------|
| **Simple UI / Advanced UI** | Bottom status bar | How busy the **chrome** is (Memory, Skills, Think, …) |
| **Simple \| Advanced** | Settings header | How many **settings knobs** are listed |
| **Privacy** | Status bar + Settings | Always available — not buried in Advanced-only chrome |

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

## Web tools (`web_fetch` / `web_search`)

- **On by default** after install (`web_tools_enabled = true`). Turn off in Settings → Security & power if you want her offline.
- First run downloads **OpenSERP** (~10 MB) to `~/.remedy/bin` and runs it on `127.0.0.1` only. Until it is ready, search uses DuckDuckGo's no-JavaScript results page.
- **SSRF protection**: private/localhost/metadata hosts blocked for fetched pages; DNS is resolved once and the connection is **pinned** to a public IP (mitigates DNS rebinding). Redirects re-validated per hop. The managed OpenSERP is a separate loopback process, not a general private-host bypass.  

## WebUI vs quit (always-ready · **0.20.0+**)

| Action | Server | Notes |
|--------|--------|--------|
| **✕ / Alt+F4** | **Stays up** | **0.20.0+** always hides to tray (not a Settings toggle). Continuity stays warm. |
| **Switch to WebUI** | **Stays up** | Opens `http://127.0.0.1:7400/` and hides desktop. |
| **Tray → Quit** | **Stops** | Full exit. Warning dialog unless you disabled it in Settings. |

Secrets stay under `~/.remedy/auth` (DPAPI-sealed tokens where applicable). Tools cannot
write into auth paths via shell or file tools (**0.20.0+** jail). `web_fetch` / `web_search`
block private and metadata targets (SSRF fail-closed).

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

Remedy is **source-available** (see repo `LICENSE` / `COMMERCIAL.md`). Free for solo and small indies under the published threshold **for this copy**; larger commercial use, resale, or a paid deal needs written permission. This build has **no** license keys — ownership and terms live in the license text. Paid licenses are available; the free grant is not a promise later versions stay free. You are responsible for how you use Remedy, including sites and accounts you point it at.

## Skills security

- Imported skill zips are checked for **Zip Slip**, path escape, and **streamed size caps** (decompression bombs).  
- Quarantined skills **cannot** run until you **Trust** them in the Skills panel.  
- Prefer bundled / reviewed skills for production workflows.  

## Secrets hygiene

- Never paste long-lived keys into chat if you can use Settings.  
- `config.toml` should not contain raw API keys after modern saves.  
- Rotate provider keys if a machine is shared or compromised.  
- Full uninstall wipe removes `~/.remedy` when you choose **full wipe**.  
- In-app **Wipe persona** (Settings → You & Agent) forgets facts about you only — not chats, keys, or the app. Type **WIPE** to confirm.

## Plan vs Build

- **Plan mode** — explore and answer without applying project edits.  
- **Build mode** — tools may edit files / run commands (subject to approvals).  

Toggle from the status bar or **Ctrl+B**.

## Related

- [Providers & auth](03-providers-and-auth) · [Skills](07-skills) · [Updates & uninstall](08-updates-and-uninstall)
