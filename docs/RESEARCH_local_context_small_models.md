# Small local models & long code sessions — context handling

**Status:** research + first implementation landed
**Date:** 2026-08-04
**Applies to:** Remedy core context pipeline (`remedy.nanoswarm.token_nanobot`, `remedy.core.agent_context`, `remedy.memory.harness.send_policy`)

---

## 1. The problem (worldwide, quantified)

Small local models (Ollama / llama.cpp, 1B–14B) have a small **fixed** context
window (`n_ctx`) that the OpenAI API has **no way to grow**. Typical defaults:
4k–8k unless the server/Modelfile raises `num_ctx`. Cloud-scale agents are built
for 128k–200k and assume huge windows. The mismatch is not cosmetic:

> On a real 4k `n_ctx`, Remedy's always-on system block alone was measured at
> **~3,087 tokens = 75% of the window** — before a single user message, before
> any history, tool schemas, or the answer.

So the model got a truncated prompt, lost its system instructions, and "couldn't
complete simple tasks" — every turn. This is the same failure class users report
everywhere with local agents: it is not a model-quality issue, it is a
**budgeting** issue.

Two concrete root causes were found and fixed:

1. **Assumed window ≫ real window.** `resolve_context_window()` returned 32k for
   any `ollama`/local model and — worse — **128k** for a local `deepseek-r1:7b`
   because the `deepseek` model-name rule fired before the local-family rule.
   The whole Memory Harness (fill%, soft/strong compression, mid-turn slim) then
   budgeted against a window the model never had, so it never compressed in time.

2. **The always-on head block is untouchable and too big.** `build_turn_context()`
   emits static instructions + a 24-skill catalog + auto-suggested skill bodies
   (~3k tokens, sometimes much more) as the head `system` message. The Memory
   Harness prunes **history only**, never the head, so a 4k model overflowed on
   turn one with nowhere to shed.

## 2. What was implemented

### 2.1 Conservative, size-aware window resolution (`token_nanobot.py`)

`resolve_context_window(provider, model)` now treats a model as **local** when:

- the provider is a local provider (`ollama`, `demo`, `local`), or
- the model family is local and the provider is not a known cloud provider, or
- the model carries a **size suffix** (`qwen2.5:7b`, `llama3.2:3b`) — this makes a
  llama.cpp server behind a `custom` base URL get the tight budget too.

Local windows are picked from the size suffix (conservative):

| Size | Window |
|------|--------|
| 0.5b–1b | 4k |
| 2b–3b | 8k |
| 4b–9b | 16k |
| 10b–19b | 32k |
| no suffix | 8k |

Cloud providers (Groq llama-70b, gpt-4o, claude, …) keep their large windows.
The `deepseek-r1:7b → 128k` bug is gone.

### 2.2 Token-aware cap on the always-on head (`agent_context.py`)

`build_turn_context()` now measures the assembled head against the model's
resolved window and, when it exceeds `window × 0.45` (leaving room for history,
answer, tool schemas), sheds the most expendable blocks **in order**: the skills
catalog / auto-suggested skill bodies → the secondary instruction blocks
(self-config, durable-memory, help, recent-memory, tool list). It never drops the
isolation banner or the workspace/orientation/partner/brief blocks.

### 2.3 (prior fix) Don't send a cloud `max_tokens` to a local server

`LlamaCppProvider` (see `providers.py`, wired for `ollama`/`llamacpp`) omits
`max_tokens` and `reasoning_effort` — a 128k `num_predict` is what made the server
reject requests outright.

## 3. The full strategy for small-model long sessions (roadmap)

The two fixes above are the first two rungs. The complete answer is a ladder of
increasingly aggressive compression, all keyed to the **real** window:

1. **Accurate budget** (done) — know the real `n_ctx`, budget against it.
2. **Head capping** (done) — the system prompt must fit.
3. **Live window discovery** (next) — probe the local server for its actual
   `n_ctx` at connect time instead of inferring from the size suffix:
   - llama.cpp server: `GET /props` → `default_generation_settings.n_ctx`
   - Ollama: `GET /api/show` (Modelfile `num_ctx`) or `/api/tags`
   Cache per (base_url, model); fall back to the suffix heuristic.
4. **Per-model context override** in config/settings (`model_context_window`) for
   users running a custom `num_ctx`/`-c`.
5. **Nested/tiered summarization** — when the harness is about to drop tool
   history, push the dropped spans into the Session Brief (already partially done
   by `send_policy` strong + `local_brief`). For small windows this must trigger
   much earlier (the 0.75/0.92 fill gates are tuned for cloud).
6. **Retrieval over memory, not full history** — keep full transcripts on disk;
   inject only a query-ranked window. `build_turn_context` already notes "Prefer
   query-time search later; recent is a light fallback" — make that the primary
   path on constrained windows.
7. **Output headroom reservation** — with `max_tokens` omitted, the model runs to
   context/EOS; keep prompt ≤ ~60% of window so the answer isn't starved. (The
   0.45 head factor is the first half of this.)
8. **Hybrid delegation** — for genuinely huge tasks, the strongest lever is not
   local-context cleverness but routing: let the small model run the loop while a
   cloud model (or a local model with a bigger `num_ctx`) does the deep
   single-file reasoning, via the existing spread/delegate machinery.

### Why compression alone is not enough

With a 4k window, "compress history" has nothing to work with the moment the
**head + current turn + one tool result** exceeds 4k. So for very small windows
the sequence matters: **budget → head → discovery → retrieval**, in that order.
Summarization becomes effective only after the head is small enough to leave room
for a summary pointer.

## 4. Verification

- `tests/test_usage_ledger_and_pattern.py` — local vs cloud window resolution.
- `tests/test_agent_context.py` — head trimming drops the catalog, keeps workspace.
- `tests/test_providers.py` — `LlamaCppProvider` omits `max_tokens`/`reasoning_effort`.
- Experiment `C:\Users\Administrator\AppData\Local\Temp\opencode\ctx_measure.py`
  quantifies the 75%-of-4k head overflow.

Run: `uv run pytest -q` (full suite); desktop untouched by these changes.

## 5. Related files

- `src/remedy/nanoswarm/token_nanobot.py` — window resolution + token estimate
- `src/remedy/core/agent_context.py` — head assembly + `_trim_context_parts`
- `src/remedy/core/providers.py` — `LlamaCppProvider`
- `src/remedy/memory/harness/send_policy.py` — window-driven history compression
