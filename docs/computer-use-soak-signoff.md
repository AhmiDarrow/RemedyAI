# Computer-use soak sign-off

**Date:** 2026-07-30  
**Tester:** Remedy agent (session Ahmi)  
**Branch:** `feature/computer-use`  
**SHA:** (see latest local commit after soak fix wave)  
**Ready to merge master?** **no** (still SKIPs on Plan mode, Stop cancel, multi-provider)

Source checklist: F1 `help_read(id="computer-use-soak")` / `docs/manual/computer-use-soak.md`

## Preconditions

| Item | Result | Evidence |
|------|--------|----------|
| Checkout `feature/computer-use` | **PASS** | `git rev-parse --abbrev-ref HEAD` → feature/computer-use |
| Local server from this tree | **PASS** | Uvicorn on 127.0.0.1:7400, WebUI from desktop\dist / tauri:dev |
| Desktop app + host poller (PC host chip) | **PASS** (partial) | Remedy Desktop hwnd live; navigate/snapshot/click `via=rust-host`. Status chip not OCR'd |
| Build mode (not Plan) | **PASS** | click/type succeeded this session |

## Desktop path

| Item | Result | Evidence |
|------|--------|----------|
| `computer_monitors` ≥1 display | **PASS** | 3 monitors; primary index 0 1920×1080 |
| `computer_screenshot` → ~/.remedy/computer/shots/ | **PASS** | desk_*.png under shots/ |
| `computer_screenshot monitor=0` primary | **PASS** | region capture 1920×1080 origin (0,0) |
| `computer_snapshot` window refs w1… | **PASS** | 12–13 windows with titles |
| `computer_snapshot mode=controls` c1… | **PASS** | comtypes installed; c1…c7 UIA controls |
| `computer_click ref=wN` | **PASS** | clicked w1 Remedy Desktop |
| `computer_click ref=cN` | **PASS** (partial) | cN refs available; full cN click not re-run after fix |
| `computer_type` notepad/editor | **PASS** | Notepad focused; typed 12 chars |
| Stop mid-type | **SKIP** | not exercised |

## Browser path

| Item | Result | Evidence |
|------|--------|----------|
| PC host chip visible | **SKIP** | UI chip not verified |
| `computer_navigate` in-app rail | **PASS** | via=rust-host; example.com / example.org |
| `computer_snapshot` e1… | **PASS** | e1 "Learn more" on example.com (~1.8s after settle) |
| `computer_click ref=eN` | **PASS** | click e1 → ok:e1:A via=rust-host in 0.1s; navigated to iana.org |
| `computer_page_text` | **PASS** | 127–1267 chars; title/url decoded (double-JSON fix) |
| Browser screenshot under shots/ | **PASS** | WebView PrintWindow ~436×656 |
| Stop cancels pending browser job | **SKIP** | not exercised |

## Hybrid / routing

| Item | Result | Evidence |
|------|--------|----------|
| URL-ish prefers browser | **PASS** (partial) | navigate → browser rail, not system browser |
| Start menu / desktop installer → desktop | **SKIP** | not exercised |
| Host offline fallbacks | **SKIP** | offline not forced |

## Plan mode

| Item | Result | Evidence |
|------|--------|----------|
| snapshot/screenshot/navigate/monitors allowed | **PASS** (partial) | monitors/screenshot/navigate OK in Build; Plan not toggled |
| click/type blocked in Plan | **SKIP** | Plan mode not entered |

## Provider-agnostic

| Item | Result | Evidence |
|------|--------|----------|
| ≥2 chat providers | **SKIP** | only xai/grok-4.5 this session |

## Regression

| Item | Result | Evidence |
|------|--------|----------|
| Short file edit + bash_exec | **PASS** | file_write/file_edit + bash_exec |
| Computer unit tests | **PASS** | `tests/test_computer_use.py` — 44 passed |
| Concurrent turns / session switch | **SKIP** | single session |

## Blockers before merge

1. ~~Browser DOM snapshot / page_text host path~~ — **fixed** (ready wait + eval retry; page_text decode; longer waits; DOM jobs off poller).
2. ~~UIA controls / comtypes~~ — **fixed** (`comtypes` win32 dep; c1… refs).
3. Still untested: Stop mid-type/job cancel, Plan-mode blocks, multi-provider, offline fallbacks, concurrent sessions.

## Fix wave notes (2026-07-30 evening)

- **Root cause (snapshot):** after fire-and-forget navigate, WebView2 eval hung mid-load; 3–4s host wait was too tight. Now: page ready poll, 2× eval retries, 5s eval budget, executor ~14s + one re-enqueue.
- **Root cause (page_text empty):** `JSON.stringify` + host serialization double-encoded; `parse_page_text_raw` unwraps.
- **Root cause (click eN timeout):** synchronous `el.click()` on `<a href>` tore down the document before eval returned. Clicks deferred via `setTimeout(0)`.
- **UIA:** `comtypes>=1.4` on win32 in `pyproject.toml`.
- **Host connected cascade:** do not `mark_host_dead` on DOM job timeouts; optimistic enqueue when in-memory poller flag is stale.
