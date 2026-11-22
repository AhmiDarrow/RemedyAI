# Token optimization

How Remedy keeps turns cheap without shrinking what she remembers. Budgets
are defaults here; advanced knobs (a follow-up) can override them. Injects
always land *after* the cache-stable turn-context marker so a changing
suffix cannot bust the prompt-cache prefix.

## What we adopted, and why

**Stable cache prefixes.** Anthropic's prompt-caching docs are clear: the
cache is a prefix hash, and a single changing byte at the front (a
timestamp, a per-turn note, a reordered tool) invalidates everything after
it. Render order is tools, then system, then messages. We keep the system
prefix byte-stable and put metabolism notes, soul, residue, and any
self-model lines after the turn-context marker. We do not put clocks or
turn ids in the cached prefix.

Sources:

- https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- https://claude.com/resources/articles/reducing-cost-and-improving-performance-with-claude-platform

**Trim tool output before summarizing.** Compaction is several cheap
operations, not one lossy model call. Drop or bound old tool results first;
keep a recent tail verbatim; only then fold older turns. Anthropic's
context editing and the March 2026 cookbook make the same split: clearing
old `tool_result` blocks is safer and cheaper than summarizing the whole
transcript. OpenAI's `ToolOutputTrimmer` keeps the last few user turns at
full fidelity and trims older bulky tool output. Remedy already caps each
tool result for the model (`MaxResultChars`, default 24,000) and elides
middle observations when the transcript is over budget. The unattended
tick now also caps on-disk soul and residue so those injects cannot grow
without bound.

Sources:

- https://platform.claude.com/docs/en/build-with-claude/context-editing
- https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- https://openai.github.io/openai-agents-python/ref/extensions/tool_output_trimmer/
- https://arxiv.org/abs/2610.25516 (Bud ContextCompact 2.0, Oct 2026: reserve a reply budget, compact early, do not wait for overflow)

**Selective memory, not more memory.** Nemori (ACL 2026) and SEDM argue
that what deserves to stay is a question of future utility and token cost,
not "store everything." We already keep durable residue only (short
facts, no chat sludge) and evict soul episodes by salience. The compact
pass on the improve tick enforces a hard byte cap as a last resort. Injects
stay short (`DEFAULT_SOUL_CHARS` is 1,400).

Sources:

- https://aclanthology.org/2026.acl-long.1607/
- https://arxiv.org/abs/2509.09498

**Skip low-value work.** RACE (Oct 2026) shows that not every turn needs
fresh reasoning. We already run the LearningLoop only when the turn earned
it. The mid-turn hook updates local ledgers; it does not rewrite the
prompt mid-round. The unattended improve tick now backs off when nothing
changed or the machine is busy, and speeds up after heavy use, with a
minimum of 20s and a maximum of 15 minutes (default 60s).

Sources:

- https://arxiv.org/abs/2610.12061

## What we did not take

We did not add a second model call to summarize the transcript on the
critical path. Bud ContextCompact's own result is that a slow summary can
erase the accuracy gain under a tight time budget. We prefer free
trimming and size caps first. Server-side Anthropic compaction remains
available when the provider already does it; we do not duplicate it in
the prefix.

## Budgets (defaults)

| Budget | Default | Where |
|--------|---------|-------|
| Soul inject | 1,400 characters | `DEFAULT_SOUL_CHARS` |
| Tool result kept for the model | 24,000 characters | `MaxResultChars` / `REMEDY_REACT_MAX_RESULT_CHARS` |
| On-disk soul field | 64 KiB | `compact_personhood` |
| On-disk residue / journal tails | 256 KiB / 64 KiB | `compact_personhood` |
| Learn log | 256 KiB, 400 lines | `learn_log` |
| Improve tick | 60s, min 20s, max 15m | hearth adaptive clock |
| Mid-turn learn cap | 10s | `prompt.after_tool_batch` |
| Turn-end learn cap | 30s | `prompt.turn_end` |
