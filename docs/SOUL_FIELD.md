# Soul Field — provider-invariant personhood

**Status:** experimental (0.21.1+)  
**Code:** `src/remedy/memory/soul/`  
**On disk:** `~/.remedy/soul/field.json`

## Thesis

Chat models are **muscle** — interchangeable compute. Remedy’s continuity is a local
**Soul Field**: identity, dyadic relationship state, episode residue, and an organism
self-model. Any provider that animates the field should feel like the *same person*
because the field — not the weights — carries who you are together.

This is intentionally **not** “more RAG.” Facts (Partner Memory) remain one stream.
Personhood is field dynamics: unfinished arcs, rapport, tensions, pledges, and how
you correct each other over time.

```
┌─────────────────────────────────────────────────────────┐
│  Provider muscle (xAI / OpenAI / Anthropic / Ollama…)   │
│         ▲ animates                                       │
│         │                                                │
│  ┌──────┴──────────────────────────────────────────┐    │
│  │ Soul Field (local)                               │    │
│  │  · Identity kernel + vow                         │    │
│  │  · Relational field (rapport, help mode, voice)  │    │
│  │  · Episode residue ring                          │    │
│  │  · Organism lessons (self-inject)                │    │
│  └──────┬──────────────────────────────────────────┘    │
│         │ injects every turn                             │
│  Partner Memory · Session Brief · Time Crystal · …      │
└─────────────────────────────────────────────────────────┘
```

## What gets injected

1. **Muscle/soul contract** — hard rule: do not reset as a new assistant.  
2. **Soul Field block** — bond scores, help mode, open relational threads, tensions,
   pledges, last episode residues, recent self-inject lessons.  
3. Existing Partner Memory / Brief / Crystal (unchanged roles).

## What updates the field

| Source | Effect |
|--------|--------|
| End of turn (`schedule_post_turn_prep`) | Micro-update stance, valence EMA, episode residue, soft habits |
| Explicit pledges / “from now on” | Life-ish pledges + Time Crystal life horizon |
| Self-inject red/green | Organism lessons + self-habits (product improving itself) |
| User corrections | Correction style + temporary trust dip |

No second cloud model is required. Heuristics are cheap and local; later local
enrichers can refine labels without changing the architecture.

## Why this is different

| Usual agent memory | Soul Field |
|--------------------|------------|
| Facts about the user | Dyadic state of *us* |
| Session transcript windows | Compressed episode residue |
| Provider system prompt = identity | Identity survives provider swap |
| Self-improve = code only | Self-improve writes organism memory |

## Privacy

- Lives only under `~/.remedy/soul/` (owner machine).  
- Secret-shaped strings are redacted before residue write.  
- Not exported in portable identity by default until an explicit opt-in is added.

## Builder muscle (capable providers)

When the active provider/model is **mid/frontier** (Grok, Claude, GPT-4/5 class,
DeepSeek-R1, etc.), `muscle_profile` unlocks:

- Higher parallel tool waves (up to 24)
- Builder system contract (explore → implement → verify → recover)
- Denser Soul inject budget
- Intent pack **build** for “implement / ship / scaffold…” phrasing

Tiny/local models stay lean so they do not thrash.

## Dream cycle

`dream_cycle` (tool `soul_dream` or automatic post-turn when ≥4 episodes and
cooldown elapsed) is how the organism *sleeps on it* — three streams:

| Stream | What it holds |
|--------|----------------|
| **Memory of them** | Life, goals, pledges, open threads |
| **Memory of myself** | Help mode, corrections, habits, organism lessons |
| **Dreams of the future** | `Toward {their goal}: {how I will partner}` |

Those dreams inject every turn and can arm a mission. Heuristics always run;
an optional local model may only refine episode labels.

## Tools

| Tool | Purpose |
|------|---------|
| `soul_status` | Bond, residue, muscle tier |
| `soul_recall` | Unified Soul + Crystal + Partner Memory |
| `soul_dream` | Force densification (+ local enrich + mission arm) |
| `soul_arm_missions` | Pledges / open threads → durable missions |
| `soul_export` / `soul_import` | Move personhood between machines |
| `continuity_score` | Bond/episode/pledge scores + self-inject targets |

## Build engine (machine construction)

See also `src/remedy/core/build_engine.py`, `build_oracle.py`, `build_ledger.py`.

| Capability | Behavior |
|------------|----------|
| Auto-verify | After write waves, machine runs stack fingerprint tests |
| Oracle-first | No test command → fail closed (cannot DONE) |
| Ledger | `{project}/.remedy-build/ledger.json` — resume mid-ship |
| Unit hop | `build_unit_hop` structural oracle (reducer hop) |
| Tools | `build_status`, `build_resume`, `build_unit_hop` |

## Life stages (1–5)

1. **Local dream enrich** — optional loopback LLM labels stance/threads  
2. **Somatic signals** — `soma` on `/api/partner/status` + tray tooltip  
3. **Mission × Soul** — auto-arm missions from pledges (every ~8 turns / dream)  
4. **Portable soul** — identity export includes soul; `soul_export`/`import`  
5. **Self-inject continuity** — `focus=auto` targets soul/memory modules when weak  

## Ops

- Delete `~/.remedy/soul/field.json` to reset personhood residue (facts in Partner
  Memory remain).  
- `REMEDY_HOME` relocates the whole home, including soul.
