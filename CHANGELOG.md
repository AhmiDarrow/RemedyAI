# Changelog

All notable changes to Remedy (`remedy-ai`) are documented here.

## [0.10.37] — 2026-07-24

### Feature: Grok-style diff colors in chat

- Unified diffs in chat code fences (`diff`/`patch` or auto-detected) show **red removals** and **green additions**, with muted meta/hunk lines.
- Tool process (Proc) args/results use the same highlighting when content looks like a patch.

### Feature: offline Help wiki + technical owner's manual

- **`docs/manual/`** — full owner chapters (install → troubleshooting → CLI/API).
- **In-app Help wiki** (`F1` / `Ctrl+/` / status bar **Help** / logo menu): searchable TOC, markdown articles, in-wiki navigation, Esc to close.
- Product glue: Settings deep-links, Skills panel guide link, error-screen Troubleshooting, About → Help, command palette entries, `/help` points to the wiki.
- **Settings → About: Report an issue on GitHub** (pre-fills version in the issue template); Help footer link too.
- Vitest coverage for the help catalog.

### Pre-push polish (same version)

- Version surfaces aligned (`latest.json`, package-lock, Cargo.lock); installer URL uses `Remedy.Desktop_*`; stale minisign signatures cleared on version bump.
- `scripts/sync_help_manual.py` keeps docs/manual ↔ desktop help articles in sync.
- Setup finish copy: F1/Ctrl+/ open Help wiki; What’s new splits 0.10.36 vs 0.10.37 correctly.
- Settings xAI sign-in re-bootstraps local API token and **persists provider=xAI** on connect; Help report-issue prefills version.
- CLI wizard + `mark_setup_completed` use safe TOML write (scalars before tables; scrub secrets).
- `server-ready` / `server-error` / Retry / Open data folder use official Tauri bridge helpers.
- Hotkeys wired from `hotkeys.ts` SSOT; GET `/settings` no longer rewrites config mid first-run.
- OAuth poll status switches active provider to xAI so chat uses new credentials immediately.
- Tests: API `_write_config` order, xAI OAuth host lock, reportIssue + formatApiErrorBody.

## [0.10.36] — 2026-07-24

### Fix: first-run setup save + xAI OAuth + corrupt config.toml

- **Root cause**: settings writer put root keys *after* TOML `[table]` sections.
  Those keys became part of the last table and could duplicate (`Cannot overwrite a value`),
  so `load_config` returned `{}`, setup looked incomplete, and finish save failed.
- Config writer now always emits **all root scalars first, then tables** (API + `mark_setup_completed`).
- Corrupt / unreadable `config.toml` forces first-run wizard again.
- Setup finish and xAI sign-in re-bootstrap the local API token and surface the real API error
  (no more opaque “Failed to save settings. Is the server running?”).
- First-run: if settings cannot load yet, **Setup opens automatically**; **Open setup** warms auth first.
- `apiFetch`: clearer network/timeout/401 messages; token bootstrap retries.

## [0.10.35] — 2026-07-24

### Fix: first-run after full wipe + uninstall UI

- Desktop sidecar always passes **`--skip-setup`** so the CLI wizard cannot block the API.
- Fresh home gets a default `config.toml` with `setup_completed = false`; **Setup Wizard** runs once the server is up.
- Startup loads **auth + settings first**, then models; wizard no longer depends on models succeeding.
- Token bootstrap retries on 401; splash pre-warms token; longer health wait for skill seed.
- Error screen: **Open setup** + clearer first-install guidance.
- Uninstall options dialog: system font / visual styles / ASCII labels (no mojibake).

## [0.10.34] — 2026-07-24

### CI / release hygiene

- Ruff config tuned so CI lint is green (E501/noise ignores; real F/N issues fixed).
- Duplicate test renamed; signing secrets confirmed for signed Desktop Release.

## [0.10.33] — 2026-07-24

### Security, tests, and performance (Phases A–C)

**Phase A — Trust & safety**
- Local API auth **on by default** (`~/.remedy/auth/local_api_token`); desktop loads Bearer automatically; disable with `REMEDY_API_AUTH=0`.
- Zip Slip protection on skill pack import; **quarantine blocks `skill_run`**.
- Secret store: never grant Everyone ACL; xAI credentials DPAPI-encrypted on Windows.
- Updater: only `AhmiDarrow/RemedyAI` release URLs + known GitHub asset CDNs.
- Default approval (ask mode) for `bash_exec`, `file_write`, `skill_run`.
- Tool subprocess env scrubbed of secrets; webhooks require auth when API key set.

**Phase B — Tests**
- New: `test_api_auth`, `test_zip_import_security`, `test_skill_tools`, `test_skills_api`, `test_session_stream`, `test_updater_api`, `test_secret_acl_no_everyone`.
- Desktop: vitest + `sanitizeChat` unit test.

**Phase C — Scale & polish**
- Tiered context caps (tool 64k / file 128k / history 1.5M); `REMEDY_FULL_CONTEXT=1` for legacy unlimited.
- Strong auto-compress when harness fill is high; skill body inject cap 24k.
- Fixed skill catalog ranking (workspace-aware, no discard); context/skill metrics.
- MessageFeed windowing (last 80 messages + “show earlier”).

## [0.10.32] — 2026-07-24

### Fix: interactive installer no longer launches before finish page

- NSIS POSTINSTALL only auto-starts Remedy for **silent / passive / update** installs.
- Interactive installs wait for the finish page (“desktop shortcut” + “Run Remedy”).
- In-app auto-update passes `/UPDATE` so update-mode hooks stay correct.

## [0.10.31] — 2026-07-24

### Fix: uninstall no longer aborts when options dialog fails

- NSIS PREUNINSTALL only **Abort**s on intentional Cancel (exit code 1).
- PowerShell/WinForms errors (exit 2+) keep user data and **still remove the app**.
- Default choices file written before the dialog so wipe never hard-fails.
- Pure-ASCII options script; safer full-wipe (no live install-dir delete mid-run).
- Logs: `%TEMP%\RemedyDesktop-UninstallOptions.log`, `…UninstallWipe.log`.

## [0.10.30] — 2026-07-24

### Skills system (unique strength)

Progressive disclosure, closed-loop learning, ranking, and governance for the
skill library — see `docs/SKILL_LIFECYCLE.md`.

- **No force-ACTIVE on discover**: curated bundled skills stay ready; auto-generated
  and quarantined skills keep probation status from frontmatter.
- **Progressive disclosure**: ranked catalog in context; tools `skill_activate`,
  `skill_run`, `skill_search` load full bodies / scripts on demand.
- **Post-turn auto-learn**: multi-step successful tool runs distill into probation
  skills; activations and script runs feed `record_skill_feedback` + promote/demote.
- **Durable stats**: `~/.remedy/skill_stats.json` so lifecycle survives restarts.
- **Ranking**: `match_skills` by status, description, tags, effort, success rate,
  workspace hints.
- **Merge + lineage**: same-name traces merge recovery notes instead of duplicating;
  honest `lifecycle_confidence` in learning history.
- **Trigger-oriented descriptions** + failure protocol on generated skills.
- **Pack export/import**: ZIP packs; imports land in **quarantine** until trusted.
- **API**: richer `SkillInfo`, `GET/POST /api/skills…` status, feedback, export, import.
- **Desktop Skills panel v2**: status chips, hard-won badge, search, activate/disable/
  trust, success/fail feedback.
- **Effort-weighted lifecycle** (from 0.10.29 tree): hard-won skills resist demote/prune.

## [0.10.29] — 2026-07-24

### Fix: auto-update install + relaunch

- Detach update PowerShell with `cmd /c start` + `CREATE_BREAKAWAY_FROM_JOB` so `app.exit()` no longer kills the installer script mid-flight.
- Upgrade in place via NSIS `/D=<current install dir>`; discover binaries under both `%LOCALAPPDATA%\Programs\Remedy Desktop` and `%LOCALAPPDATA%\Remedy Desktop`.
- Prefer relaunching a binary whose mtime advanced (detect real replace); log to `%TEMP%\RemedyDesktop-Update.log`.
- POSTINSTALL relaunch via `cmd /c start` so the new app survives NSIS exit.
## [0.10.28] — 2026-07-24

### Fix: DeepSeek / long turns cut off mid-answer

- SSE idle timeout **120s → 900s** (DeepSeek thinking pauses no longer kill the stream).
- Never soft-empty after tools/thinking: promote `reasoning_content` to the answer; retry synthesis up to 8×.
- DeepSeek `max_tokens` uses API-legal caps (chat 8k / reasoner 64k) so oversized 128k requests stop 400ing the turn; auto-continue on `finish_reason=length`.
- Final-answer rounds stream live; ReAct budget **256** steps; length continuations effectively unlimited.
- Last synthesis asks for a **complete** answer (not a short stub).

## [0.10.27] — 2026-07-24

### Fix: desktop update check (tray + Settings)

- Tray **Check for updates** now opens Settings and runs a real check (no longer only focuses the chat composer).
- Compare against the **desktop shell** version, not only the Python sidecar (prevented 0.10.25 EXE from seeing 0.10.26 when the sidecar was already newer).
- Merge Tauri + API update sources; always show **This app** vs **Latest release**.
- Cache-bust GitHub `latest.json`; `/api/updates/check?current=` for shell version.

## [0.10.26] — 2026-07-24

### Agent headroom (no cut-off answers / thinking / tools)

- **Provider `max_tokens`**: always **128k** completion budget — never throttled by thinking level or tool vs answer.
- **No soft-trim** of history answers, tool results, file reads, bash stdout/stderr (OOM safety only at 50M chars).
- **Harness prune**: dedupe only by default — does **not** shorten tool/assistant bodies.
- **ReAct**: up to **128** steps; removed early force-answer at step 8; **64** length auto-continues.
- **History**: 2000 messages / large char budget; drop oldest turns instead of slicing mid-message.
- **Thinking default**: **high**; nudges say finish fully, never truncate.
- **UI**: full answers (no collapse); tall thinking panel with full text.
- **Sessions**: export/import as plain-text `.txt` (round-trip) via API + desktop.

### Session export / import

- `GET /api/sessions/{id}/export?format=txt|md` — default plain-text export.
- `POST /api/sessions/import` — create session from `.txt` / legacy `.md` / freeform.
- Desktop: Sidebar Import/Export, command palette, `/export`, `/import-session`.

## [0.10.25] — 2026-07-24

### Partner desktop UX polish

- **Tool process** modes: **Off** (minimal) · **Medium** (labels + short results) · **Full** (complete raw args/stdout). Settings + status-bar **Proc** cycle. Process log stays under the message, collapsed after the turn.
- **Stick-to-bottom** chat feed for tokens, thinking, tools, and full process dumps; detach when user scrolls up; **↓** resumes follow. Process panel has the same rule.
- **Chat**: sleek shrink-wrap bubbles; user initials/name; icon-only copy/edit; image lightbox; progress bar for tools/jobs.
- **Branding**: title-bar wordmark (logo menu: Settings, About, Updates); session avatars use circuit-R icon.
- **You & Agent**: `user_name` (what Remedy calls you) before agent name; first-run name prompt; profile sync.
- **Sessions**: auto-title from first prompt; double-click / ✎ rename; search, pin, tags.
- **Tray**: Show, Settings, Check for updates, About, Quit.
- **Themes**: Neutral Dark; density cozy/compact; custom accent; accurate theme swatches; smoother switches.
- **ComfyUI / local discover**: portable discovery, image embed path, tool progress SSE.
- **Auth / keys**: per-provider secret store; DSML strip + pseudo-tool recovery; thinking stream to UI.

## [0.10.24] — 2026-07-24

### xAI OAuth in frozen desktop (hard fix)

- Device OAuth **must** use `https://auth.x.ai` (never `accounts.x.ai` → 307 `/sign-in`).
- Refuse wrong host; no redirect following for device/token POSTs.
- PyInstaller builds force `--paths src` + PYTHONPATH so site-packages cannot pin old OAuth.
- Sidecar start kills anything on :7400 (prevents dual stale servers).
- Diagnostics: `GET /api/auth/xai/oauth-meta` shows `oauth_build` / device URL.

## [0.10.23] — 2026-07-24

### Desktop release rebuild

- Rebuild sidecar + installer so **xAI OAuth (`auth.x.ai`)** is in the frozen
  desktop package (0.10.22 source fix was easy to miss if an older sidecar stayed installed).
- Includes Defender Persistence.A!ml fix (Startup folder, no HKCU Run).

## [0.10.22] — 2026-07-24

### xAI OAuth 307 fix

- Device-code + token endpoints now use **`https://auth.x.ai`** (was
  `accounts.x.ai`, which returns **307** to `/sign-in?redirect=…` and broke
  “Sign in with xAI”).
- Verification URLs still open on `accounts.x.ai` (as returned by xAI).

### Windows Defender Persistence.A!ml (critical)

- **Stop writing HKCU Run** for “Start with Windows” (triggered `Behavior:Win32/Persistence.A!ml`).
- Autostart now uses a **Startup folder** `.lnk` only (Settings → Apps → Startup).
- On launch / toggle / uninstall: **scrub legacy Run keys** (`RemedyDesktop`, etc.).
- Installer PREUNINSTALL removes Startup shortcut + Run leftovers.

## [0.10.21] — 2026-07-23

### Final partner phase (goals · approve · knowledge)

- **Goals loop**: tools `goal_add` / `goal_list` / `goal_complete` / `goal_verify`; slash `/goal`, `/goals`.
- **Approvals**: high-impact bash patterns require explicit approve; API + `/approve` `/deny`; desktop **ApprovalBanner**.
- **Knowledge packs**: import `.md`/`.txt` folders via `POST /api/memory/import` and `/import <path>`.
- **Partner status**: `GET /api/partner/status` + status-bar chip (approvals, goals, harness, scope).

## [0.10.20] — 2026-07-23

### Remaining phases + prompt history

- **Composer ↑ / ↓**: shell-style previous/next prompt history (localStorage, up to 80 entries).
- **Always ready runtime**: close-to-tray (hide, keep sidecar), start-in-tray, tray menu Show/Quit, left-click tray to show.
- **Desktop prefs** file `~/.remedy/desktop.json` + Tauri commands.
- **Setup finish**: optional “Keep Remedy ready” + ↑ tip.
- **Handoff** includes Memory Harness Session Brief when present.

## [0.10.19] — 2026-07-23

### Partner plan (remaining phases)

- **Access scope**: `project` | `home` | `full` multi-root path resolution; Settings control; agent hot-reload.
- **Always ready**: Start with Windows (HKCU Run), start-in-tray / close-to-tray prefs in Settings + config.
- **Memory Harness**: auto compress nudges by context fill; artifact tracking on file tools; Settings mode.
- **Companion skills**: `remember-me`, `design-critique`, `personal-briefing`, `write-with-user`, `decision-journal`.
- Slash already: `/compact`, `/harness`, `/remember`, `/whoami`.

## [0.10.18] — 2026-07-23

### Partner vision (Phase A foundation)

- **System identity**: partner framing (knowledge, design, code, PC tasks when allowed); medical disclaimer retained.
- **Desktop chat**: user messages on the **right**, Remedy on the **left**, themed bubble tokens for all palettes.
- **Settings**: persona + agent name; project path **input + Browse**; save reports **Remedy reloaded** / project loaded.
- **Native folder picker** (`pick_folder` Tauri command) for project workspace.
- **Memory Harness (L0–L2)**: mechanical send-view prune; Session Brief; `compress_context` tool; real `/compact`, `/harness`, `/remember`, `/whoami`; profile injection.
- Empty chat copy: “Your partner is ready.”

### Branding / taskbar icon

- Multi-size `icon.ico` (16–256) from circuit-R monogram via `scripts/setup_branding.py`.
- Runtime `set_icon` on main window so taskbar matches tray (not stale medical PE cache).
- Docs: Windows icon-cache clear steps in `docs/DESKTOP.md`.

## [0.10.17] — 2026-07-23

### Branding (not medical)

- Clarify Remedy is a **software coding agent** for projects/code — not medical
  or clinical software (README, pyproject, system prompt, desktop setup copy).
- Replace caduceus / healing brand prompts and splash/logo assets with tech
  wordmark + circuit monogram (no medical symbols).

## [0.10.16] — 2026-07-23

### Fixed

- **Splash hang on "Ready"**: parent re-renders with inline `onReady` restarted the
  health-poll effect mid-handoff; handoff now uses stable callback refs and a
  single mount lifecycle.
- **White splash flash**: boot splash and React splash force a dark background
  (`#0a0a1a`) regardless of system light theme.
- **Auto-update reliability**: longer unlock delay, PowerShell-scheduled silent
  NSIS (`/S /NCRC`) with post-install relaunch fallback; clearer manual URL on
  failure. Release workflow renames installers to space-free asset names so
  `latest.json` URLs match GitHub assets.

## [0.10.15] — 2026-07-23

### xAI OAuth + API key (OpenCode-style dual auth)

- First-class **xAI (Grok)** provider with `https://api.x.ai/v1`
- **Sign in with xAI** device-code OAuth (desktop Settings + Setup wizard)
- Secondary **console API key** path (`xai-…` / `XAI_API_KEY`)
- Tokens stored in `~/.remedy/auth/xai.json`; refresh on expiry / HTTP 401
- CLI: `remedy auth login|logout|status|apikey xai`
- Env bootstrap: `XAI_API_KEY` preselects xAI on clean/default config

### Providers & self-setup

- Catalog: **Groq**, **Mistral**, plus OpenAI / Anthropic / Google / DeepSeek / OpenRouter / Ollama
- `GET /api/providers` is the desktop source of truth (auth modes, models, advanced flag)
- Known brands hide Base URL; **Custom** lives under Advanced
- Ollama auto-detect (`GET /api/providers/ollama/detect`) with setup-wizard hint
- Desktop opens OAuth verification via Tauri shell (fallback `window.open`)

### API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/providers` | Provider catalog |
| `GET` | `/api/providers/ollama/detect` | Local Ollama probe |
| `GET` | `/api/auth/xai` | xAI auth status |
| `POST` | `/api/auth/xai/login` | Start device-code OAuth |
| `GET` | `/api/auth/xai/login/status` | Poll OAuth session |
| `POST` | `/api/auth/xai/apikey` | Save console API key |
| `DELETE` | `/api/auth/xai` | Sign out / clear tokens |

## [0.10.14] — 2026-07-23

### Desktop polish

- Splash holds **at least 3 seconds** (and longer if server still starting); fade-out handoff
- Kill white flash: themed HTML boot splash + early background
- Theme default **System** (follow OS light/dark); improved reading contrast on all themes
- Hotkey registry + **Settings → Help & shortcuts**; `/help` includes keyboard shortcuts
- Empty chat and setup finish tip Shift+Enter / Ctrl+/

## [0.10.13]
 — 2026-07-23

### Fixed (remaining review backlog)

- Metrics registry/counters/histograms are actually thread-safe (locks).
- FTS MATCH failures log at debug before LIKE fallback.
- TOML writer omits `None` keys instead of writing empty strings.
- SSE stream idle timeout (120s) ends stuck keep-alive rounds.
- Sandbox workdir/allowed_paths compare after consistent resolve.
- Learning trace dict builder validates/aliases tool keys more safely.

## [0.10.12]
 — 2026-07-23

### Fixed (review + stop-the-agent failures)

- **DeepSeek HTTP 400** `reasoning_content must be passed back`: assistant tool
  turns now include `reasoning_content` from the stream; repair+retry if missing.
- **API failures no longer abort the whole turn**: soft-recover up to 3 times,
  force a final answer from tool context instead of stopping cold.
- Stream exceptions end with a recoverable user message (session intact).
- **CLI `remedy tool run`**: uses BasicRuntime workspace-jailed tools (no bypass).
- **Security**: Windows dangerous commands (reg, takeown, icacls, …); Windows
  recursive del/rmdir patterns; stop flagging bare `2>/dev/null`.
- **SecurityError** tool results use SECURITY_BLOCKED (clearer than generic exception).
- Larger history/context (48k char budget, more steps/tokens) for long project reviews.
- Workspace jail unit tests + reasoning_content tests.

## [0.10.11]
 — 2026-07-23

### Fixed

- **remedy-desktop.exe stays in Task Manager after close**: Windows does not kill
  child processes when the UI exits, and cleanup only ran on window Destroyed.
  Now tree-kills the sidecar PID (`taskkill /T`), force-stops leftover
  remedy-desktop images / :7400 listeners, and runs shutdown on CloseRequested,
  Destroyed, ExitRequested, and Exit.

## [0.10.10] — 2026-07-23

### Fixed

- **DeepSeek (and other OpenAI-compatible providers) stream crash**: agent only
  treated `provider_name == openai` as SSE, so DeepSeek responses
  (`text/event-stream`) were read with `resp.json()` and failed with
  unexpected mimetype. Now all OpenAI-compatible adapters use SSE streaming.

## [0.10.9] — 2026-07-23

### Fixed

- Auto-update aborted with **Cant write remedy-desktop.exe**: installer ran while the
  sidecar/main process still held file locks. Now force-kills sidecar processes,
  schedules silent install (~2s) after app exit, and NSIS PREINSTALL retries kills
  + best-effort delete of locked binaries.

## [0.10.8] — 2026-07-23

### Fixed

- CI desktop build: TypeScript unused variable in useUpdateChecker failed tsc -b (blocked 0.10.5-0.10.7 installers).

## [0.10.7] — 2026-07-23

### Fixed (one-click update pipeline)

- **Silent install**: used MSI-style `/PASSIVE` which NSIS ignores → multi-step
  wizard. Now launches the installer with **`/S`** (true silent NSIS).
- **Relaunch**: NSIS hooks only killed processes; no POSTINSTALL launch. Added
  `NSIS_HOOK_POSTINSTALL` to `Exec` `Remedy Desktop.exe` after install.
- **One click**: Update screen required a second “Update & Relaunch” press. It
  now **auto-starts** download/install when opened.
- **Detached installer**: spawn with `DETACHED_PROCESS` so install survives app exit.
- **Download hardening**: 10-minute timeout, reject HTML content-types, validate
  PE `MZ` header + min size, refuse update-available without installer URL.
- **Concurrency**: block double-start of in-flight updates.

## [0.10.6] — 2026-07-23

### Fixed

- **About showed Version v0.9.0** while the updater reported 0.10.x — `GET /api/settings`
  crashed with `NameError: name 'version' is not defined` (should use
  `_remedy_version`). Settings never loaded, so the UI fell back to the hard-coded
  `0.9.0` placeholder.
- Same bug on `/api/updates/check` (`current = version`).
- urllib call used `_urllib.request.urlopen` after `import urllib.request as _urllib`
  (AttributeError); corrected to `_urllib.urlopen`.
- About panel prefers the desktop shell version from the update checker when present.

## [0.10.5] — 2026-07-23

### Fixed

- **Check for Updates no longer looks like a no-op** — errors were swallowed and
  the Settings panel only rendered status when `updateInfo` was set, so failed
  checks left a blank area after the button.
- Desktop update fetch tries **all** metadata sources (no longer aborts after the
  first URL error), uses a **15s timeout**, and runs off the UI thread.
- Frontend always surfaces current/latest/up-to-date/error after a check; falls
  back to `/api/updates/check` when the Tauri path reports an error.
- Python `/api/updates/check` also tries GitHub API when `latest.json` fails and
  returns combined error strings instead of silent desktop failures.

## [0.10.4] — 2026-07-23

### Fixed

- **ReAct tool-call pairing** — OpenAI-compatible APIs require every assistant
  `tool_calls[].id` to be followed by a matching `role=tool` message. Large
  multi-tool turns (e.g. “review project”) could previously emit fewer tool
  results than tool calls when:
  - parallel execution hit `MAX_PARALLEL_TOOLS` and dropped the remainder,
  - fingerprint dedupe collapsed identical calls to a single result,
  - a tool raised and the error path used a random `tool_call_id`.
- Missing or empty streaming tool-call `id`s are normalized before the next
  provider request.
- Defense-in-depth: `ensure_tool_call_pairings()` sanitizes the message list
  before every LLM request so incomplete pairings cannot ship.

### Tests

- Added `tests/test_tool_call_pairing.py` for normalize / sanitize / parallel
  cap / dedupe / exception id pairing.

## [0.10.3] — 2026-07-23

### Added

- Agent recovery contract with suggestive tool errors and one recovery nudge.
- Stream-path chat latency metrics; expanded mypy surface.
- Themed custom title bar matching app theme.

### Fixed

- Long LLM streams no longer cut off mid-answer (`finish_reason=length` auto-continue).
- Restore full original prompt in composer on Edit.
- Enable `createUpdaterArtifacts` for signed auto-updates.
