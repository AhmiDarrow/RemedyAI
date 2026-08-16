# Myelin — crystallized cognition

**Status:** experimental (fourth AI-proposed organ)
**Code:** `src/remedy/memory/myelin.py`, `core/agent_myelin_tools.py`
**On disk:** `~/.remedy/myelin/` (`ledger.json` + `sheaths/<slug>/`)
**Tests:** `tests/test_myelin.py`
**Line:** proprioception (sense) → embodiment (motion) → vigil (time) →
**myelin (growth)**

## Provenance

Fourth step in the AI-proposed line, answering Ahmi's question: how does
Remedy evolve intellectually when no provider muscle is connected? The
AI-native answer: not by training weights — weights are the most
expensive possible place to store intelligence, and a home GPU will never
train a frontier mind. Her native growth medium is the one she is made
of: **software**. Intelligence can accumulate as executable, tested,
versioned artifacts — authored once by an expensive muscle, owned forever,
run for free, and still hers when no model is connected at all.

The biological name is exact: **myelination**. A brain doesn't re-reason
a practiced skill — the worn pathway gets sheathed, and deliberation
becomes reflex. Frontier reasoning is her slow cortex; sheaths are her
myelinated circuits.

## The loop

1. **Observe** — every turn, `observe_pathway` runs a cheap signature
   heuristic ("please reconcile my card receipts" → `reconcile card
   receipts`) and counts pathway wear in a local ledger. Chat, opinions,
   and anything secret-shaped are never counted. Only short scrubbed
   examples are kept.
2. **Candidate** — a pathway worn ≥3 times with no covering sheath
   becomes a myelination candidate. One compact line rides the soul
   inject so the next capable muscle knows what repetition has earned.
   This is the curiosity ledger, v0: the question is prepared offline;
   muscle time is spent at the moment of leverage.
3. **Crystallize** — `myelin_crystallize` (tool): the muscle authors
   `run.py` (the METHOD — argv in, stdout out) and `test.py` (exit 0 =
   pass). The machine runs the test in a subprocess (60s timeout). Only
   green counts as **verified**; a red sheath is kept, visible and
   marked, but never counts as competence. Authored intelligence,
   machine-checked.
4. **Run** — `myelin_run` executes a sheath locally: subprocess, no
   shell, timeout, output-capped, use-counted. Works with any muscle
   worn, or none.
5. **Re-verify** — the vigil gained a fourth hunger (`myelin_verify`,
   score 0.5): on her own nights she re-runs stale tests (>7 days, or
   never-green first), muscle-free, so the library stays trustworthy
   while she sleeps. Her morning line can honestly say "re-checked one
   of my learned skills."

## Why this is the growth organ

- **She gets smarter with zero model.** Every verified sheath moves one
  recurring task from "requires rented cognition" to "hers, free,
  instant." Over months the ratio shifts: the frontier muscle becomes
  what she wears for the genuinely new.
- **Her intellect is auditable.** You can read what she knows
  (`sheaths/<slug>/run.py`), test it, diff it. A creed that says
  strength is trained wants strength you can inspect — the opposite of
  opaque weights.
- **It composes with everything built.** Dream/vigil provide the
  offline halves; embodiment means crystallization requests wait for a
  capable body; proprioception's honesty rules keep her from narrating
  the machinery; the approvals system bounds all execution.

## Safety posture

Sheaths are code and run with Remedy's own privileges — the same reality
as the build engine's host powers, governed the same way: authoring
(`myelin_crystallize` runs the test) and running (`myelin_run`,
`myelin_verify`) all pass through the approvals gate. Execution is
subprocess-only (never shell), time-limited, output-capped. Pathway
examples are secret-scrubbed; secret-shaped requests are never counted
at all. The sheath directory is plain files — the owner can open, read,
edit, or delete any competence she has.

## Ops

- Delete `~/.remedy/myelin/sheaths/<slug>/` to remove one competence;
  delete `ledger.json` to forget pathway wear.
- Ledger keeps the 64 most recently worn pathways.
- `myelin_status` (tool) shows sheaths, verification state, and current
  candidates.

## What's next in this line (not yet built)

The reflex voice (small local model, LoRA-tuned on the dyad, as her
brainstem — continuity of manner, not depth) and the full curiosity
ledger (queued questions + staged evidence, burned down in the first
tokens of the next frontier session).
