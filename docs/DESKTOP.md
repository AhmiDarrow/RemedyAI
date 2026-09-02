# Remedy Desktop — Primary User Interface

## Overview

**Remedy Desktop** is the recommended way to use Remedy — your personal AI
partner for knowledge, design, code, computer use, and get-it-done work (not a
medical or clinical product). Current package series: **0.48.x** (see root
`CHANGELOG.md`).

### Dev workflow (single build)

**One dev build** on the default ports (`127.0.0.1:7400`, `~/.remedy`, Vite
`localhost:5173`). Run `cd desktop && npm run tauri:dev`.

The dev build runs the **live Python sidecar** (repo `.venv/Scripts/remedy.exe`,
current `src/remedy`), never a stale packaged `remedy-desktop.exe`. If you also
have the installed release running, quit it first (they share port `7400` and
`~/.remedy`).

### Always-ready window (close → tray) — **0.20.0+**

| Chrome | Behavior |
|--------|----------|
| **OS ✕ / Alt+F4** | **Always hide to system tray** — sidecar/API stays up |
| **Tray click / Show** | Restore + focus main window |
| **Tray Quit** | Full process exit (stops local server + Web UI) |

**0.20.0** hardens this as product rule (not a user opt-out). Stale
`close_to_tray=false` in `~/.remedy/desktop.json` or `config.toml` is healed to
**true** on load. Settings documents the behavior as always-on.

### Local model packaging (0.11+)

| Item | Policy |
|------|--------|
| Installer | **Does not** include SmolVLM2 GGUF / multi‑GB weights |
| First run | Setup downloads pinned `smolvlm2-2.2b` into `~/.remedy/vision/` |
| After install | llama-server **auto-starts with Remedy** |
| Offline (optional) | `python scripts/stage_local_bundle.py --from-vision-home` then `REMEDY_LOCAL_BUNDLE=…` |

`desktop/resources/local/` is a **staging area only** (gitignored weights). It is
**not** listed in `tauri.conf.json` `bundle.resources` — so normal NSIS builds stay small.

### Voice runtime (0.30+)

The frozen sidecar has no `pip`, and Chatterbox pulls torch, so voice works
the same way as vision: nothing heavy in the installer, a pinned runtime in
the owner's home.

| Item | Policy |
|------|--------|
| Installer | **Does not** include kokoro-onnx / faster-whisper / onnxruntime / chatterbox / torch (`scripts/build_desktop.py` excludes them explicitly) |
| Runtime | `~/.remedy/voice/runtime/python/` — pinned python-build-standalone CPython 3.12 (`remedy/voice/runtime.py`, sha256-verified, ~70 MB) |
| Packs | `pip install` into that runtime: **voice** (`kokoro-onnx`, `faster-whisper`) on first Download; **hq** (`chatterbox-tts`) when HQ is turned on |
| Inference | `remedy/voice/worker.py` runs inside the runtime (JSON lines over stdin/stdout); `remedy/voice/bridge.py` is the sidecar client. Models (`tts/`, `stt/`, `models/smart-turn/`, `chatterbox/`) stay where they were. |
| Dev | In-process as before. `REMEDY_VOICE_MANAGED=1` forces the Desktop path from a checkout; `REMEDY_VOICE_PYTHON=…` points it at any interpreter (tests use this). |
| Marker | `runtime/runtime.json` — `{ok, python, packs: {voice, hq}}`; `voice_status` reads it instead of importing engines |

The worker imports the bundled `remedy` *source* (`sys._MEIPASS`, from
`--add-data src/remedy`), so the runtime never needs `remedy-ai` installed
and versions cannot drift. Only stdlib-backed modules may be imported on
the worker path (`remedy.voice.*`, `remedy.core.atomic_json`,
`remedy.telephony.narrowband`).

### Native runtime cutover (0.48+)

Installers include two small native components in addition to the Python
sidecar: the Go `remedy-runtime` probe/runtime and the Zig `remedy_core` shared
library (`.dll` on Windows, `.so` on Linux). Python is deliberately retained for
compatibility and AI/ML workers while parity moves over in tested slices.

The default is `REMEDY_NATIVE_RUNTIME=compatibility`. Developers can select
`auto` to use a native slice only when both versioned probes are healthy, or
`native` to request it explicitly. A mismatch or missing artifact is visible in
`/api/ping` as path-free fallback evidence. Liveness polling never starts a
process or loads a library. A native failure is replayed through compatibility
only for an operation that declares itself idempotent; sends, payments, deletes,
and other potentially partial side effects are never silently repeated.


### Skills panel (0.10.30+; HITL + packs in 0.10.44)

The desktop **Skills** side panel lists packs with status chips (active / validated /
discovered / disabled), **hard-won** badges, search, human overrides (**force promote**,
**quarantine**), **Edit MD** (CodeMirror), **Export/Import Pack** (ZIP), and success/fail
feedback. Full skill lifecycle docs: [SKILL_LIFECYCLE.md](SKILL_LIFECYCLE.md).

Agent tools: `skill_activate`, `skill_search`, `skill_run` (progressive disclosure).
Quarantined packs cannot load instructions or run scripts until you clear quarantine /
force-promote.

### Time Travel & usage ticker (0.10.44)

- **⏱ Time travel** (status bar / palette): restore chat + best-effort `file_write` undo
  + checkpoints to a chosen step.
- **Token & cost ticker** (bottom-right, hideable): live run + session tokens and estimated
  API cost (provider usage when available).

### Help wiki + Web UI (0.10.37)

- Offline owner’s manual: **F1** / status bar **Help** (`docs/manual/` + in-app wiki).
- **Switch to Web UI**: hide to tray + open `http://127.0.0.1:7400/` (same SPA as desktop; API serves built assets).
- **WebUI vs desktop dev:** `tauri:dev` uses Vite HMR; WebUI needs `cd desktop && npm run build` then **restart serve** (or sync into staged `webui/`). Prefer `desktop/dist` over stale `target/debug/webui` — see **AGENTS.md** (“Desktop SPA vs WebUI”).
- **✕ always hide-to-tray (0.20.0+)** (server stays up). **Tray → Quit** fully exits and warns that the local server stops (Web UI dies).
- Sync wiki copies: `python scripts/sync_help_manual.py`
- Docs stay aligned with code (CI gate): `python scripts/check_docs.py` — help bodies, versions, slash commands, hotkeys, catalog ids, README test-count claim

### Partner features (0.10.18–0.10.25)

- Chat bubbles: user right / Remedy left (theme tokens); sleek shrink-wrap; user name/initials
- Stick-to-bottom feed (tokens, thinking, tools); **↓** if you scrolled up
- **Tool process**: Min / Med / Full (status bar + Settings; answer always full)
- Prompt history: ↑ / ↓ in the composer
- Title-bar wordmark menu: Settings, About, Updates; session avatars use circuit-R
- **Window chrome:** OS decorations for min / max / close (reliable hit-tests). In-app
  menu strip is React (`TitleBar.tsx`) — logo menu only, no fake window buttons.
  Window drag uses explicit `startDragging()` on the middle strip only — **do not**
  reintroduce whole-bar `data-tauri-drag-region` (Windows WebView2 sticky hit-tests
  break chrome buttons after move/maximize). Logo + controls are always `no-drag`.
- Settings: your name, agent name, persona, project browse, scope, harness, themes, density, accent
- Sessions: auto-title from first prompt; rename; search / pin / tags
- Approvals banner for high-impact shell commands
- Tray: Show, Settings, updates, About, Quit
- ComfyUI skill: from-scratch bootstrap + portable local discovery + generate into chat

## Branding / icons

App icons are generated from `assets/remedy_icon.png` (circuit-R monogram):

```bash
python scripts/setup_branding.py
```

That refreshes `desktop/src-tauri/icons/*` (including multi-size `icon.ico` for
Windows taskbar) and public favicons. **Rebuild the desktop app** after running
the script so the new ICO is embedded in the EXE.

### Windows Defender — known signals and how Remedy addresses them

Remedy is **not** malware. Defender’s **ML** signatures sometimes mislabel
unsigned or newly published desktop apps (especially PyInstaller sidecars).
We treat every known trigger as a product defect and mitigate it in code.

| Defender name | What triggered it | Mitigation in current builds |
|---------------|-------------------|------------------------------|
| `Behavior:Win32/Persistence.A!ml` | Writing **HKCU\…\Run** for “Start with Windows” (0.10.19–0.10.21) | **Never write Run.** Autostart = **Startup folder** `.lnk` only. Launch/install/uninstall **delete** legacy values. |
| Hidden PowerShell + Run key (related) | Launch/Settings polled and scrubbed Run via `powershell -ExecutionPolicy Bypass` | Scrub uses **`winreg` in Rust** (no PowerShell). NSIS uses **`DeleteRegValue`**. PowerShell only for optional `.lnk` create when the user toggles Start with Windows. |
| `Behavior:Win32/Execution.A!ml` | Fresh unsigned **`app.exe`** in `%LOCALAPPDATA%\Remedy Desktop` then spawning the Python sidecar / shell (classic dropper pattern to Defender ML) | **0.23.2+** ships the UI as **`Remedy Desktop.exe`** (`tauri.conf.json` `mainBinaryName`). Keep **Allow on this device** for 0.23.1 if it already fired. Authenticode still the long-term reputation fix. |
| `Trojan:Win32/Wacatac.B!ml` / `Bearfoos.A!ml` | Unsigned **PyInstaller onefile** / freshly written installer EXEs with weak PE identity; also common on **first in-app update** when Defender scans a new `Remedy.Desktop_*_setup.exe` in `%TEMP%` | Sidecar build stamps **version resource** + **icon** (`scripts/build_desktop.py`). Bundle metadata: publisher, copyright, descriptions in `tauri.conf.json` / Cargo.toml. **No UPX.** In-app updates remain **minisign**-verified before install. Full fix for SmartScreen/Defender reputation is **Authenticode** (see [WINDOWS_SIGNING.md](./WINDOWS_SIGNING.md)). After each PE-changing release, maintainers should **submit false-positive** reports to Microsoft WDSI (installer + sidecar). |
| SmartScreen “Unknown publisher” | No **Authenticode** on first browser download | Expected until OV/EV code signing. In-app updates still **minisign**-verified. See [WINDOWS_SIGNING.md](./WINDOWS_SIGNING.md). |

**Autostart policy (Persistence):**
- Enable path: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk`
- Visible under **Settings → Apps → Startup**
- Registry Run entries are **never written**

**If Defender already blocked an older install:**
1. Install the latest release from [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases) only.
2. Windows Security → Virus & threat protection → **Protection history** → **Allow** on device if Remedy was quarantined.
3. Optional (only if you trust that release): exclusions for  
   `%LOCALAPPDATA%\Remedy Desktop\`
4. Confirm no leftover value under  
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` named `RemedyDesktop` / `Remedy Desktop` / `remedy-desktop`.

**Reporting false positives (maintainers):** submit the installer + `remedy-desktop.exe` to Microsoft’s [//www.microsoft.com/wdsi/filesubmission](https://www.microsoft.com/en-us/wdsi/filesubmission) portal after each release that changes PE layout.

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

The desktop app provides a chat interface with streaming tokens,
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

### Steering a running turn

`POST /api/sessions/{id}/steer` `{message}` → `{steered: true|false}`.
`true`: a turn was live; the text was queued (`turn_context.push_nudge`),
persisted as a user message, and the ReAct loop appends it as a user
message at its next step boundary (`_take_nudges` at the top of each step
and again before a plain answer would end the turn; the stream emits a
`progress` event "Taking that in…"). `false`: no turn running — send it
as a normal message. Text only; attachments need a turn of their own.
Grove sends with `mode: 'steer'`; Studio keeps its queue/interrupt modes.

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
| `GET` | `/api/models` | Available LLM models + default (**live** `GET {base}/models` for OpenAI-compatible providers; catalog fallback) |
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

## UI Layout

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

### Installer asset naming (agent + release ops)

| Item | Canonical value |
|------|-----------------|
| Product title (release name) | `Remedy Desktop v{X.Y.Z}` |
| Tag | `v{X.Y.Z}` |
| NSIS file on disk (Tauri default) | `Remedy Desktop_{X.Y.Z}_x64-setup.exe` (space) |
| **GitHub asset name / latest.json URL** | **`Remedy.Desktop_{X.Y.Z}_x64-setup.exe`** (dots for spaces) |
| Metadata asset | `latest.json` (same release) |
| Full installer URL | `https://github.com/AhmiDarrow/RemedyAI/releases/download/v{X.Y.Z}/Remedy.Desktop_{X.Y.Z}_x64-setup.exe` |

**Rules (do not break auto-update):**

1. Never publish `Remedy_Desktop_*` (underscore between product words) — only `Remedy.Desktop_*`.
2. CI renames spaces → dots before upload (`.github/workflows/desktop-release.yml`).
3. `latest.json` → `platforms.windows-x86_64.url` must equal the asset’s
   `browser_download_url` **exactly** (signature is bound to that file).
4. If a release asset is misnamed, the **easy fix** is rename the GitHub Release
   asset to `Remedy.Desktop_{ver}_x64-setup.exe` (and ensure `latest.json` URL
   matches). Prefer fixing the asset over disabling the URL match check.
5. In-app install **re-reads** signed `latest.json` at download time so a stale
   UI-held URL cannot fail after a multi-MB pull.

| Piece | File |
|-------|------|
| Check + download + silent install | `desktop/src-tauri/src/lib.rs` |
| Kill old / relaunch new | `desktop/src-tauri/windows/hooks.nsh` |
| Progress UI | `desktop/src/components/UpdateScreen.tsx` |
| CI `latest.json` + signed assets | `.github/workflows/desktop-release.yml` |
| Version / URL stamp | `scripts/sync_version.py` → `scripts/latest.json` |

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

Version is sourced from `pyproject.toml` — `scripts/sync_version.py` / `build_desktop.py` keep `package.json`, `package-lock.json`, `tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, and `scripts/latest.json` in sync (installer URL uses `Remedy.Desktop_*`). Help chapters: `python scripts/sync_help_manual.py`. `src/remedy/__init__.py` reads the package version at runtime.

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

### Authenticode / SmartScreen (first install)

minisign only covers **in-app updates**. The first browser download still needs
**Authenticode** code signing to avoid “Unknown publisher” / SmartScreen.

See **[WINDOWS_SIGNING.md](./WINDOWS_SIGNING.md)** for certificate types (OV/EV),
`signtool` examples, and where to plug signing into CI.

### Uninstall data options

Interactive uninstall shows checkboxes:

| Option | Removes |
|--------|---------|
| **Configuration** | `~\.remedy\config.toml`, `desktop.json`, `auth\`, … |
| **Skills** | `~\.remedy\skills` |
| **Full wipe** | Entire `~\.remedy` + app leftovers (reinstall is clean) |

Leave all unchecked to keep user data. Silent auto-update uninstalls (`/UPDATE`)
**never** wipe user data.
