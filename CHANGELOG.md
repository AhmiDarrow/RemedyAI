# Changelog

All notable changes to Remedy (`remedy-ai`) are documented here.

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
