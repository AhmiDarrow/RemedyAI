# Remedy Desktop — Primary User Interface

## Overview

**Remedy Desktop** is the recommended way to use Remedy — your personal AI
partner for knowledge, design, code, and get-it-done work (not a medical or
clinical product). Current package series: **0.10.x** (see root `CHANGELOG.md`).

### Partner features (0.10.18–0.10.25)

- Chat bubbles: user right / Remedy left (theme tokens); sleek shrink-wrap; user name/initials
- Stick-to-bottom feed (tokens, thinking, tools); **↓** if you scrolled up
- **Tool process**: Off / Medium / Full (status bar **Proc** + Settings)
- Prompt history: ↑ / ↓ in the composer
- Title-bar wordmark menu: Settings, About, Updates; session avatars use circuit-R
- Settings: your name, agent name, persona, project browse, scope, harness, themes, density, accent
- Sessions: auto-title from first prompt; rename; search / pin / tags
- Approvals banner for high-impact shell commands
- Tray: Show, Settings, updates, About, Quit
- ComfyUI + portable local discovery

## Branding / icons

App icons are generated from `assets/remedy_icon.png` (circuit-R monogram):

```bash
python scripts/setup_branding.py
```

That refreshes `desktop/src-tauri/icons/*` (including multi-size `icon.ico` for
Windows taskbar) and public favicons. **Rebuild the desktop app** after running
the script so the new ICO is embedded in the EXE.

### Windows Defender: `Behavior:Win32/Persistence.A!ml`

Older builds (0.10.19–0.10.21) used the **HKCU Run** registry key for “Start with Windows”.
Defender’s ML model often flags that pattern as malware-style persistence.

**Fix (0.10.22+):**
- Autostart uses only the **Startup folder** shortcut  
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk`)
- Visible under **Settings → Apps → Startup**
- Registry Run entries are **never written**; install/launch **remove** any legacy ones

**If Defender already blocked an older install:**
1. Update to 0.10.22+ (or reinstall the new installer).
2. Windows Security → Virus & threat protection → Protection history → allow Remedy if listed.
3. Optional: Windows Security → Manage settings → Add exclusion for  
   `%LOCALAPPDATA%\Remedy Desktop\` (only if you trust the signed release).
4. Confirm no `RemedyDesktop` value under  
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

### Taskbar still shows an old (medical) icon?

Windows caches taskbar icons aggressively. After reinstalling/rebuilding:

1. Fully quit Remedy (tray → Quit).
2. Clear the icon cache, e.g. in PowerShell as your user:

```powershell
# Stop explorer, clear icon cache, restart
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Remove-Item "$env:LOCALAPPDATA\IconCache.db" -Force -ErrorAction SilentlyContinue
Remove-Item "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\iconcache*" -Force -ErrorAction SilentlyContinue
Start-Process explorer
```

3. Unpin Remedy from the taskbar and pin again (or reboot).
4. Confirm you launched the newly built `app.exe` under
   `%LOCALAPPDATA%\Remedy Desktop\`.

It bundles the full Remedy server as a sidecar inside a native Tauri application,
so users only need to download and run one installer — no Python, Node, or Rust
toolchain required.

The desktop app provides an OpenCode-like chat interface with streaming tokens,
session management, file/image attachments (drag-and-drop), slash commands,
themes, first-run setup, bundled skills, and persistent memory. Releases are
minisign-signed for in-app auto-update.

## Goal

A **Tauri desktop app** (Windows-first) with an interactive chat UX, backed by an
**extended Remedy FastAPI** server. The desktop is the primary installation target;
CLI and web UI remain available as power-user features.

## Architecture

```
┌─────────────────────────────────────────────┐
│  remedy-desktop (Tauri 2)                   │
│  ┌───────────────────────────────────────┐  │
│  │  Web UI (React 19 + Vite + Tailwind)  │  │
│  │  chat · sessions · slash · markdown   │  │
│  └─────────────────┬─────────────────────┘  │
│                    │ HTTP + SSE             │
│  ┌─────────────────▼─────────────────────┐  │
│  │  Sidecar: `remedy serve` (Python)     │  │
│  │  extended session/message/event API   │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## API Contract (v1)

### Sessions

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/sessions` | List sessions, sorted by last message |
| `POST` | `/api/sessions` | Create session (`{ title?: string, model?: string }`) |
| `GET` | `/api/sessions/{id}` | Session detail + message count |
| `PATCH` | `/api/sessions/{id}` | Rename session (`{ title }`) |
| `DELETE` | `/api/sessions/{id}` | Delete session + all messages |
| `POST` | `/api/sessions/{id}/abort` | Stop active generation |

### Messages

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/sessions/{id}/messages` | List messages (`?limit=50`) |
| `POST` | `/api/sessions/{id}/messages` | Sync send, returns full response |
| `POST` | `/api/sessions/{id}/messages/stream` | SSE: `thinking`, `token`, `tool_call`, `tool_result`, `done`, `error` |

### Attachments

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/sessions/{id}/attachments` | Upload file (JSON + base64 preferred in frozen sidecar) |
| `GET` | `/api/sessions/{id}/attachments/{filename}` | Download stored attachment |

Same-name re-upload overwrites the prior file (no `_N` suffixes).

### Management

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/status` | Health / version / provider status |
| `GET` | `/api/metrics` | JSON metrics + health; `?format=prometheus` for scrape text |
| | | Chat latency: `remedy_chat_duration_seconds{path=session_stream\|session_message\|chat}` |
| `GET` | `/api/models` | Available LLM models + default (auto-discovers from provider) |
| `GET` | `/api/providers` | Provider catalog (auth modes, models, advanced flag) |
| `GET` | `/api/providers/ollama/detect` | Probe local Ollama for setup suggestions |
| `GET` | `/api/auth/xai` | xAI connection status (OAuth / API key) |
| `POST` | `/api/auth/xai/login` | Start xAI device-code OAuth |
| `GET` | `/api/auth/xai/login/status` | Poll OAuth until connected |
| `POST` | `/api/auth/xai/apikey` | Save xAI console API key |
| `DELETE` | `/api/auth/xai` | Sign out / clear xAI tokens |
| `GET` | `/api/agents` | Available agent profiles |
| `POST` | `/api/sessions/{id}/command` | Execute slash command |
| `GET` | `/api/skills` | List skills (including bundled defaults) |

### Events (SSE)

```
event: token         → { text: "Hello" }
event: thinking      → { text: "..." }
event: tool_call     → { name: "read_file", args: {...} }
event: tool_result   → { name: "read_file", output: "..." }
event: done          → { request_id: "..." }
event: error         → { message: "..." }
```

## UI Layout (OpenCode-like)

```
┌──────────────┬──────────────────────────────────────┐
│ Session List │  Message Feed                        │
│              │  ┌──────────────────────────────────┐│
│  + New       │  │ User: "What files are in src?"   ││
│  ──────────  │  │ Agent: "src/ contains..."        ││
│  Session 1   │  │           [markdown + code]       ││
│  Session 2   │  └──────────────────────────────────┘│
│  Session 3   │                                      │
│              │  ┌──────────────────────────────────┐│
│              │  │ Composer                  [model] ││
│              │  │ [multiline input + send/stop]     ││
│              │  └──────────────────────────────────┘│
│              │  Status: ● Connected · remedy v0.10.15│
└──────────────┴──────────────────────────────────────┘
```

## Provider setup (v0.10.15+)

- **Known brands** (OpenAI, Anthropic, Google, DeepSeek, **xAI**, Groq, Mistral,
  OpenRouter, Ollama): no Base URL field — catalog fills it.
- **xAI**: primary **Sign in with xAI** (device-code OAuth); secondary API key.
  Opens the system browser for verification; tokens in `~/.remedy/auth/xai.json`.
- **Custom / OpenAI-compatible**: under **Show advanced** (Base URL editable).
- **Ollama**: auto-detect when local server responds; no API key required.
- Themes default to **System**; Settings → Help & shortcuts lists hotkeys.

## Slash Commands (v1)

| Command | Action |
|---------|--------|
| `/help` | Show available commands |
| `/new` | Create new session |
| `/sessions` | List all sessions |
| `/compact` | Compact/summarize current session |
| `/models` | List available models |
| `/thinking` | Toggle thinking visibility |
| `/memory` | Search memory |
| `/skills` | List available skills |
| `/handoff` | List handoff notes |

## Implementation Order

### Phase 0 — API Foundation (Python, in-repo)
1. Session/message models in `models.py`
2. Session/message tables in `memory/store.py`
3. Structured SSE streaming in `api.py`
4. Full session/message/command/model REST endpoints

### Phase 1 — Web UI (React + Vite + Tailwind)
5. Scaffold `desktop/` with Vite + React + Tailwind
6. Session sidebar with list & create
7. Message feed with markdown rendering
8. Composer with send/stop + model selector
9. Slash command palette
10. Status bar

### Phase 2 — Tauri Shell (Windows first)
11. Set up `src-tauri/` with sidecar config
12. Bundle/spawn `remedy` process
13. Window management, tray icon (optional)
14. NSIS installer build

### Phase 3 — Polish
15. Tool call cards in message feed
16. Diff rendering (stretch)
17. Dark theme refinement

## Tech Decisions

- **Frontend**: React 19 + Vite + Tailwind CSS — best velocity for chat UIs, excellent Tauri integration docs
- **Markdown**: `react-markdown` + `rehype-highlight` for code blocks
- **Streaming**: Native `fetch` with `ReadableStream` for SSE; no WebSocket needed
- **State**: TanStack Query (React Query) for REST caching; lightweight
- **Sidecar**: `remedy serve` spawned as subprocess; PyInstaller `.exe` as fallback for standalone Windows builds

## Windows Distribution

- **Dev**: `remedy serve` + `pnpm dev` in separate terminals
- **Packaged**: Tauri bundles `remedy` binary via sidecar; NSIS `.exe` installer
- **Config**: Shares `~/.remedy/config.toml` with CLI `remedy`

## Success Criteria (v1)

- [x] Windows `.exe` launches → auto-starts server → opens chat window
- [x] Create session → send message → tokens stream in real-time
- [x] Switch sessions without losing history
- [x] `/new`, `/help`, stop generation work via UI
- [x] Attachments via picker and native drag-and-drop
- [x] In-app signed auto-update (check → install → relaunch)
- [x] No Electron dependency
- [x] Multi-tool ReAct turns keep complete `tool_calls` / tool-result pairing
  (avoids provider HTTP 400 on large reviews)

## Sidecar agent notes

The desktop chat path is `React UI → FastAPI → BasicRuntime` ReAct loop. Tool
batches are executed in parallel waves (`MAX_PARALLEL_TOOLS`) but **every**
assistant tool-call id still receives a tool result message before the next LLM
request. Incomplete pairing is also sanitized by `ensure_tool_call_pairings`
immediately before each provider call.

## One-click auto-update pipeline

User path (Ollama-style):

1. Settings / status bar → **Update & Relaunch** (single click)
2. UI opens full-screen progress and **starts download immediately** (`autoStart`)
3. Rust downloads the NSIS installer from GitHub Releases (trusted hosts only)
4. Validates PE `MZ` header + minimum size (rejects HTML error pages)
5. Kills the Python sidecar so files can be replaced
6. Launches installer with **`/S`** (silent NSIS — not MSI `/PASSIVE`)
7. Detaches installer, exits the app
8. NSIS **`NSIS_HOOK_POSTINSTALL`** runs `Exec "…\Remedy Desktop.exe"` so the app
   relaunches on the new build

Metadata: `https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json`

| Piece | File |
|-------|------|
| Check + download + silent install | `desktop/src-tauri/src/lib.rs` |
| Kill old / relaunch new | `desktop/src-tauri/windows/hooks.nsh` |
| Progress UI | `desktop/src/components/UpdateScreen.tsx` |
| CI `latest.json` + signed assets | `.github/workflows/desktop-release.yml` |

**UAC:** Windows may still show one elevation prompt for the installer; that is
outside the app’s control. After approval, install + relaunch are automatic.

## Build Toolchain

| Tool | Path | Notes |
|------|------|-------|
| Cargo | `~\.cargo\bin\cargo.exe` | Rust stable MSVC; prepend `$env:USERPROFILE\.cargo\bin` if not on PATH |
| Rust | Same as cargo | `rustc` stable |
| Tauri CLI | via `npm run tauri` in `desktop/` | Installed via npm, not globally |

### Build from scratch

```powershell
# 1. Add Rust to PATH for this session
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

# 2. Build Python sidecar (output: desktop/bin/remedy-desktop.exe)
python scripts/build_desktop.py --clean

# 3. Build Tauri app (output: desktop/src-tauri/target/release/bundle/nsis/)
cd desktop
npm run tauri build
```

Version is sourced from `pyproject.toml` — `scripts/sync_version.py` / `build_desktop.py` keep `package.json`, `tauri.conf.json`, and `Cargo.toml` in sync. `src/remedy/__init__.py` reads the package version at runtime.

## Releases & auto-update

CI workflow: [`.github/workflows/desktop-release.yml`](../.github/workflows/desktop-release.yml)

1. Push a tag `vX.Y.Z` on the release branch (or use `workflow_dispatch` with a version).
2. Jobs: build sidecar → build Tauri NSIS with `TAURI_SIGNING_*` secrets → publish GitHub Release + `latest.json`.
3. Desktop checks `https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json`.

### Signing checklist

| Item | Location | Commit? |
|------|----------|---------|
| Public key | `plugins.updater.pubkey` in `tauri.conf.json` | Yes |
| `createUpdaterArtifacts` | `bundle.createUpdaterArtifacts: true` | Yes |
| Private key | `~/.tauri/remedy.key` | **Never** (`.gitignore`) |
| CI private key | GitHub secret `TAURI_SIGNING_PRIVATE_KEY` | Secret only |
| Password | GitHub secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Secret only |

Helper to set secrets from the local key file:

```bash
uv run python scripts/set_tauri_signing_secrets.py
```

Losing the private key breaks trust for already-installed clients until they manually install a build with a new pubkey.
