# Computer-use soak sign-off

**Date:** 2026-07-30  
**Tester:** Remedy agent (session Ahmi)  
**Branch:** `feature/computer-use`  
**SHA:** `901abf8` (+ local follow-up for browser screenshot PrintWindow path)  
**Ready to merge master?** **no** (Plan mode / multi-provider / offline fallback / concurrent sessions still SKIP)

Source checklist: F1 `help_read(id="computer-use-soak")` / `docs/manual/computer-use-soak.md`  
Machine results: `docs/_soak_probe_results.json` (PASS=22 FAIL=0 SKIP=4 on first pass; browser screenshot upgraded to PrintWindow after small executor fix)

## Preconditions

| Item | Result | Evidence |
|------|--------|----------|
| Checkout `feature/computer-use` | **PASS** | branch `feature/computer-use` |
| Local server from this tree | **PASS** | ping version=0.20.0 on `:7400` |
| Desktop app + host poller | **PASS** | app+remedy after rebuild; navigate `via=rust-host` |
| Build mode (not Plan) | **PASS** | click/type exercised |

## Desktop path

| Item | Result | Evidence |
|------|--------|----------|
| `computer_monitors` ≥1 | **PASS** | 3 monitors; primary 1920×1080 |
| `computer_screenshot` → shots/ | **PASS** | desk_*.png 3840×2160 |
| `computer_screenshot monitor=0` | **PASS** | 1920×1080 |
| `computer_snapshot` w1… | **PASS** | 12 windows |
| `computer_snapshot mode=controls` c1… | **PASS** | 7 UIA controls (comtypes) |
| `computer_click ref=wN` | **PASS** | clicked w1 Remedy Desktop |
| `computer_click ref=cN` | **PASS** | clicked c4 System |
| `computer_type` notepad | **PASS** | Typed 12 chars |
| Stop mid-type | **SKIP** | not exercised (job cancel covered below) |

## Browser path

| Item | Result | Evidence |
|------|--------|----------|
| PC host chip visible | **SKIP** | UI chip not OCR'd |
| `computer_navigate` in-rail | **PASS** | via=rust-host 0.4s example.com |
| `computer_snapshot` e1… | **PASS** | e1 in 1.5s |
| `computer_click ref=eN` | **PASS** | ok:e1:A via=rust-host 0.1s → iana.org |
| `computer_page_text` | **PASS** | 1267 chars title=Example Domains |
| Browser screenshot (PrintWindow/rail) | **PASS** | PrintWindow 436×656 hwnd_*.png |
| Stop cancels pending browser job | **PASS** | cancel_count=1 status=cancelled |

## Hybrid / routing

| Item | Result | Evidence |
|------|--------|----------|
| URL-ish prefers browser | **PASS** | navigate → rust-host rail |
| Start menu / desktop installer | **SKIP** | not exercised |
| Host offline fallbacks | **SKIP** | not forced |

## Plan mode

| Item | Result | Evidence |
|------|--------|----------|
| snapshot/screenshot/navigate/monitors allowed | **PASS** | Plan mode: monitors/snapshot/screenshot/navigate execute (not PLAN_MODE_BLOCKED) |
| click/type blocked in Plan | **PASS** | computer_click/type/key/scroll/act/app → PLAN_MODE_BLOCKED + Build suggestion |
| F1 help in Plan | **PASS** | help_list + help_read(computer-use-soak) allowed |

## Provider-agnostic

| Item | Result | Evidence |
|------|--------|----------|
| ≥2 chat providers | **SKIP** | single provider |

## Regression

| Item | Result | Evidence |
|------|--------|----------|
| Short file edit + bash_exec | **PASS** | file edit + `git rev-parse` |
| `tests/test_computer_use.py` | **PASS** | 44 passed |
| Concurrent turns / session switch | **SKIP** | single session |

## Blockers before merge

1. ~~Browser DOM snapshot / page_text / click timeouts~~ — fixed (prior commit `901abf8`).
2. ~~UIA / comtypes~~ — fixed.
3. ~~Browser screenshot full-desktop fallback when poller flag stale~~ — fixed (try PrintWindow before offline desktop shot).
4. ~~Plan-mode matrix~~ — fixed (help + computer observe allow / input block; live soak PASS).
5. Still open: multi-provider, forced offline fallbacks, concurrent sessions, Stop mid-type keystroke race.

## Rebuild + re-soak notes

- Clean stop of app/remedy → `cargo build` (already current) → `npm run tauri:dev`.
- Ready: app=13616 remedy=46864, API 0.20.0.
- Soak script: `scripts/_soak_run.py` (local helper; optional to keep).
