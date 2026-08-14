# Desktop / Tauri / SPA review — RemedyAI v0.23.2

Reviewed `desktop/src` (React/Vite SPA) and `desktop/src-tauri` (Rust). Did not modify source. Prior bugsweep notes in `docs/_bugsweep_review.md` were re-checked against current code rather than carried forward.

## Summary

The per-session stream job registry, session LLM map, and close-to-tray / sidecar story are in better shape than the last sweep: `CancelledError` now persists a mid-turn assistant row, and `abort_session` keeps the stream claim until the generator `finally` releases it. Remaining defects are real but narrower — Stop still aborts the fetch before `POST /abort` (so interrupt still races the dying generator), Stop’s queue drain can fire on the *newly focused* session, single-instance reclaim `taskkill`s every `app.exe`, and Rust `normalize_url` still misses IPv4-mapped IPv6. App.tsx / `useMessages` remain large enough that the next race will likely land in those two files.

## Issues

### Issue 1 -- Severity: bug
- File: desktop/src/hooks/useMessages.ts:1113
- Description: Focused **Stop** calls `stopStreamJob(sid).finally(() => drainQueue(sessionIdRef.current))`. `sessionIdRef` is the *currently focused* tab, not the session that was stopped. Interrupt send correctly drains `targetId` (`useMessages.ts:1052`); Stop does not. If the user hits Stop on session A and switches to B before `abortSession` returns (30s `apiFetch` timeout worst case), the `finally` drains B’s queue and can start B’s next turn without the user sending. Same footgun if A still has an “after” item: it is dropped on the floor because drain runs against B.
- Suggestion: Capture `const stoppedSid = sid` before the async work and `drainQueue(stoppedSid)`. Do not read `sessionIdRef.current` in that `finally`.
- Status: open

### Issue 2 -- Severity: bug
- File: desktop/src-tauri/src/lib.rs:3646
- Description: When the single-instance mutex exists but `FindWindowW("Remedy Desktop")` fails, the new process runs `taskkill /F /IM` for **both** `Remedy Desktop.exe` **and** `app.exe`. `app.exe` is a generic image name (Tauri debug / other products). This path is not rare: first-launch double-click before the window exists, start-in-tray before the title is set, or a hidden-to-tray window that `FindWindowW` misses. A second shortcut click can kill an unrelated `app.exe`, or kill the live Desktop instance that still owns the sidecar and then spawn a duplicate serve.
- Suggestion: Never `taskkill /IM app.exe`. Reclaim only by this process’s PID recorded next to the mutex, or by the known installed image + window class. If no window is found, fail closed (exit the second launch) rather than broadcasting a kill.
- Status: open

### Issue 3 -- Severity: bug
- File: desktop/src-tauri/src/browser_host.rs:703
- Description: `normalize_url` now blocks dotted `169.254.*`, metadata hostnames, and non-RFC1918 **IPv4**. IPv6 is only “has a dot / is IPv4 / is localhost”. IPv4-mapped IMDS `http://[::ffff:169.254.169.254]/` has `host_str() == "::ffff:169.254.169.254"` — contains a dot, does not parse as `Ipv4Addr`, does not `starts_with("169.254.")`, and is **allowed**. Address-bar Go, `browser_navigate`, and the computer-use poller all share this helper. Python `is_valid_navigate_url` is not applied on this path.
- Suggestion: Parse IPv6 (`Ipv6Addr`), reject link-local / ULA-metadata / IPv4-mapped (`to_ipv4_mapped()` then apply the same IPv4 rules). Keep the hostname blocklist. Add a unit test for `[::ffff:169.254.169.254]`.
- Status: open

### Issue 4 -- Severity: bug
- File: desktop/src/hooks/useMessages.ts:249
- Description: Session switch force-loads history with no generation token. `load()` only cancels if `sessionIdRef` changed (`:255`). A turn that finishes during that load calls `finishOk` → optimistic bubble → `listMessages` (`:690`). Whichever `setMessages` lands last wins. If the in-flight switch `listMessages` (started *before* persist) returns after `finishOk`’s refresh, the feed is replaced with a snapshot that has the user row and no assistant — streaming chrome is already cleared. Repro: background turn on A almost done → switch to A → Stopped/done lands → stale `load()` overwrites. Intermittent; more likely when `listSessionTodos` keeps the first `load()` on the stack after `setMessages` (`:261`).
- Suggestion: Monotonic `loadGenRef` incremented at the start of every `load` / `finishOk` history replace; ignore stale responses. Or skip the switch `load()` when `getStreamJob(sessionId)?.status === 'running'` and rely on reattach paint + `finishOk`.
- Status: open

### Issue 5 -- Severity: suggestion
- File: desktop/src/sessions/streamJobs.ts:306
- Description: `stopStreamJob` still **aborts the fetch first**, then `POST /sessions/{id}/abort`. Server `CancelledError` now persists a row (`src/remedy/interfaces/routes/sessions/stream.py:521`) and the claim stays until `event_stream` `finally` (`stream.py:215`) — the 0.22.3 “durable mid-turn end” hole is **not** still fully open. What remains: no `event: aborted` / `event: done` reaches the client (`messages.ts:329` swallows `AbortError`), so Stop is 100% optimistic. Interrupt (`useMessages.ts:1003` + 40ms `drainQueue`) still POSTs a new stream while the dying generator is persisting; the client 409-retries with *another* abort (`messages.ts:155`) for 80/160/320ms. Slow persist / busy DB can exhaust that budget and surface a 409 as a chat error. Two ReAct loops on one session are harder now (claim held) but not impossible if the claim is released before persist finishes writing order.
- Suggestion: Invert Stop: `POST /abort` first, wait for `event: aborted`/`done` (or a short timeout), *then* abort the fetch. Keep the 409 backoff as a belt. Do not treat fetch-abort as the cancel signal.
- Status: open

### Issue 6 -- Severity: suggestion
- File: desktop/src/utils/browserUrl.ts:16
- Description: SPA `normalizeBrowserUrl` blocks schemes / userinfo / prose but **not** raw public IPs or IMDS. Desktop Go goes through Rust (Issue 3). WebUI does not: `BrowserSlide.tsx:447` sets `loaded` and mounts an iframe (`:1051`) with `src={activeUrl}` and `sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`. Double-click chat links (`MessageFeed.tsx:200`) and the omnibox can point that iframe at `http://169.254.169.254/` or `http://[::ffff:169.254.169.254]/`. Chat hrefs only require `https?:` (`MessageFeed.tsx:170`).
- Suggestion: Share one URL policy (block metadata / non-private raw IPs) in `browserUrl.ts` and Rust. Drop `allow-same-origin` unless a specific site requires it, or keep iframe as “↗ system browser only” in WebUI.
- Status: open

### Issue 7 -- Severity: suggestion
- File: desktop/src-tauri/tauri.conf.json:40
- Description: CSP `connect-src` includes `https: http:` (any host). `capabilities/default.json` grants `shell:allow-open` plus plugin `"open": true` with no scope. `openExternalUrl` scheme-checks before `plugin:shell|open`, but any XSS / compromised renderer can invoke the plugin directly with `file:` / local binaries. `read_dropped_files` (`lib.rs:3295`) reads **any** filesystem path the SPA passes (not only OS-dropped ones) into base64 — same capability set includes `allow-read-dropped-files` (declared in `permissions/app-update.toml`, which is the wrong file). Combined with `withGlobalTauri: true`, this is a large renderer blast radius. Chat is react-markdown (no raw HTML), so this is not a free XSS today; it is still an unsafe permission surface.
- Suggestion: Tighten `connect-src` to loopback + `ipc:` + updater hosts. Scope `shell:allow-open` to `http(s)`. Jail `read_dropped_files` to paths previously emitted by the native drop / picker (or drop the command and only use `take_pending_file_drops`).
- Status: open

### Issue 8 -- Severity: suggestion
- File: desktop/src/hooks/useSessionLlm.ts:218
- Description: While the focused bar provider is `rmb`, an 8s poll adopts the host’s loaded GGUF and `setSessionBind`s the **active** session. `onProviderModelChange` is correctly a no-op while `streaming` (`:260`); this poll is not. RMB is a single loaded slot, so following the host is often right — but a mid-turn GGUF swap (Settings / another tab) rewrites the open session’s bind and the next send/retry without a toast. `remedy:rmb-model-changed` (`:191`) does the same and also `applySessionLlm`s while a stream may still be in flight.
- Suggestion: Skip bind writes while `streaming` or `getStreamJob(activeId)` is running. Apply on turn end. Keep the toast on explicit user switches only.
- Status: open

### Issue 9 -- Severity: suggestion
- File: desktop/src/hooks/useMessages.ts:1164
- Description: `stopAndRetry` `await`s `stopStreamJob`, then calls `stop()`, which fires **another** `stopStreamJob` and a `drainQueue`. Then it `send()`s the retry. `stop()` also commits a second optimistic `[Stopped]` unless `uiCommitted` is already set (it is, if `rawText` was committed in `stop()` — but `stopAndRetry`’s first `stopStreamJob` already completed the job, so `stop()`’s paint/commit path can still run on leftover accum). Queue drain + retry send can reorder: an “after” item may start if `drainQueue` wins the race before `registerStreamJob` for the retry (`send.ts` busy check). Usually `uiCommitted` + running-job guard save this; the double-stop is still a footgun.
- Suggestion: Split “commit Stopped + abort” from “clear chrome”. `stopAndRetry` should abort once, commit once, then `send` without going through `stop()`’s drain.
- Status: open

### Issue 10 -- Severity: nit
- File: desktop/src/components/Composer.tsx:649
- Description: `handleSubmit(streaming ? 'after' : 'after')` is a dead ternary. Ctrl/Cmd+Enter interrupt is handled on the line above; this branch is always `'after'`.
- Suggestion: `handleSubmit('after')`.
- Status: open

### Issue 11 -- Severity: nit
- File: desktop/src/App.tsx:1715
- Description: A second `<SettingsPanel open={false} />` stays mounted under the main shell “so floating settings stay wired.” `SettingsPanel` still runs all hooks (large state, `load` callback, xAI poll teardown) and then `return null` (`SettingsPanel.tsx:757`). The live panel is the rail instance (`App.tsx:1336`). Duplicate instance is wasted work and a future “which panel saved?” trap if someone flips `open`.
- Suggestion: Delete the `open={false}` mount. Rail-only is already the product rule.
- Status: open

### Issue 12 -- Severity: nit
- File: desktop/src/App.tsx:1632
- Description: Composer vision banner gets `llmProvider={llmProvider}` / `llmModel={model}` (floating hook state). Status bar and `handleSend` use `barProvider` / `barModel` / `sessionLlmMap`. After bootstrap (`useAppBootstrap.ts:120` overwrites `llmProvider`/`model` from `conn.active_*`) the composer can advertise a different model than the one that will actually be sent for the focused tab.
- Suggestion: Pass `barProvider` / `barModel` into Composer.
- Status: open

## UX/architecture notes

**What looks solid**

- Per-session `streamJobs` paint buffers + detach/reattach are the right model for multi-tab turns. `completeStreamJob` will not revive a terminal job. Tests cover detach isolation and the Stopped marker.
- Session LLM binds (`useSessionLlm`) no longer clobber an existing map entry from list polls. `handleSend` reads the map for *that* sid. Settings save does not rewrite an open chat’s bind (`App.tsx:1371`, `:1748`).
- Server claim is held until `event_stream` `finally`; `abort_session` is epoch-aware. Desktop 409 retry is a reasonable backup, not the only lock.
- Close-to-tray is forced in `load_desktop_prefs`, `CloseRequested`, and `request_close_main_window`. Quit is tray / `request_quit_app` only. Sidecar spawn prefers live venv in debug, skips stub EXEs, and offers a foreign-serve dialog instead of silently double-binding :7400.
- Token handling: renderer keeps the bearer in module memory only; Settings GET exposes `llm_api_key_set`, not the key. Tauri `get_local_api_token` decrypts DPAPI and rejects sealed envelopes. `openExternalUrl` rejects non-http(s).
- Browser rail bounds / stack-suppress vs overlays is elaborate but intentional (WebView2 HWND z-order). Child embed is a separate webview label; capabilities are `windows: ["main"]`.

**God components (maintainability that already caused bugs)**

- `App.tsx` is still ~1951 lines after extracting bootstrap, overlays, send flow, workspace chrome, and session LLM. It still owns import/export, tray listeners, SSE session sync, rail layout, and a dead Settings mount. The Stop drain / Composer bind mismatches live at this composition layer.
- `useMessages.ts` (~1378) owns load, queue, send, interrupt, Stop, stall, and reattach. `sendTurn` is one closure. Issue 1 and Issue 4 are exactly “too much session identity in one callback.”
- `SettingsPanel.tsx` + `SetupWizard.tsx` remain large form gods; secret fields are React state until save (acceptable) but the dummy mount (Issue 11) multiplies that cost.

**Dual WebUI vs Desktop**

- Same SPA, different load path (Vite HMR vs `find_webui_dir()` / sidecar `REMEDY_WEBUI_DIR`). Agents must `npm run build` **and restart serve** for WebUI parity — documented in `Agents.md`, still easy to miss.
- Desktop always `fetch`es `http://127.0.0.1:7400` unless `VITE_REMEDY_API` was baked at build or `window.__REMEDY_API_ORIGIN__` is injected (`client.ts:16`). Rust sidecar honors `REMEDY_API_PORT`. A non-default port makes the splash look “offline” forever while the sidecar is healthy on another port. Isolated dogfood (`vite.config.ts` `:7410`) is the only in-tree override.
- WebUI bootstrap is loopback HTTP (`/api/auth/local-bootstrap`); Desktop prefers IPC. `http_bootstrap: false` is a silent WebUI-only break.
- Browser rail on WebUI is a sandboxed iframe (no WebView2, no Rust URL jail). Terminal PTY / privacy shield / computer-use host are Tauri-only (`useComputerHost` invokes Rust). Do not assume WebUI “has a browser rail” in the Desktop sense.

**Stop + interrupt + send (state machine as implemented)**

```
Stop:     abort fetch → POST /abort → job=aborted → optimistic [Stopped]
          → drainQueue(sessionIdRef)          // Issue 1
Interrupt: same abort path (awaited) → queue item front → drain in 40ms
New send:  409 → POST /abort again → 80/160/320ms retry
Switch:    detach (do not abort) → reattach paint; load() in parallel  // Issue 4
```

Provider switch mid-stream is blocked on the status bar. Session switch does not abort. Concurrent tabs are allowed. Those three are correct product rules; the bugs are in the edges (drain sid, stale load, abort-first).

**Not re-opened (fixed since `_bugsweep_review.md`)**

- `CancelledError` now `add_chat_message`s before re-raise (`stream.py:521`).
- `abort_session` no longer drops `_stream_claims` (`turn_context.py:364`).
- Rust `normalize_url` now rejects public / link-local **IPv4** and a metadata hostname list (IPv6-mapped still open — Issue 3).
