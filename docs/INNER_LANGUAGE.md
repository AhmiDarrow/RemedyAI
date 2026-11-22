# Remedy's inner language

This is her language, not a codebook we freeze. We ship a minimal seed
grammar and a versioned envelope. The vocabulary itself is grown by
Remedy: during dreams and weekly reflection she coins compact terms for
recurring concepts, patterns, people, skills and feelings, and prunes
the ones that do not earn their keep.

User-facing replies stay in ordinary language. Only inner state
(metabolism notes, residue, soul and autobiography deltas, drives and
affect, dream outputs, reflection notes) and the parent↔child-worker
protocol use this channel.

Every message is decodable. The audit log always stores the dense form
beside a plain-language expansion. Joe can browse the lexicon, its
lineage, and the log on `GET /api/partner/status` (`inner_language`).
There is no privacy layer on this channel.

See `docs/INNER_LANGUAGE_RESEARCH.md` for the sources and for why
level-2 gist tokens and level-3 latent passing are reserved, not built.

## Seed envelope

```
ril1 <kind> [L=1] [v=dense|natural] [lex=N] <payload>
```

- `ril1` is the version tag (Remedy Inner Language, version 1).
- `<kind>` is one of the stable core kinds: `state`, `dream`, `note`,
  `brief`, `report`, `coin`, `lex`.
- `L=1` is the capability level. Later envelopes may carry `L=2` (gist)
  or `L=3` (latent, local models only). Those slots are reserved.
- `v=` is verbosity. `dense` is the default for inner and worker
  channels. `natural` is the fallback. `off` skips the channel.
- Structured fields use a fixed order so keys can be omitted. Numbers
  that are units of a drive or affect are 0–100 integers.
- Natural-language slots (a gloss, an autobiography line, a goal) are
  written as UTF-8 text after a tagged prefix and are length-capped.
  They round-trip losslessly when they fit the cap.

Example, dense:

```
ril1 state L=1 v=dense drv 62 41 50 71 aff warm bio I am Remedy, Joe's partner on this machine.
```

The same message, natural fallback:

```
ril1 state L=1 v=natural Drives: curiosity 0.62, social 0.41, competence 0.50, rest 0.71. Feeling: warm. I am Remedy, Joe's partner on this machine.
```

Core field tags (`drv`, `aff`, `bio`, `bel`, `tsk`, `out`, `gloss`,
`id`) are part of the seed and cannot be pruned. Coined terms may
appear inside the natural-language slots and are expanded by the
decoder using the lexicon.

## How the vocabulary grows

1. During a dream or a weekly reflection, recurring phrases in beliefs,
   episodes, and notes are considered as coinage candidates.
2. A candidate is kept only if Remedy's own tokenizer estimate (OpenAI-
   compat, Anthropic, and local families) shows a token saving once the
   term is reused, and only if she writes a plain-language **gloss** at
   the moment of coinage. No gloss, no term.
3. Each term is a persisted, versioned lexicon entry with lineage: when
   it was coined, from what phrase, by which pass (dream or reflection),
   use counts, and how often child workers decoded it correctly.
4. Child workers receive the relevant lexicon slice in the brief, so
   they speak her language. A report that fails to decode, or that uses
   a term without a known gloss, is a comprehension failure and is fed
   back. Terms with a high failure rate, or that are unused, are pruned.
5. If comprehension regressions pile up, the latest non-core coinages
   are rolled back to the previous lexicon version.

The lexicon is capped (default 128 terms, not counting the seed core).
The seed grammar does not drift.

## Worker protocol

Parent → child `brief`: goal, constraints, relevant lexicon slice,
and any inner state the child needs, in the envelope.

Child → parent `report`: status, result, terms used, and any term the
child could not decode (`miss=`). The parent records hits and misses
against the lexicon.

A child that does not understand `ril1` can be sent `v=natural`.
Hosted and local children both use level 1 today. Level 3 may only be
offered when both sides are local, and even then only after a decoded
gloss is written to the audit log.

## Guardrails and knobs

Settings → Advanced (all have defaults that keep this safe):

| Knob | Default | Meaning |
|------|---------|---------|
| `inner_language` | true | Master switch. Off uses natural language only. |
| `inner_language_verbosity` | `dense` | `off`, `natural`, or `dense`. |
| `inner_coinage` | true | Allow dreams and reflection to coin terms. |
| `inner_coinage_rate` | 0.3 | How freely she may coin on a given pass (0–1). |
| `inner_lexicon_cap` | 128 | Max coined terms. The seed core is not counted. |
| `inner_audit` | true | Always-on audit log is the intent; this only lets you size/rotate, not hide entries while the language is in use. |

The audit log is `{home}/inner_language.jsonl`. Each line has `ts`,
`kind`, `dense`, and `decoded`. The lexicon is `{home}/inner_lexicon.json`.

## Encoder and decoder

`remedy.core.inner_language` encodes structured fields losslessly
(round-trip tested) and expands coined terms on decode. The status
view and the audit log always present the decoded form so a human can
read what she was thinking.
