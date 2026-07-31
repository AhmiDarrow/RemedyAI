# Computer-use soak sign-off

**Date:** 2026-07-30  
**Tester:** Remedy agent (session Ahmi)  
**Branch:** `feature/computer-use`  
**SHA:** (see latest commit on branch after open-issues wave)  
**Ready to merge master?** **closer** — core paths green; still optional live dual-LLM chat smoke

Source checklist: F1 `help_read(id="computer-use-soak")` / `docs/manual/computer-use-soak.md`

## Preconditions

| Item | Result | Evidence |
|------|--------|----------|
| Checkout `feature/computer-use` | **PASS** | branch live |
| Local server + Desktop host | **PASS** | `:7400` + app/remedy |
| Build mode | **PASS** | click/type exercised earlier |

## Desktop / Browser (prior wave)

| Item | Result |
|------|--------|
| Monitors, screenshots, w1…, c1…, type, navigate, e1, click eN, page_text, PrintWindow | **PASS** |
| Stop cancels pending browser job | **PASS** |
| Plan mode observe allow / input block + F1 help | **PASS** |

## Open issues wave

| Item | Result | Evidence |
|------|--------|----------|
| Host offline navigate | **PASS** | refuses OS browser unless explicit (`rail_failed`); unit + isolated home probe |
| Host offline snapshot | **PASS** | immediate desktop fallback `fallback=desktop` + note (no multi-second hang) |
| Concurrent sessions | **PASS** | abort session A cancels only A jobs; B remains pending; `test_stream_concurrency` |
| Stop mid-type | **PASS** | abort at char 8; executor returns `aborted=True` + typed count |
| Multi-provider | **PASS** (tool layer) | computer tools independent of xAI/DeepSeek/OpenAI adapters (`_PROVIDERS` ≥2) |
| Live dual-LLM chat smoke | **SKIP** | requires two configured API keys in UI; tool layer proven agnostic |

## Tests

- New: offline navigate/snapshot, mid-type abort, provider-agnostic tools, concurrent abort isolation
- `tests/test_computer_use.py` + `tests/test_stream_concurrency.py` green for these paths

## Blockers before merge

1. Optional: manual dual-provider chat UI smoke (xAI + DeepSeek with real keys).
2. Optional: Stop mid-type live Notepad keystroke visual (unit path covered).

Otherwise computer-use soak checklist is **substantially solid** on `feature/computer-use`.
