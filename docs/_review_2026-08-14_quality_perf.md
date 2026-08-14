# Quality / performance review

Reviewed local tree `C:\Users\Administrator\Old-Remedy` (product **v0.24.0**, `pyproject.toml` version `0.24.0`). Did not modify product source. Did not re-run the suite or time endpoints in this pass; timings below are from call-graph analysis plus the live-log numbers supplied by the caller.

## Summary

`GET /api/rmb/status` is not a cheap health check. Each call (desktop every 8s while the chat provider is RMB; watchdog independently probes HTTP every 8s) can run **two to four** llama-server HTTP probes, each trying **three URLs** at **0.9s** timeout, then run **`probe_gpus()` twice** (nvidia-smi + PowerShell CIM, no TTL), scan `~/Downloads` for `*.gguf`, and — when the host is ready — **rewrite `config.toml`**. That matches ~1650ms when llama-server is mid-load or nvidia-smi is slow under VRAM pressure.

`GET /api/vision/status` is the full (not light) status path: `system_health` → uncached `probe_gpus()` on a 30s TTL. ~520ms is one GPU/CIM snapshot, not a mystery FS walk.

HF pull (`runtime/rmb/hf.py`) is new and mostly well-shaped (host allowlist, Range resume, tests for parse/search/list/resume). Real holes: dest flatten+skip, cancel cleared inside `download_gguf`, no start lock, cancel ignored while `apply_rmb_settings` loads.

Prior 0.23.2 quality review is **partly stale**: e2e home isolation, vacuous Linux `return`s, Windows CI jail/CUA list, and `Users/`/`~/` gitignore are fixed. mypy still skips the ReAct loop and all HTTP routes. CHANGELOG `[Unreleased]` is no longer empty.

## Prior issues re-verified

Checked against current source (not the 0.23.2 review text).

| Prior claim | Now |
|-------------|-----|
| `tests/test_e2e_simple_c_rmb.py` writes real `~/.remedy` | **Fixed.** `home_dir` / `memory_db_path` are `tmp_path / "remedy_home"` (`:67–70`). Residual: still writes repo `_e2e_simple_c_pytest/`; `@pytest.mark.skipif(not _rmb_ready())` can call `wait_rmb_ready(timeout_s=30)` at **collection**; `addopts` still does not exclude `-m live`. |
| `test_print_window_foreground` writes `~/.remedy/computer/shots` | **Still open.** `tests/test_computer_use.py:1296`. |
| `test_vision.py` `BasicRuntime(..., home_dir=Path.home()/".remedy")` | **Still open.** `tests/test_vision.py:635`. |
| Computer-use tests `return` on Linux (vacuous pass) | **Fixed.** Those cases now `pytest.skip("Windows only")` (`:1100`, `:1119`, `:1226`, `:1251`, `:1286`). |
| Windows CI omits jail / CUA files | **Fixed.** `.github/workflows/ci.yml:70–75` now runs `test_project_write_jail.py`, `test_shell_jail_fuzz.py`, `test_win_paths.py`, `test_uninstall_wipe_paths.py`, `test_computer_use.py`, `test_host_bridge.py`. |
| CHANGELOG `[Unreleased]` empty / tree mislabeled 0.23.2 | **Stale.** Product is **0.24.0** (`pyproject.toml:6`). `[Unreleased]` has 0.24.x notes including an RMB-status probe claim (see Issue 2). |
| mypy excludes ReAct + routes | **Still open.** `pyproject.toml:133–153`; `interfaces/routes/` still not in `files`. |
| `Users/` and `~/` not gitignored | **Fixed.** `.gitignore:70–72`. Directories still exist in the working tree; they will not stage. |
| `conftest.py` only sets `REMEDY_API_AUTH=0` | **Still open.** `tests/conftest.py:9`. |
| `python -c print(1)` jail row accepts either outcome | **Still open.** `tests/test_shell_jail_fuzz.py:98–104`. |
| `test_computer_capture_api` treats 404 as success | **Fixed.** Now asserts `status_code == 200` (`:1133–1141`). |
| `live_settings_matrix.py` soak `or True` | **Still open.** `scripts/live_settings_matrix.py:614`. |
| Desktop CI: no cargo test / no Windows desktop job; release does not `needs: test` | **Still open.** `ci.yml` desktop job is Ubuntu npm only; `desktop-release.yml` `needs: build-sidecar` only. |
| `test_no_gguf_committed_under_resources_local` silent `return` | **Still open.** `tests/test_packaging_policy.py:36–37`. |
| Process-global `_force_tool_choice` steals the next tab | **Mostly fixed.** `begin_turn` installs `TurnReactFlags` (`turn_context.py:254`); `set_turn_force_tool_choice` no longer writes the runtime (`:504–509`). Fallback still reads `runtime._force_tool_choice` if flags are missing (`:499–500`). |
| Computer `time.sleep` on the asyncio loop | **Fixed** for tool wrappers: `agent_computer_tools.py:12–14` uses `asyncio.to_thread`. `executor.py` still sleeps (correct, off-loop). |
| Messenger cancel drops assistant row | **Fixed.** `session_bridge.py:290–305` persists on `CancelledError`. |
| Mid-SSE abort only between ReAct steps | **Partially fixed.** Main consume path races abort (`stream_consume.py:38–55`, `:110–112`). A second ClientSession in `loop.py:3146–3159` still `async for line in resp.content` with `sock_read=900` and no abort race. |

## Issues

### Issue 1 -- Severity: bug
- File: src/remedy/runtime/rmb/service.py:2468
- Description: `get_rmb_status` is the handler for `GET /api/rmb/status` (`interfaces/routes/rmb.py:100–107`, `asyncio.to_thread`). Desktop `useSessionLlm.ts:218–243` polls it every **8s** whenever the chat provider is RMB. The RMB watchdog (`service.py:65`, `:455–474`) independently calls `is_running(..., force=True, require_http=True)` every **8s**. One status call does all of the following, none of it cached across polls:

  1. `ensure_rmb_watchdog` (cheap if already running).
  2. `is_running(force=True, require_http=True)` (`:2482`) — **bypasses** the 1.5s `_RUNNING_CACHE_TTL_S` (`:58`, `:255–260`) and always hits `_health`.
  3. `_health` (`:91–168`) still walks **three** URLs (`/health`, `/v1/models`, `/models`) sequentially, each `urlopen` timeout **`_HEALTH_TIMEOUT_S = 0.9`** (`:59`). Mid-load / wedged llama-server: first path 503 (fast) then the next two wait up to 0.9s each → **~1.8s for one `_health`**. Worst case all three hang → **2.7s**.
  4. If not running and `auto_start`: `adopt_existing_host` (`:2492`, another `_health` at 1.0s × 3 URLs) then `is_running` **again** (`:2493`).
  5. `is_loading` (`:2499` → `:200–214`) is another `_health(timeout=0.9)` whenever the port is open.
  6. `is_starting` (`:2501` → `:228–230`) calls `is_running(force=True, require_http=True)` **again** whenever this process owns a live child.
  7. `_nvidia_ok()` and `_gpu_present()` (`:2666–2667`) each call `probe_gpus()` with **no cache** (`gpu_probe.py:128–150`). Every snapshot always runs nvidia-smi (`timeout=4`, `:177–184`, `:217–223`) **and** `Get-CimInstance Win32_VideoController` (`timeout=6`, `:371–384`) even after nvidia-smi already succeeded. Two snapshots = two nvidia-smi + two CIM. Under GPU load that is hundreds of ms to >1s **after** HTTP already returned.
  8. `discover_ggufs` (`:2540`, `:762–811`) + `_resolve_model_path` (`:2497`) both walk `_model_search_roots` (`:698–706`), which includes **`Path.home() / "Downloads"`** (top-level `*.gguf` glob + `Path.resolve()` on each root).
  9. When `ready`, `sync_rmb_chat_identity(..., force_provider=True)` (`:2563–2573`) **invalidates the config cache and writes `config.toml` every poll** (`:3006–3027`). GET mutates disk; concurrent Settings PUT can lose fields; every 8s poll pays a full TOML rewrite.

  A healthy, managed host still pays: `_health` × (is_running + is_loading + is_starting) + 2× `probe_gpus` + Downloads glob + config write. A loading host pays the 0.9s × 3 URL tax two to four times. **~1650ms is the expected cost of this function, not a one-off stall.**
- Suggestion: Split “UI snapshot” from “heal/start”. Status should: (a) one TCP check, (b) at most one HTTP URL with ≤150ms timeout, (c) cached GPU probe (30s+, share with vision), (d) no Downloads scan, (e) never write `config.toml`. Keep adopt / wake / identity-sync on start/settings/watchdog only. Honor `_RUNNING_CACHE_TTL_S` on the poll path (`force=False`). Collapse `_health` to `/health` only.
- Status: open

### Issue 2 -- Severity: suggestion
- File: CHANGELOG.md:17
- Description: Unreleased note says `GET /api/rmb/status` “probes the host once per call instead of three HTTP health checks”. `_health` still has a 3-tuple `paths_try` (`service.py:109–113`) and `get_rmb_status` calls `_health` more than once (Issue 1). The changelog describes an intent that is not in the code.
- Suggestion: Either actually probe once, or delete the sentence so the next ship notes stay honest.
- Status: open

### Issue 3 -- Severity: bug
- File: src/remedy/runtime/rmb/service.py:2563
- Description: Ready-path identity “heal” on a GET (see Issue 1 step 9). `sync_rmb_chat_identity` always sets `last_model_by_provider.rmb` and, with `force_provider=True`, also overwrites `llm_provider` / `llm_model` / `llm_base_url` / harness percents (`:3014–3021`) then `_write_config`. A status poll while the user is saving Settings, or while they briefly switched provider, can clobber `config.toml`. This is a correctness bug that also makes every 8s poll expensive.
- Suggestion: Sync identity only from `apply_rmb_settings` / `rmb_use` / model switch. If drift must be healed, write only when stem actually changed (compare before `_write_config`).
- Status: open

### Issue 4 -- Severity: suggestion
- File: src/remedy/interfaces/routes/vision.py:45
- Description: `GET /api/vision/status` calls `get_status(cfg)` with default `light=False` (`vision/service.py:75–78`, `:191–195`). Full status always builds `catalog_public()` and `_cached_system_health` → `system_health` → `detect_gpu` → `probe_gpus()` (`vision/health.py:92–105`, `:143`). GPU cache TTL is 30s (`service.py:38`) and is **per vision process only** — it does not share with RMB’s two uncached `probe_gpus` calls. StatusBar polls vision (`StatusBar.tsx:457–478`: 12–45s idle, **1.5s while busy**). A cache miss is one nvidia-smi + CIM ≈ **~400–600ms**, which matches the live ~520ms. Settings GET already learned this (`settings.py:284–297` uses `light=True`); the dedicated vision route did not.
- Suggestion: Default the poll route to `light=True`. Expose health/catalog on `/api/vision/catalog` (already exists) or `?full=1`. Share one process-wide GPU snapshot between RMB and vision.
- Status: open

### Issue 5 -- Severity: suggestion
- File: src/remedy/interfaces/routes/settings.py:78
- Description: `GET /api/settings` is `async def` but runs entirely on the event loop: `load_config`, secret-store, messengers, assistant store, `vision_get_status(..., light=True)`, `sleev_status`. No `asyncio.to_thread`. It logs `GET /api/settings slow` at ≥250ms (`:329–331`). Desktop calls this on bootstrap, Settings open (`SettingsPanel.tsx:313`), send-flow, and Browser slide. Light vision is cheaper than Issue 4, but DPAPI / assistant SQLite / sleev still block SSE abort and other polls if they hitch.
- Suggestion: Offload the body with `asyncio.to_thread` (same pattern as RMB/vision status). Keep GET side-effect free (already documented at `:97`, `:116–118`).
- Status: open

### Issue 6 -- Severity: suggestion
- File: src/remedy/interfaces/routes/auth.py:199
- Description: Provider model list for `pid == "rmb"` calls **full** `get_rmb_status` just to read `discovered_ggufs` (`:194–204`). That inherits Issue 1 (~1.6s) on every model refresh. `useSessionLlm.ts:209` also `refreshModels({ provider: 'rmb' })` after a model change, stacked on the 8s status poll.
- Suggestion: `discover_ggufs` only (or a `light=True` status). Do not adopt/wake/GPU-probe/write-config to fill a picker.
- Status: open

### Issue 7 -- Severity: suggestion
- File: src/remedy/interfaces/diagnostics.py:399
- Description: Diagnostics RMB section calls `get_rmb_status` then `is_running` again then `_http_latency_ms` (`:399–406`). The panel auto-polls every 8s when open (`DiagnosticsPanel.tsx:241–246`). Three host touches per tick on top of the desktop 8s poll and the watchdog.
- Suggestion: Reuse one light snapshot; measure latency only on expand or a slower interval.
- Status: open

### Issue 8 -- Severity: bug
- File: src/remedy/runtime/rmb/hf.py:590
- Description: `download_gguf` always `_cancel.clear()` at the start of a pull. `start_pull` already clears (`:715`) then starts a thread. If the user hits Cancel after `start_pull` returns and before the worker reaches `:590`, the worker **un-cancels** and continues the download. `cancel_pull` (`:185–190`) also no-ops unless `phase == "downloading"`, so once the worker sets `phase="loading"` and calls `apply_rmb_settings(..., wait_s=120)` (`:672–690`) Stop cannot abort the 120s llama restart. Tests cover parse/search/list/resume (`tests/test_rmb_hf.py`) but not cancel or start races.
- Suggestion: Never clear cancel inside `download_gguf`; clear only in `start_pull` immediately before `t.start()`. Treat `phase in ("downloading", "loading")` as cancellable (set event; loading path should not start a new server if cancelled). Guard `start_pull` with `_progress_lock` so two POSTs cannot both pass `is_pulling()` (`:649–651`).
- Status: open

### Issue 9 -- Severity: bug
- File: src/remedy/runtime/rmb/hf.py:451
- Description: Dest is always `models_dir / basename` (`:451–460`; `del repo`). Two Hugging Face files that share a basename (different repos, or `subdir/foo.gguf` vs `foo.gguf`) write the same path. `_download_one` (`:478–482`) **skips** an existing dest when `size > 64` and (`expected_size` is 0 **or** sizes match). URL pulls often send `expected_size=0` (`RmbHfPull`, `rmb.py:331`). A leftover/wrong GGUF is treated as complete. `partial.replace(dest)` (`:542`) overwrites without backup when a real download does run.
- Suggestion: Namespace dest (`models/{owner}__{repo}__{basename}`) or refuse overwrite unless size+etag match. If dest exists and size ≠ expected, resume/replace `.partial` and do not skip. Require `expected_size` from the tree listing when present.
- Status: open

### Issue 10 -- Severity: suggestion
- File: src/remedy/runtime/rmb/hf.py:373
- Description: Multipart handling is incomplete, not unused: `_is_multipart_skip` keeps only names containing `00001` (`:373–378`); `_sibling_parts` then expands `00001-of-N` (`:546–564`). Repos that shard as `00000-of-N` list **no** shards. `expected_size` is applied only to the first part (`:613`); `on_chunk` still counts later parts, so `pct` can exceed 100. Tree pagination uses `x-next-cursor` or **`x-linked-etag`** as cursor (`:436–438`) — HF’s tree API normally uses `Link` / `cursor=`; a wrong etag can loop up to 20 × 30s `_hf_json` calls.
- Suggestion: Treat `00000` as a first shard too. Sum sizes for `bytes_total`. Paginate only on a documented cursor/Link header; never reuse ETag as cursor.
- Status: open

### Issue 11 -- Severity: nit
- File: src/remedy/runtime/rmb/catalog.py:15
- Description: `RmbModelSpec.hf_repo` is serialized in `catalog_public()` / `to_public()` (`:22–31`, `:41`, `:54`, `:64`) and typed on the desktop (`desktop/src/api/rmb.ts:9`). Nothing in `hf.py` or the HF routes reads it. Pulls always go through user search / pasted URL. Vision’s `runtime/catalog.py:58` **does** use `hf_repo` for download URLs — different catalog. RMB’s field is display-only dead weight unless the UI grows a “get the catalog GGUF” button.
- Suggestion: Either wire “Install catalog model” to `hf_repo` + `filename`, or stop shipping unused URLs in the status payload (status already embeds full `catalog_public()` every 8s — Issue 1 `:2668`).
- Status: open

### Issue 12 -- Severity: suggestion
- File: src/remedy/core/react_loop/loop.py:3146
- Description: Main SSE consume now aborts cooperatively (`stream_consume.py:38–55`). The recovery/second session still opens `ClientTimeout(total=900, sock_read=900)` and iterates `resp.content` with no abort race (`loop.py:3146–3159`). Stop can sit on a stuck local host for up to 900s on that path. Primary loop timeout is the same 900s sock_read (`:212–214`) but that path *is* abort-raced. `classify_turn_tier` also runs twice before the first token (`agent.py:1061–1064` and `agent_react_preamble.py:191`) — cheap regexes, not the 1650ms, but wasted work on every turn including L0.
- Suggestion: Route the fallback session through `consume_llm_http_response`. Skip the second `classify_turn_tier` when `try_l0_system_reply(..., preclassified=True)` already ran in `stream_response`.
- Status: open

### Issue 13 -- Severity: bug
- File: tests/test_e2e_simple_c_rmb.py:23
- Description: `_rmb_ready()` (`:23–31`) is used in `@pytest.mark.skipif` (`:45`), which pytest evaluates at **collection**. If port 8787 is open but `/health` is not ready, this calls `wait_rmb_ready(timeout_s=30)` during collection — up to 30s of `_health` loops (`service.py:1573–1619`) before any test runs. Default `addopts` (`pyproject.toml:72`) is `-v --tb=short --strict-markers` and **does not** exclude `-m live`. Linux CI is safe only because the port is closed (fast `is_running` false → then **still** enters `wait_rmb_ready(30)`!). Wait: if not running, `_rmb_ready` **always** calls `wait_rmb_ready(30)`, which loops until deadline starting the host if enabled (`:1596–1618`). On a dev box with `rmb.json` `enabled`+`auto_start`, **collecting pytest can spawn llama-server**. On CI with no RMB config, `start_rmb_server` should fail quickly, but the function still polls until timeout if a start is attempted or the port is sticky.
- Suggestion: Collection skipif must be a 200ms TCP/`is_running` check only — never `wait_rmb_ready`. Add `addopts` `-m "not live"`. Keep the 30s wait inside the test body after skip.
- Status: open

### Issue 14 -- Severity: suggestion
- File: src/remedy/runtime/rmb/service.py:1
- Description: `service.py` is **3539** lines (watchdog, health, adopt, GGUF scan, GPU, autofit card, start/stop, settings apply, identity sync, public status). `get_rmb_status` doing heal+write (Issues 1 and 3) is the maintainability defect that produces the 1650ms poll and config races — not file length by itself. Other still-large modules: `react_loop/loop.py` (~3390, many `set_turn_force_tool_choice` sites), `App.tsx` (1951), `react_policy.py` (1912). `agent.py` is now an orchestrator (1259) — that split held.
- Suggestion: Extract `health.py` (port + one HTTP + cache) and `status.py` (read-only snapshot). Leave start/stop/apply in `service.py`. Do not start a third ReAct loop.
- Status: open

### Issue 15 -- Severity: suggestion
- File: src/remedy/runtime/gpu_probe.py:128
- Description: `probe_gpus()` has no TTL. RMB status calls it twice per poll; vision health calls it on a separate 30s cache; `plan_autofit` → `probe_hardware` → `probe_primary_vram` calls it again when previewing a GGUF (`autofit.py:234–241`, `service.py:2737–2744` when not running). No sharing. `probe_gpus` never returns early after nvidia-smi succeeds (`:141–150`).
- Suggestion: Process-wide snapshot, TTL 15–30s, invalidate on RMB start/stop. Skip CIM when nvidia-smi already returned a dedicated device.
- Status: open

### Issue 16 -- Severity: suggestion
- File: pyproject.toml:123
- Description: Unchanged from the prior review: mypy `files` is `core` / `execution` / `tools` plus a few interface helpers; `exclude` still lists `react_loop/loop.py`, `tool_batch.py`, `react_turn.py`, `agent_llm.py`, build/learning modules. `interfaces/routes/` (including new `rmb.py` HF routes) is not type-checked. CI `uv run mypy` cannot catch the progress/cancel races in Issue 8.
- Suggestion: Add `src/remedy/runtime/rmb/hf.py` and `interfaces/routes/rmb.py` first (small, new). Then `tool_batch.py`.
- Status: open

### Issue 17 -- Severity: suggestion
- File: tests/conftest.py:7
- Description: Still no autouse `REMEDY_HOME`. Residual live-home tests: `test_computer_use.py:1296` (`~/.remedy/computer/shots/_test_print.png`), `test_vision.py:635`. Windows CI now *runs* `test_computer_use.py`, so Issue 2 from the prior review can mutate a CI user profile if the runner has a foreground window.
- Suggestion: Autouse tmp `REMEDY_HOME`. Point `print_window_png` at `tmp_path`.
- Status: open

### Issue 18 -- Severity: nit
- File: tests/test_e2e_simple_c_rmb.py:52
- Description: Live e2e still uses a repo-root scratch dir `_e2e_simple_c_pytest/` (gitignored). Home is isolated; project files are not. Harmless if ignored; `git add -f` still possible.
- Suggestion: Use `tmp_path / "proj"` unless a jail test specifically needs a repo-relative path.
- Status: open

## What is in good shape (this pass)

- Product version surfaces say **0.24.0**; CHANGELOG has a dated `[0.24.0]` section.
- RMB/vision status HTTP handlers use `asyncio.to_thread` (settings GET does not).
- Vision `is_running` avoids urlopen on a closed port (`vision/runtime.py:156–157`); health timeout is 0.35s and cached 2.5s.
- HF module blocks non-HF hosts / `file://` / IMDS (`tests/test_rmb_hf.py:74–83`); redirect handler re-checks host (`hf.py:127–142`).
- Range resume is tested (`test_download_resumes_partial`).
- Computer-use Linux skips are real skips; Windows CI list includes jail + CUA.
- Turn flags are ContextVar-backed when `begin_turn` ran; computer tools and messenger cancel no longer match the 0.23.2 review.
- `python-multipart` is a declared dependency; HF routes are JSON (not multipart HTTP). “Multipart” in this review means GGUF shards only.

## Measurable hot path (poll)

| Caller | Interval | Work |
|--------|----------|------|
| Watchdog `_loop` | 8s | `is_running(force, http)` → `_health` × up to 3 URLs |
| `useSessionLlm` | 8s if provider=rmb | full `get_rmb_status` (Issue 1) |
| Settings RMB card | on open / after start | full `get_rmb_status` |
| HF pull UI | 800ms | `GET /api/rmb/hf/progress` only (cheap) |
| StatusBar vision | 12–45s idle, 1.5s busy | full `get_status` (Issue 4) |
| Diagnostics | 8s when open | `get_rmb_status` + extra `is_running` + latency |
| `GET /api/settings` | open / bootstrap / send | light vision + secrets + sleev on event loop |

Do not treat green Linux pytest as proof these polls are fast — the suite does not assert `get_rmb_status` wall time.
