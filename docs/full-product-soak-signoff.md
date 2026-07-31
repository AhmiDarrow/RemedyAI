# Full product soak sign-off

**Date:** 2026-07-30  
**Branch:** `master`  
**SHA:** `2b6b4ec` (+ follow-up snapshot host-try fix if committed after)  
**Tester:** Remedy agent (full automated soak)  
**Machine results:** `docs/_full_product_soak_results.json`

## Summary

| Metric | Value |
|--------|-------|
| **PASS** | **39** (primary run) + e1 click re-probe **PASS** after snapshot fix |
| **FAIL** | **0** |
| **SKIP** | **2** (click eN during first run due to CLI host flag; host_connected in-process) |
| **Elapsed** | ~112s core soak + unit suites |

**Verdict:** **PASS — product soak green.** Desktop + API live; computer-use, plan mode, dual providers, offline fallbacks, concurrency, docs check, and core pytest suites all green.

## Suites exercised

### Preconditions / API
- Git `master`, API `:7400` ping/status `0.20.0`
- Desktop `app.exe` + host `host_connected: true` (bounds live)
- Computer host hello + jobs/next

### Computer-use
- Monitors (3), window snapshot, UIA controls (10), navigate `via=rust-host`
- Browser page_text, PrintWindow screenshot
- Offline: navigate refuses OS browser; snapshot → desktop fallback
- **e1 click** confirmed after host-try fix: `ok:e1:A`

### Plan mode
- help_list/help_read + computer_monitors allowed
- computer_click/type, bash_exec, file_write blocked

### Concurrent / abort
- Session A cancel leaves B pending
- Stop mid-type aborts

### Providers
- xAI + DeepSeek both emit `computer_monitors` tool_calls; tool executes

### Routing / regression
- Start menu / installer → desktop; URL → browser
- File edit + git

### Automated tests
- `test_computer_use`, `test_plan_mode_stream`, `test_stream_concurrency`, `test_help_docs`, `test_browse_intent`
- `test_session_stream`, `test_turn_context`, `test_web_fetch_ssrf`
- `scripts/check_docs.py` exit 0

## Not fully UI-OCR’d (by design)
- Browser rail **📱/🖥** toggle (shipped; manual smoke earlier)
- Visual Gmail icon paint (cosmetic inject disabled; mobile UA default)
- Multi-tab concurrent chat streams (unit coverage yes; live dual-tab UI not driven)

## Follow-up fix during soak
CLI/desktop process split: browser `computer_snapshot` no longer skips the host job queue solely because in-process `host_connected` is false — tries a short rail claim first, then desktop fallback.

## How to re-run
```bash
# Desktop + sidecar up
uv run python scripts/_full_product_soak.py
```
