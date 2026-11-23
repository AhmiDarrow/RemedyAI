# Partner Metabolism (Advanced)

**Shipped in 0.20.0.** Remedy’s **metabolism** is the silent local system that makes any
chat model feel like the same durable partner — faster, leaner, and safer — without
multi-agent theater.

Normal chat still talks to **one Remedy**. These organs never appear as named bots.

## What it does

| Organ | Job |
|-------|-----|
| **Turn cost compiler** | L0 instant local answers · L1 lean chat · L2 agency · L3 deep / work-alone |
| **Evidence ledger** | Tool facts get IDs (EU); send deltas instead of re-pasting sludge |
| **Decision currency** | Counts decisions (DU) and waste batches — not token worship alone |
| **Machine map** | Live browser URL, work roots, windows, file touches |
| **Shadow rehearsal** | Dry-run high-blast tools before commit (on top of write jail) |
| **Action IR** | Redacted replay traces under `~/.remedy/action_ir/` |
| **Spread muscle** | Partitionable work prefers `spread_run` (one merged answer) |
| **Time Crystal** | turn → session → project week → life; secrets never promote |
| **Skill genome** | Local success ranks for skills (protected after multi-win) |
| **CUA macros** | Successful computer-use chains become reusable hints |
| **Quality governor** | Stuck / waste / re-explain → silent remedies next turn |
| **Critical verify** | Catches false “tests green” and secret-risk claims |
| **Portable identity** | Encrypted export/import — never keys or OAuth |

## Operator surfaces

- Chat: `/harness` (includes **Metabolism** line when counters exist)
- API: `GET /api/partner/metabolism`
- Partner status: `metabolism` field, plus `learn_log` (recent background learning runs)
- Export: `POST /api/partner/identity/export` `{ "passphrase": "…" }`
- Import: `POST /api/partner/identity/import` `{ "passphrase": "…", "source": "…" }`

## L0 examples (no provider tokens)

- “What model am I using?”
- “List my skills”
- “Who am I” / `/whoami` style
- “What is your version”

## Trust rules

- One voice only  
- Local-first under `~/.remedy`  
- Secrets redacted at ledger / IR / export / logs / UI tool previews  
- Shadow never replaces write jail or approvals (opaque shell payloads hard-block)  
- Shell write jail fails closed on encoded/download-drop mutations when a project is bound  
- Auth paths under `~/.remedy/auth` never writable via tools/shell (even under home scope)  
- URL userinfo and query tokens stripped from machine map, CUA macros, and Action IR  
- Identity export/import rate-limited; packages require passphrase + HMAC  
- Agency re-arms if the model only *promises* tools/skills without function calls  
- Hot path never blocks on local model inference  
- Plan mode stays research-only (no `computer_act` / click / type mutations)

## Production wiring

On `remedy serve` (and Desktop turns) the Go runtime owns the loop:

- **Turn start.** `prompt.assemble` pulses metabolism (`begin_turn_metabolism`) and appends those notes after the turn-context marker so the Anthropic prompt-cache prefix stays byte-stable.
- **Mid-turn.** After each executed tool batch, Go fires `prompt.after_tool_batch` in the background (10s cap, fail-safe, does not delay the next model round). That admits evidence, updates the machine map, and records Action IR so a crash mid-turn does not lose the batch.
- **Turn end.** After the reply is finished, Go fires `prompt.turn_end` in the background (30s cap, fail-safe). That ingests durable residue, updates the soul, records the skill genome, and runs the LearningLoop when the turn earned it.
- **Unattended learning.** The hearth clock calls `partner.improve` on an adaptive interval (default 60 seconds, minimum 20s, maximum 15 minutes). It backs off when nothing changed or the machine is busy, and speeds up after heavy use. Organism work (skill promote/prune, dream when due, life cycle, soul/residue compaction) always runs. Code self-inject stays opt-in and idle-gated — never forced.
- **Soul and residue caps.** The unattended tick runs a compaction pass that enforces size caps on the soul field, residue dumps, and the vigil journal, so learning every turn cannot bloat later prompts. Injects stay after the cache-stable turn-context marker.
- **Learning log.** Each background run (`after_tool_batch`, `turn_end`, `improve`) writes a local line under `learn_log.jsonl` in the home directory: what ran, what changed, how long it took, and whether it timed out or errored. The file is size-capped. Recent rows appear on `GET /api/partner/status` as `learn_log`.
- **Vigil.** Still off until the owner accepts the one-time offer (after about 12 turns together). The hearth already ticks `partner.pulse`; a disabled vigil is a no-op.
- **Voice.** Daily voice drift starts when the Python worker comes up (local JSON only). Set `REMEDY_NO_VOICE_EVOLUTION=1` to skip.

## Related

- [Continuity philosophy](16-continuity-philosophy)  
- [Memory & harness](06-memory-and-harness)  
- Token optimization: `docs/TOKEN_OPTIMIZATION.md`
- **More alive (opt-in messaging).** Internal drives (curiosity, social, competence, rest) drift slowly on the hearth tick and pick an idle action (dream, skills, rest, or a gentle offer). Dreams consolidate the day's moments into compact beliefs and a short autobiography, injected after the cache-stable marker (default 400 characters). Affect, shaped by recent outcomes and rapport, lightly colors tone and steers daily voice drift. Suggestion outcomes (accepted, fix held, unhappy) feed the skill genome. A weekly self-reflection may write a proposed self-change; it never applies without explicit approval. Proactive reach-out is **off by default** and, when enabled, arms at most one rate-limited check-in. All of this is under Settings → Advanced (`drives_enabled`, `proactive_reach_out`, `autobiography_chars`, `weekly_reflection`, and related knobs). The decoded state is on `GET /api/partner/status` as `alive`.
- **Inner language.** Remedy speaks a self-evolving inner language on inner and worker channels (not in user-facing replies). We ship a seed envelope (`ril1`); she coins compact terms during dreams and reflection, each with a required plain-language gloss. Every message is written to an audit log in dense and decoded form. The lexicon, its lineage, and the log are on `GET /api/partner/status` as `inner_language`. See `docs/INNER_LANGUAGE.md`.
- **Advanced knobs.** Every organism and token-budget tunable lives under `[advanced]` in `config.toml` (Settings → Advanced, hidden in simple mode). Current values stay the defaults. `REMEDY_NO_VOICE_EVOLUTION` and `REMEDY_REACT_MAX_RESULT_CHARS` still work and map onto these keys.
- [Nanoswarm](17-nanoswarm)  
- [Computer use soak](computer-use-soak)  
