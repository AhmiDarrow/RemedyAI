# Memory context — how it works today & a machine-native middleman

**Status:** analysis + **integrated into the live loop**
**Date:** 2026-08-04

---

## 1. How in-app memory works today

Memory is layered and already sophisticated, but it is **prose-centric**: almost
everything the model ultimately reads is *narrated back* (summaries, a brief,
pruned history). Machine structure is discarded at the boundary.

| Layer | What it is | How it reaches the model |
|-------|-----------|--------------------------|
| **SQLite + FTS5** (`memory/store.py`) | persistent `memory_entries`, handoff notes, session summaries; full-text search | `memory_search` tool + cross-session lookups |
| **SessionBrief** (`harness/brief.py`) | live structured state — intent, decisions, open_tasks, blockers, key_paths, next_steps, history_thread | injected as a block **every turn** |
| **Memory Harness** (`harness/send_policy.py`, `compressor.py`, `pruner.py`, `local_brief.py`) | prunes history to a token budget; compresses → brief pointer; **content-addressed offload** of fat tool bodies (SHA-256 → disk handle); background local-model brief updates | compressed/offloaded send-view each turn |
| **Nanoswarm** (`nanoswarm/`) | token budget, fill%, pattern/goal/health memory nanobots | gates when the harness acts |
| **Memory tools** | `memory_save`, `memory_search`, `compress_context` | explicit calls |

**The structural gap.** `harness/offload.py` already proves the machine-native
pattern: a fat tool body is hashed (SHA-256), written to disk, and the context gets
a **cheap handle** — the full content is pulled back only on demand. But this is
used only for oversized *tool output*. The rest of memory (notes, brief, decisions)
still goes through lossy prose summarization. The model "remembers" by reading
sentences, and the structured truth — file paths, symbol names, exit codes, diffs,
content hashes — is flattened and thrown away the moment it is summarized.

## 2. Why prose-summarization is the wrong default (and why it hurts small models)

- **Lossy + human-mimicking.** Summaries are an imitation of human note-taking.
  They discard exactly the structured signal a coding agent needs.
- **No locality.** Retrieval is mostly recency/fill-gated, not *what the current
  turn is about*. A turn about `token_nanobot.py` still re-injects the whole brief.
- **Budget-hostile.** A 4k–8k local window cannot afford a full brief + history +
  a summary pointer at once. Compression "after the fact" has nothing to work with
  once the head + current turn + one tool result already exceed the window.
- **Ambiguous handles.** Offload handles today are human-readable hints; there is
  no uniform, resolvable, content-addressed handle the model can trade for the real
  artifact.

## 3. Machine-native memory concepts (no human metaphors)

Aiming at *compute* ideas, not memory-palace / spaced-repetition / hippocampus style:

1. **Content-addressed store (CAS).** Every fact/artifact is an immutable object
   keyed by its own SHA-256. Dedup is free, equality is O(1), and a handle is a
   cryptographically stable pointer. (Already proven in `offload.py`; generalize it.)
2. **Event-sourcing / append-only action log.** Record every action (tool call,
   file read, decision, diff) as a compact structured event. The context never sees
   the raw log — it sees a **schema projection** (grouped tallies, latest N, per-path
   slices). The log is truth; the model is given indices.
3. **Provenance graph (path/tool/session edges), not chronology.** "What do we know
   about *this file*" is answered by following edges, not by scanning time. This is
   the middleman's locality trick.
4. **Inverted token index + BM25 retrieval keyed by the current message.** Instead
   of "most recent memory," ask "which stored units best match the user's current
   message + the open task," and inject only the top-k.
5. **Two-level store: dense hot set + sparse cold set.** A small bounded buffer of
   hot keys stays available; everything else is on disk, retrieved on demand. The
   hot set is sized to the model's window, not to storage.
6. **Budget projection.** A projection function returns exactly the slice that fits
   a token budget, so a 4k model gets a minimal, sufficient block rather than a dump.
7. **Fingerprint/delta memory.** Store deltas and diffs rather than snapshots;
   reconstruct only what's needed.
8. **Lazy handle resolution as a first-class tool.** The model is *trained* (via the
   system prompt) to emit/consume handles; a `memory_resolve(handle)` tool returns
   the full body only when the agent decides it needs it.

## 4. The middleman concept

**"Can Remedy hold information and act as a middleman?" — yes.** The middleman is a
process-local service that sits between the session's raw events and the model's
context window. It does three things on every turn:

```
  events (tool output, file reads, decisions)
        │  ingest + CAS + provenance edges
        ▼
  ┌───────────────────────┐
  │  MIDDLEMAN            │   stores structured truth, never the whole log in context
  │  - content-addressed  │
  │  - provenance graph   │
  │  - inverted index     │
  │  - budget projection  │
  └───────────┬───────────┘
              │  current user message + open task → query
              ▼
        minimal slice of context  (compact, budget-bounded, with handles)
              │
              model → if it needs a body → memory_resolve(handle)
```

The middleman is *not* a summarizer. It is a **queryable, content-addressed working
memory that projects a minimal slice on demand**. Summarization becomes one optional
offline job, not the primary channel.

## 5. Prototype: `remedy/memory/middleman.py`

A self-contained reference implementation (`tests/test_middleman.py`, green) with
the machine-native primitives above:

- `put(body, *, kind, path, tool, session_id)` → SHA-256 content address; idempotent + dedup.
- `search(query, *, paths/tools/session/kinds)` → BM25-lite, provenance-filtered, relevance-ranked.
- `project(query, *, budget_tokens)` → a compact structured block that stops at the
  budget — the exact thing to inject into a small window.
- `get`/`resolve(handle)` → lazy full-body pull (the "middleman returns the artifact
  when asked").
- Provenance indexes let a turn about `token_nanobot.py` fetch only memory that
  touches `token_nanobot.py`.

Verified behaviors: content-addressing dedup; path-filtered locality; retrieval keyed
by query (not recency); budget-bounded projection; lazy handle resolution.

## 6. Integration (landed)

Wired into the live loop at three safe seams (all guarded, non-blocking):

1. **Ingest — tool results** (`core/agent_tool_batch.py`): every finalized tool
   result is `ingest_tool_result(...)` into the session middleman (bounded ~2k
   body; full content stays in the offload store).
2. **Ingest — facts** (`core/agent_memory_tools.py`): `memory_save` also stores
   the fact in the middleman (`kind="fact"`).
3. **Projection** (`core/agent_context.py`): `build_turn_context` appends a
   `Working memory (retrieved by query):` block — `get_session_middleman(sid)
   .project(query, budget=win*0.15, paths=key_paths, session_id)`. Budget-bounded,
   provenance-filtered, and marked droppable by the small-window head trimmer.
4. **Resolve tool** (`memory_resolve(handle)`) registered so the model can lazily
   pull a full body by its `remedy-mm://` handle.

The middleman is process-global per session (`get_session_middleman`), so it is
writable from the tool boundary and read from context assembly.

## 7. How to extend (roadmap)

1. **Ingest side:** at each tool-result boundary, `middleman.put(body, kind="tool",
   path=<file>, tool=<name>)`; at each memory_save, `put(kind="fact")`. Idempotent,
   cheap, non-blocking.
2. **Projection side:** replace/augment the always-on brief block with
   `middleman.project(current_message, budget_tokens=…, paths=<open key_paths>)` so a
   small window gets the relevant slice, not the full brief. Keep the brief for large
   windows; prefer projection for small ones.
3. **Resolve tool:** expose `memory_resolve(handle)` and teach the system prompt to
   emit handles instead of pasting bodies.
4. **Cold store:** back the in-memory maps with SQLite (mirroring `store.py`) so the
   middleman is durable and survives restarts; FTS5 can power the inverted index.
5. **Optional offline summarization:** run the existing `local_brief`/quality jobs as
   a *background compaction* of the event log into higher-level units — never as the
   primary context feed.

The existing `offload.py` (content-addressed handles) and `local_brief.py`
(background local-model jobs) are the natural seams to build on — the middleman
generalizes `offload.py` from "big tool outputs" to "all structured memory."

## 8. Files

- `src/remedy/memory/middleman.py` — the middleman (CAS + provenance + BM25 +
  projection + lazy resolve + session registry)
- `tests/test_middleman.py`, `tests/test_agent_context.py` — green tests
- Integration seams: `core/agent_tool_batch.py`, `core/agent_memory_tools.py`,
  `core/agent_context.py`; existing `memory/harness/{offload,local_brief,brief}.py`
