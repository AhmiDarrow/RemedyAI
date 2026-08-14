# Architecture + core agent review — RemedyAI v0.23.2

Review of the Python agent/runtime: ReAct loop, session isolation, metabolism, nanoswarm, memory, self-inject. Current tree as of 2026-08-13. No code was changed.

## Summary

The hot path is real and has already been split once: `BasicRuntime` orchestrates, `react_loop/loop.py` runs the stream, `turn_context` + `llm_binding` isolate session id / workspace / provider credentials. Prior bugsweep claims about abort persist and abort dropping the stream claim are **refuted in current code**. What remains is a half-migrated singleton: many ReAct control flags still live on the process-wide runtime, so concurrent tabs can steal tool-choice, turn tier, action IR, and the next-turn build protocol. The loop is evolvable incrementally, but it is not safe to treat two in-flight streams as fully isolated.

## Architecture map

**Real (on the chat hot path)**

| Piece | Role |
|-------|------|
| `src/remedy/core/agent.py` (`BasicRuntime`, ~1258 lines) | Process singleton orchestrator: tool registry, workspace jail, `stream_response`, turn lock snapshot |
| `src/remedy/core/react_loop/loop.py` (`call_llm_stream`, ~3391 lines) | Multi-epoch ReAct: arm tools, POST, consume SSE, execute tools, recover, force-answer |
| `src/remedy/core/react_policy.py` (~1913 lines) | System prompt + heuristics (wants-tools, pseudo-tools, unfinished work, nudges) |
| `src/remedy/core/react_turn.py` | `TurnState` + `resolve_tools` — extracted helpers, not a second loop |
| `src/remedy/interfaces/routes/sessions/stream.py` | Claim → persist user → SSE → persist assistant / abort note |
| `src/remedy/core/turn_context.py` | ContextVars + stream claim/epoch + abort Event + proc kill |
| `src/remedy/core/llm_binding.py` | Per-turn provider/model/key ContextVar |
| `src/remedy/core/agent_tool_batch.py` | Parallel tool waves + write-path locks |
| `src/remedy/memory/store.py` | SQLite WAL + RLock — real session history |
| `src/remedy/memory/harness/` | Brief / send_policy / compressor — real context fitting |
| `src/remedy/memory/partner_memory.py` | Durable user facts injected each turn |
| `src/remedy/core/metabolism/` L0 + tier + evidence + governor | Real: L0 skips the model; tier changes caps/shadow/injects |
| `src/remedy/core/build_engine.py` | Real supervisor when the message looks like a task |
| NanoToken + pattern/skill/health | Real accounting / rank / flaky-provider hints |

**Ornamental or weakly wired**

| Piece | Reality |
|-------|---------|
| `agent_react_loop.py` | Compatibility shim → `react_loop` (not a second implementation) |
| `metabolism/forge/`, `metabolism/immune/` | One-line re-exports of `organism.py` |
| Organism pulse / soul mood injects | Prompt flavor; do not change control flow |
| Skill genome | Process-global rank hints, not a genome |
| NanoSwarm router / memory bots | `SwarmEvent.message_added` is never dispatched from production (tests + API only) |
| Helper / guard / pack / scout | Slash/API + optional `context_snapshot` hints; not ReAct control |
| Build `*_pending` flags | Intended as next-turn injects; implemented as process-global leftovers |

```
POST /sessions/{id}/messages/stream
  try_claim_session_stream  →  persist user  →  stream_response
       │                                          │
       │                     _llm_turn_lock (bind + workspace snapshot only)
       │                                          │
       │                     begin_turn (ContextVars) + set_llm_binding
       │                                          │
       │                     L0? → return  else  _call_llm_stream
       │                                          │
       │                     prepare_turn_preamble → call_llm_stream
       │                                          │
       │                     for step in range(MAX 10_000):
       │                       abort check → POST LLM → consume SSE
       │                       execute_tool_calls → metabolism after_tool_batch
       │                                          │
       persist assistant / abort note ← @@aborted / CancelledError / done
       release_session_stream_claim (epoch-guarded)
```

## Prior bugsweep claims

### Abort persist — **refuted**

`stream.py` cooperative stop writes a continue bubble when the model yields `@@aborted` with empty text (`stream.py:465-469`). Client disconnect / ASGI cancel hits `except asyncio.CancelledError` and persists the same note if a row was not already written (`stream.py:521-558`). `CancelledError` is not swallowed by `except Exception`.

### abort_session drops claim — **refuted**

```349:364:src/remedy/core/turn_context.py
def abort_session(session_id: str, *, epoch: int | None = None) -> int:
    """Signal in-flight turns and kill their shell children. ...
    Keeps the stream *claim* until the dying generator releases it so Stop+send
    cannot overlap a still-running ReAct loop.
    """
    ...
        events = list(_registry.pop(sid, []) or [])
        # Claim stays until release_session_stream_claim (stream finally).
```

Claim is released only from the stream `finally` (`stream.py:214-215`) or setup `BaseException` (`stream.py:617-619`), both epoch-guarded. Stale `CancelledError` calling `abort_session(..., epoch=old)` is a no-op if a newer claim exists (`turn_context.py:360-362`).

### Computer `_session_id` is last-tab global — **partially refuted**

`ComputerExecutor._session_id` now prefers `runtime._session_id`, which is a property that reads the turn ContextVar when `in_active_turn()` (`agent.py:195-203`, `executor.py:33-43`). Host-bridge element/drive maps are per-session (`host_bridge.py:291-294`, `403-408`). Residual races remain (Issues 3–4).

---

## Issues

### Issue 1 -- Severity: bug
- File: `src/remedy/core/react_loop/loop.py:292` (also `:911`, `:919`, `:940`, and ~12 later writes); `src/remedy/core/react_loop/build_request.py:115`; `src/remedy/core/agent_react_preamble.py:350-369`
- Description: Concurrent streams share one `BasicRuntime`. ReAct still stores control plane on that singleton: `_force_tool_choice`, `_turn_tier`, `_action_ir`, `_shadow_strict`, `_force_spread`, `_pending_verify_remedy`, `_tool_choice_required_blocked`. Tab A’s unfinished-work drive can set `_force_tool_choice = True` and Tab B’s next POST (`build_request.py:115-129`) will send `tool_choice=required` (or DeepSeek `auto` at temp 0.05). Tab B’s `after_tool_batch` appends to Tab A’s `_action_ir` (`agent_tool_batch.py:541-543`). Tab B’s lean L1 can apply Tab A’s L3 shadow/caps via `runtime._turn_tier`. Session id / LLM bind / workspace were moved to ContextVars; these flags were not.
- Suggestion: Put the remaining flags on `TurnState` or new ContextVars (same pattern as `_turn_build_verify_green`). Stop writing `runtime._force_tool_choice`. Read tier/IR only from turn-local state.
- Status: open

### Issue 2 -- Severity: bug
- File: `src/remedy/core/react_loop/loop.py:370-383`; `src/remedy/core/agent_react_preamble.py:510-520`
- Description: `begin_build_turn` runs **after** `prepare_turn_preamble`. The preamble is the only consumer of `_build_protocol_pending` / `_frontier_continue_pending`. So this turn’s protocol is left on the process singleton and injected into **whichever session starts next**. Session A’s “RESEARCH → PLAN → BUILD” block (or frontier-continue ledger) can appear as a system message on Session B’s greeting. Same leak for `_pending_verify_remedy` (`preamble.py:366-369`, `agent_post_turn.py:90`).
- Suggestion: Inject the protocol in the same `call_llm_stream` after `begin_build_turn` (append to `messages` locally). Never store it on `runtime`. If a next-turn stash is required, key it by session id.
- Status: open

### Issue 3 -- Severity: bug
- File: `src/remedy/core/agent_computer_tools.py:45-60`; `src/remedy/core/computer/executor.py:68-81`, `:372`, `:387`, `:921`
- Description: Computer tools are `async def` but call `ComputerExecutor.run()` synchronously. `run()` uses `time.sleep` for wait/settle (e.g. `:372`). That blocks the whole asyncio loop: sibling SSE streams, abort checks, messenger, and the idle self-inject task all freeze for the duration of a click/wait/screenshot. Two tabs cannot make progress while one is in `computer_wait`.
- Suggestion: `await asyncio.to_thread(ex.run, ...)` (copy context so ContextVars survive) or make wait/poll async. Keep `_active_session_id` as a local in `run()`, not an instance field.
- Status: open

### Issue 4 -- Severity: bug
- File: `src/remedy/core/computer/executor.py:31`, `:81`; `src/remedy/core/computer/host_bridge.py:297-300`, `:410-414`
- Description: The executor is a process singleton (`get_computer_executor`, `executor.py:1576-1581`). `_active_session_id` is overwritten at the start of every `run()`. Enqueue/cancel use that field (`:53-66`). Today the event loop serializes `run()` (Issue 3), so the race is latent. `_last_navigate_url` / `_last_navigate_at` / `_last_navigate_optimistic` are still process-global (unlike `last_drive_target`, which is session-keyed). Tab A’s navigate settle can make Tab B skip or wait incorrectly.
- Suggestion: Pass `session_id` as a `run()` local through `_enqueue`. Move navigate-settle state into the existing `_last_*_by_session` maps.
- Status: open

### Issue 5 -- Severity: bug
- File: `src/remedy/core/react_loop/loop.py:723-742`; `src/remedy/core/react_loop/stream_consume.py:29-75`
- Description: Cooperative abort is checked only at the top of each ReAct step. `consume_llm_http_response` never calls `is_turn_aborted()`. Default sock_read is 900s (`loop.py:202`); SSE idle timeout is 180–900s (`stream_consume.py:21-26`). If the desktop calls `POST /abort` but keeps the SSE socket open, Stop waits until the provider round ends. Client disconnect is fine (CancelledError persist — already fixed). Abort does kill shell children and CUA jobs (`turn_context.py:368-377`).
- Suggestion: Race `is_turn_aborted()` (or `current_abort_event().wait()`) against each SSE read / HTTP wait. Cancel the aiohttp response when the Event is set.
- Status: open

### Issue 6 -- Severity: bug
- File: `src/remedy/gateway/session_bridge.py:256-316`
- Description: Messenger path claims the stream and iterates `stream_response`, but `except Exception` does not catch `CancelledError`. On cancel, `finally` releases the claim, then the exception propagates **past** the persist at `:309-316`. Desktop SSE persist-on-cancel is fixed; Telegram/Discord/etc. can still lose the assistant row (user message already persisted at `:249`).
- Suggestion: Mirror `stream.py`’s `except asyncio.CancelledError` persist + `abort_session(epoch=...)` before re-raise.
- Status: open

### Issue 7 -- Severity: suggestion
- File: `src/remedy/core/self_inject.py:511-520`; `src/remedy/core/self_inject_draft.py:1-8`; `src/remedy/interfaces/api.py:216-264`
- Description: Unattended self-inject defaults **on** (`config self_inject.enabled` default `True`; only `REMEDY_SELF_INJECT=0` disables). After 300s with no user turn, the serve process may draft/edit the source checkout (`run_unattended_draft`) or `ruff --fix` + apply (`_maybe_ruff_self_heal`). Guards are real (path jail, file/diff caps, test gate, git snapshot rollback, no commit/push). Residual risk: it mutates the running product tree without an explicit owner prompt; `git reset --hard` + re-apply can surprise a dirty-but-snapshot-captured tree; sidecar restart marker can bounce the API mid-session.
- Suggestion: Default `enabled=False` for packaged installs; require an explicit Settings opt-in. Keep the gate/ledger. Never run while `_stream_claims` is non-empty (draft already skips `__self_improve__` streams, not other claims).
- Status: open

### Issue 8 -- Severity: suggestion
- File: `src/remedy/core/react_loop/loop.py:91-3390`; `src/remedy/core/react_policy.py:1-1913`
- Description: The stream loop is still one ~3391-line function with dozens of local counters that overlap `TurnState` (`zero_tool_drive_count`, `agency_rearm_count`, `force_answer_sticky`, `mono_fp_hits`, …). Policy is a second god-file of regex + nudge strings. Extraction already started (`react_loop/{binding,build_request,recovery,stream_consume,tool_batch}`, `react_turn.py`) but the main `for step in range(max_total)` body was not moved. Safe evolution is possible only if new flags go on `TurnState`/ContextVars (Issue 1), not `runtime.*`.
- Suggestion: Next split: (1) step prelude (abort/epoch/force-answer), (2) HTTP+consume, (3) tool-batch+recovery. Cap `REACT_MAX_TOTAL_STEPS` default or add a wall-clock budget — 10_000 model rounds (`react_policy.py:228-230`) is an unbounded token burn if unfinished-work / open-drive keep re-arming.
- Status: open

### Issue 9 -- Severity: nit
- File: `src/remedy/core/agent.py:114-116`, `:1143-1153`; `src/remedy/core/agent.py:1034-1036`
- Description: `_llm_turn_lock` comment says it serializes “LLM bind + stream”; it only covers the bind/workspace snapshot (`:997-1031`). That is the correct scope — the comment is stale. `_streaming` setter still `clear()`s **all** sessions (`:1148-1153`); only tests write it. `stream_response` still assigns `self._last_auto_checkpoint_n = 0` and `self._build_verify_green = False` on the singleton (`:1034-1036`) even though ContextVar helpers exist (`turn_context.py:434-462`).
- Suggestion: Fix the comment; delete the legacy `_streaming` setter; use only `set_turn_*` helpers.
- Status: open

## Maintainability notes (not defects)

**God-file / split debt.** `agent.py` is a thin orchestrator now (good). The remaining mass is `react_loop/loop.py` + `react_policy.py` + `computer/executor.py` (~1570 lines) + ~20 `build_*.py` modules. Dual-implementation fear is mostly outdated: `agent_react_loop.py` is a re-export; `react_turn.py` is helpers; `react_loop/tool_batch.py` is post-batch bookkeeping, not a second executor.

**Session isolation.** Done well: stream claim/epoch, ContextVar session/workspace/brief/partner/plan/tools, per-session LLM bind, per-session build map (`build_engine.py:936-948`), per-session evidence/governor/crystal, per-session CUA cancel. Half-done: Issue 1–2 flags, computer navigate settle, skill genome / CUA macros (process-global by design). `reconfigure_llm` still mutates process-wide `_llm_*`; in-flight turns keep their ContextVar bind, new turns after Settings save pick up the change.

**Metabolism density.** L0 (`metabolism/l0.py`, gated in `stream_response` and again in the loop) is a real fast path. Tier classification changes tool-result caps, shadow rehearsal, force-spread, and inject budget — real. Evidence ledger + after_tool_batch are real and session-keyed. Organism pulse / soul stance lines are prompt theater on top of that. `forge/` and `immune/` packages add import surface without logic. Feature is maintainable if new organs stay behind `begin_turn_metabolism` / `after_tool_batch` and do not grow more `runtime._*` flags.

**Nanoswarm.** Not unused theater, not a second agent. On the hot path: NanoToken (window + compressor), `tool_step` → pattern/goal, skill rank cache, provider_health. `SwarmEvent.message_added` (router + memory nanobots) is **not** dispatched from production — only tests and the classify API. Pack/scout contribute optional `remedy_system` hints via `context_snapshot.py:354-394`. Treat the swarm as telemetry + token accounting, not as a control plane.

**Memory / harness.** `MemoryStore` (WAL + RLock) and partner-memory injects are the durable core. Harness send_policy is the real context manager. Soul Field is a persistence/inject layer (personhood across provider switches), not a separate runtime.

**Self-inject.** Safety design is serious (jail, two-pass PR guard, test gate, snapshot rollback, ledger). The product risk is default-on unattended writes to a source checkout (Issue 7), not a missing gate.

**Can this be evolved safely?** Yes, the same way the last isolation pass was done: one flag family at a time onto `TurnState`/ContextVars, with `tests/test_stream_concurrency.py` / `test_turn_context.py` as the contract. Do **not** start a third loop. Do **not** add organs that write `runtime._*`. Mid-stream abort (Issue 5) and unblocking computer I/O (Issue 3) are the two changes that most improve Stop / multi-tab behavior without a rewrite.
