# Desktop / agent isolation review — RemedyAI v0.24.0

**Date:** 2026-08-14  
**Tree:** `C:\Users\Administrator\Old-Remedy` (`pyproject.toml` version `0.24.0`)  
**Scope:** Stop/session races, agent isolation, ReAct loop globals, computer-use event-loop blocking, messenger cancel, Tauri single-instance, Hugging Face Settings UI (`HfPullPanel` + `desktop/src/api/rmb.ts`).  
**Method:** Re-read current source against the 2026-08-13 claims in `docs/_full_project_review.md` and `docs/_review_desktop.md`. No product code was changed.

---

## Summary

Most of the 2026-08-13 isolation / Stop / messenger / single-instance bugs are **fixed in this tree**. Stop now drains the stopped session id, session-switch `load()` has a generation token, ReAct flags live on a per-turn `ContextVar`, computer-use `run()` is off the asyncio loop, messenger `CancelledError` persists, and single-instance reclaim no longer `taskkill /IM app.exe`. Mid-SSE abort is no longer “between steps only” — `stream_consume.py` races the abort Event against each SSE line.

What remains is narrower. Stop is still abort-fetch-first. A process-global `drainQueue` lock can drop another tab’s queued send. RMB / Hugging Face load still rebinds **whichever chat is focused** (including mid-stream) and rewrites global `llm_provider`. Host Bridge `mkdir -p dest && compile` skips the compile when `dest` already exists. `HfPullPanel` does not resume polling after Settings unmount, and a progress-fetch error re-enables GGUF live-apply while a download is still running.

Settings **Save** still does not steal an open tab’s bind (`App.tsx` gates on `!activeId`). The 0.24 claim is true for that path. RMB / HF is a separate, still-open steal.

---

## Prior issues re-verified

| # | 2026-08-13 claim | Now |
|---|------------------|-----|
| 1 | `useMessages.ts` Stop `finally` drains focused tab | **Fixed.** `stoppedSid` is captured and passed to `drainQueue` (`useMessages.ts:1116–1119`). |
| 2 | Session-switch `load()` has no generation token | **Fixed.** `loadGenRef` incremented at `load()` start and `finishOk` history replace; stale `listMessages` is ignored (`useMessages.ts:113`, `:252`, `:258`, `:694–696`). |
| 3 | `streamJobs.ts` abort-fetch-first | **Still open** (same order). |
| 4 | `lib.rs` `taskkill /F /IM app.exe` | **Fixed.** Reclaim is recorded PID (image-checked) or `/IM "Remedy Desktop.exe"` only (`lib.rs:3649–3755`). |
| 5 | Process-global `_force_tool_choice` / `_turn_tier` / `_action_ir` / `_shadow_strict` | **Fixed for the claimed theft.** `set_turn_force_tool_choice` writes only `TurnReactFlags` on the turn ContextVar (`turn_context.py:504–509`). No `runtime._force_tool_choice =` assignments remain in `src/`. Residual: `runtime._turn_tier_preclassified` is still a process singleton (see Issue 8). |
| 6 | Build protocol consumed before `begin_build_turn` | **Fixed.** Loop calls `begin_build_turn` then appends `build_protocol_block` to **this** turn’s messages (`loop.py:380–390`). `stash_build_protocol` has no writers. Preamble still has a dead `runtime._build_protocol_pending` fallback. |
| 7 | `agent_computer_tools.py` / `executor.py` `time.sleep` on the asyncio loop | **Fixed.** Wrappers use `asyncio.to_thread(ex.run, …)` (`agent_computer_tools.py:12–14`). Sleeps still exist inside `run()`; they no longer freeze sibling SSE. |
| 8 | `executor.py` singleton `_active_session_id` | **Fixed.** Per-thread `threading.local()` stamped for the `run()` call (`executor.py:30–31`, `:93–104`). Host jobs take `session_id` from that TLS. |
| 9 | ReAct abort only between steps, not mid-SSE | **Fixed for SSE/JSON reads.** `consume_llm_http_response` races each line/`json()` against the turn abort Event (`stream_consume.py:38–65`, `:110–118`). Loop still also checks between steps (`loop.py:738–756`). Tool waves still wait for the in-flight tool (see Issue 9). |
| 10 | `gateway/session_bridge.py` `CancelledError` not persisted | **Fixed.** Dedicated `except asyncio.CancelledError` persists the assistant row (or a continue note) before re-raise (`session_bridge.py:290–310`). |

---

## Issues

### Issue 1 -- Severity: suggestion
- File: desktop/src/sessions/streamJobs.ts:306
- Description: `stopStreamJob` still aborts the client `AbortController` first, then `POST /sessions/{id}/abort`. Server persist-on-cancel and claim-until-`finally` are in place, so the old “durable mid-turn end” hole is not back. What remains: Stop is optimistic (`AbortError` swallowed on the fetch), and interrupt/send can `POST` a new stream while the dying generator is still persisting (409 retry budget in `messages.ts`).
- Suggestion: `POST /abort` first; wait for `event: aborted`/`done` (or a short timeout); then abort the fetch. Keep 409 backoff.
- Status: open

### Issue 2 -- Severity: bug
- File: desktop/src/hooks/useMessages.ts:432
- Description: `drainQueue` uses a **process-global** `drainingRef`. If session A’s drain is awaiting `sendTurn`, session B’s Stop/`finishOk` drain hits `if (drainingRef.current) return` and never retries. B’s queued “after” / interrupt item sits until some later drain (another Stop, another finish). Concurrent tabs + queue is exactly the product model this hook claims to support.
- Suggestion: Per-session drain lock, or if global-busy, `queueMicrotask` / `setTimeout(0)` to retry the skipped `forSid`.
- Status: open

### Issue 3 -- Severity: bug
- File: desktop/src/hooks/useSessionLlm.ts:191
- Description: `remedy:rmb-model-changed` always `setSessionBind(activeId, 'rmb', stem)` and `applySessionLlm`s the **currently focused** tab. It does not check `streaming` / `getStreamJob(activeId)`. `HfPullPanel` `onLoaded` and every GGUF `<FormSelect>` fire `emitRmbChatModel` (`sections_localModels.tsx:43–49`, `:546`, `:663–668`). A pull started on Settings while tab A is on OpenAI (or a pull that finishes after the user switched to tab B) rewrites that tab’s bind and can change the next send/retry mid-turn. Backend `apply_rmb_settings(..., use_as_chat_provider: True)` also writes global `config.toml` `llm_provider=rmb` (`hf.py:681–686`, `service.py:3355–3358`).
- Suggestion: Bind only sessions already on `rmb`, or the session that initiated the load. Skip bind writes while that session’s job is `running`. Keep global Settings default for *new* tabs only (the 0.24 Save rule).
- Status: open

### Issue 4 -- Severity: suggestion
- File: desktop/src/hooks/useSessionLlm.ts:217
- Description: While the status-bar provider is `rmb`, an 8s poll adopts the host GGUF stem onto `activeId` with no streaming guard (`:232–235`). Same class of bug as the 2026-08-13 desktop Issue 8; `onProviderModelChange` still no-ops while `streaming` (`:260`), this poll does not.
- Suggestion: Skip the tick when `streaming` or `getStreamJob(activeId)?.status === 'running'`. Apply on turn end.
- Status: open

### Issue 5 -- Severity: bug
- File: desktop/src/components/settings/sections_localModels.tsx:154
- Description: `HfPullPanel` progress poll (`:134–167`) only runs while local `progress.phase` is `downloading`/`loading`. Settings live in the rail slide — leaving Settings unmounts the panel, clears the interval, and drops local `progress`. Re-open starts at `progress=null` / `pulling=false`; a server-side pull continues with no UI and `rmbBusy` reset. A `getHfProgress()` throw sets `onBusy(false)` but leaves `progress` on downloading, so `pulling` stays true **inside** the panel while parent `rmbBusy` is false. Parent GGUF `<FormSelect>`, path blur, profile, and knobs key only off `rmbBusy` (`:502`, `:626`, `:681`) — the user can live-apply another GGUF (restart llama-server) on top of the still-running HF download/load. Server `start_pull` is a single global thread (`hf.py:63–65`, `:650–651`); a second Pull returns `{started:false}` and the UI silently tracks the other job. Load failure is reported as `phase: 'ready'` (`hf.py:698–702`), so the panel calls `onLoaded` and broadcasts a model-changed event for a host that did not load.
- Suggestion: Resume poll on mount from `getHfProgress()`. Keep `onBusy(true)` until a terminal phase. Gate all RMB live-apply on `pulling \|\| rmbBusy`. Do not map load-failure to `ready`. Show Cancel during `loading`. Generation-token search/list if you later allow overlap.
- Status: open

### Issue 6 -- Severity: bug
- File: desktop/src/components/settings/SettingsPanel.tsx:625
- Description: Settings **Save** still writes `llm_provider` / `llm_model` from **form state**. App `onSettingsSaved` correctly refuses to rewrite an open chat bind (`App.tsx:1369–1373`, `:1747–1750`) — the 0.24 steal is fixed on that path. After an HF/GGUF load, the form is not reloaded (`onLoaded` only `refreshRmb` + `onSettingsSaved`), so the Provider fields can still show the previous cloud provider. A later Save writes that stale pair back over `config.toml`, undoing `sync_rmb_chat_identity` / `use_as_chat_provider`.
- Suggestion: After RMB/HF live apply, either `load()` the Settings form or stop sending `llm_provider` on Save unless the user edited Provider. Keep the `!activeId` bar guard.
- Status: open

### Issue 7 -- Severity: bug
- File: src/remedy/execution/host/translate.py:239
- Description: `mkdir -p dest && gcc hello.c -o hello` becomes `if not exist "dest\" mkdir "dest" && gcc …`. In `cmd.exe`, `IF NOT EXIST …` consumes the rest of the line, so when `dest` already exists the `&& gcc` is **skipped**. Same for `mkdir -p a && true` (the unit test only checks rewrite text). This is the common “create build dir then compile” shape local models emit.
- Suggestion: Wrap the `if not exist` in parentheses, or emit `if not exist … mkdir …` and join the next segment with `&` (always run) rather than `&&` attached to the IF. Add a Windows execution test, not only a string assert.
- Status: open

### Issue 8 -- Severity: suggestion
- File: src/remedy/memory/harness/send_policy.py:317
- Description: The original `_force_tool_choice` cross-tab drive is gone. What is still process-global: `runtime._turn_tier_preclassified = pre_tier` with no session key. The next turn’s preamble prefers that attr (`agent_react_preamble.py:328–332`) and does not clear it. Tab A’s send-policy classification can light/heavy-pack Tab B’s next metabolism snapshot. `set_turn_tier` / `set_turn_action_ir` / `set_turn_shadow_strict` still write the runtime singleton when `TurnReactFlags` is missing (`turn_context.py:549–550`, `:568–569`, `:585–586`) — safe inside `begin_turn`, leftover if anything calls them outside a turn.
- Suggestion: Store preclassified tier on the turn flags (or drop the attr and reclassify). Make the remaining setters match `set_turn_force_tool_choice` (ContextVar only).
- Status: open

### Issue 9 -- Severity: suggestion
- File: src/remedy/core/computer/executor.py:402
- Description: Computer-use no longer blocks the event loop. `computer_wait` still `time.sleep`s up to 30s on the worker thread with **no** `_abort_check` (`executor.py:402–406` and the browser-target copy at `:951–955`). A tool wave waits for that sleep (`agent_tool_batch.py:595` only checks abort between waves), so Stop can sit behind a 30s wait even though SSE abort is live.
- Suggestion: Sleep in small slices and return aborted when `_abort_check()` is true (same pattern as type/click).
- Status: open

### Issue 10 -- Severity: suggestion
- File: src/remedy/execution/host/translate.py:362
- Description: Piped `head`/`tail` (`type file \| head -n 5`) is not rewritten — the `if files:` branch is skipped and `head` stays as a POSIX command. When there *is* a file operand, the rewrite uses `sys.executable -c …` (`translate.py:424–435`), which in the packaged sidecar is the frozen Remedy exe, not a CPython that understands `-c`. `resolve_which("python")` already prefers dialect / `PATH`; this path does not.
- Suggestion: Rewrite stdin `head`/`tail` to `more` / PowerShell `Select-Object -First/-Last`, or to dialect `python_cmd`. Never pass the frozen `sys.executable` as `python -c`.
- Status: open

### Issue 11 -- Severity: suggestion
- File: desktop/src/hooks/useMessages.ts:1170
- Description: `stopAndRetry` still `await stopStreamJob(sid)` then calls `stop()`, which fires a second `stopStreamJob` and `drainQueue`. Usually `uiCommitted` + the running-job guard save it; an “after” queue item can still start if drain wins before the retry `registerStreamJob`.
- Suggestion: Abort once, commit once, `send` without `stop()`’s drain.
- Status: open

### Issue 12 -- Severity: nit
- File: desktop/src/App.tsx:1715
- Description: A second `<SettingsPanel open={false} />` is still mounted “so floating settings stay wired.” `if (!open) return null` (`SettingsPanel.tsx:757`) avoids a second `HfPullPanel`, but the dummy instance still runs Settings hooks (load, xAI poll teardown, RMB callback). Rail instance is the live one (`App.tsx:1336`).
- Suggestion: Delete the `open={false}` mount.
- Status: open

---

## Not re-opened (verified fixed)

- Stop drain uses `stoppedSid`, not `sessionIdRef.current`.
- `loadGenRef` on switch `load()` and `finishOk` history replace.
- Single-instance: PID sidecar + `pid_is_remedy_desktop_image`; comment explicitly forbids `/IM app.exe`.
- `TurnReactFlags` allocated in `begin_turn`; unfinished-work `set_turn_force_tool_choice(True)` cannot force a sibling tab’s next POST.
- Build protocol injected after `begin_build_turn` on this turn’s `messages`.
- Computer tools: `asyncio.to_thread`; executor session id is TLS + `turn_session_id`.
- Mid-SSE / mid-JSON abort via `_await_or_abort`.
- Messenger cancel persists an assistant row.
- Settings Save → `onSettingsSaved` does not `setSessionBind` / overwrite the focused tab’s provider (only `!activeId`).

---

## Notes (not defects)

- RMB Settings copy says picking a GGUF “sets chat to RMB” — stealing the **focused** tab is the written product rule. The bugs above are *wrong* tab (switch during pull), *mid-stream* rewrite, and Save clobber — not the existence of a focused-tab adopt.
- One HF pull per process is intentional (`_pull_thread` singleton).
- `cmd.exe` Host Bridge is the designed Windows runner for model-emitted `bash_exec`; PowerShell goes through `pwsh -File`.
