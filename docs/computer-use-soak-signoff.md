# Computer-use soak sign-off

**Date:** 2026-07-30  
**Tester:** Remedy agent (session Ahmi)  
**Branch:** `feature/computer-use`  
**SHA:** (latest local commit after open-issues final wave)  
**Ready to merge master?** **yes** (local merge OK; push only when you choose a release path)

Source checklist: F1 `help_read(id="computer-use-soak")` / `docs/manual/computer-use-soak.md`  
Evidence: `docs/_open_issues_final.json`, `docs/_soak_probe_results.json`, unit suite

## Preconditions

| Item | Result | Evidence |
|------|--------|----------|
| Checkout `feature/computer-use` | **PASS** | branch live |
| Local server + Desktop host | **PASS** | `:7400` + app/remedy |
| Build mode | **PASS** | click/type exercised |

## Desktop / Browser

| Item | Result |
|------|--------|
| Monitors, screenshots, w1…, c1…, type, navigate, e1, click eN, page_text, PrintWindow | **PASS** |
| Stop cancels pending browser job | **PASS** |
| Plan mode observe allow / input block + F1 help | **PASS** |

## Hybrid / routing

| Item | Result | Evidence |
|------|--------|----------|
| URL-ish prefers browser | **PASS** | navigate → rust-host rail |
| Start menu / installer → desktop | **PASS** | `resolve_target` desktop for Start menu + setup.exe |
| Host offline navigate | **PASS** | refuses surprise OS browser |
| Host offline snapshot | **PASS** | immediate desktop fallback |

## Plan mode

| Item | Result |
|------|--------|
| snapshot/screenshot/navigate/monitors + help | **PASS** |
| click/type/act blocked | **PASS** |

## Provider-agnostic

| Item | Result | Evidence |
|------|--------|----------|
| Two chat providers + computer tools | **PASS** | Live xAI grok-4.5 **and** DeepSeek both emitted `computer_monitors` tool_calls; tool executed (3 monitors) |

## Stop / concurrency

| Item | Result | Evidence |
|------|--------|----------|
| Stop mid-type | **PASS** | Live Notepad path: abort on turn thread → `Aborted by user` mid-type (check every 2 chars) |
| Concurrent sessions | **PASS** | abort A cancels A only; B stays pending |
| Concurrent streams unit | **PASS** | `test_stream_concurrency` |

## Regression

| Item | Result |
|------|--------|
| File edit + bash | **PASS** |
| Computer unit tests | **PASS** (49+ in test_computer_use) |

## Blockers before merge

None for computer-use soak. Optional release hygiene only (CHANGELOG polish, PR).

## Commits (local, not pushed)

- Host snapshot/page_text/click + comtypes  
- Soak + PrintWindow path  
- Plan-mode matrix + F1 help  
- Offline fallback + mid-type + open-issue tests  
- Final wave: dual-provider live smoke, mid-type on turn thread, tighter abort checks  
