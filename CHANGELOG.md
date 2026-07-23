# Changelog

All notable changes to Remedy (`remedy-ai`) are documented here.

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
