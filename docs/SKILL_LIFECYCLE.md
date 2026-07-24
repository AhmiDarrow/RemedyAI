# Skill lifecycle protocol

Remedy’s learning loop turns successful work into reusable skills — but a **lucky
one-off must not become gospel**, and **hard-won multi-attempt solutions must not
be thrown away**.

Skills follow the [agentskills.io](https://agentskills.io) format with **progressive
disclosure**: the model sees a ranked catalog (name + description + status); full
`SKILL.md` bodies load only via `skill_activate`.

## Stages

| Status | Meaning |
|--------|---------|
| `discovered` | Candidate from one good trace; probation |
| `validated` | Static checks OK + early re-use looking good |
| `active` | Multi-session proof; preferred for routing |
| `disabled` | Failing in the field; kept for audit / revive |
| `deprecated` | Soft-removed; prune candidate |

**Never** jump to `active` from a single generation.

Curated bundled skills load as `active`. Auto-generated skills keep their
frontmatter status (probation). Imported packs enter **quarantine** (`discovered`
+ `metadata.quarantine`) until the user trusts them.

## Progressive disclosure

1. **Discovery** — system prompt gets a ranked catalog (`summary_lines` / `match_skills`).
2. **Activation** — tool `skill_activate(skill=…)` injects the full procedure (body capped).
3. **Execution** — follow instructions; optional `skill_run(skill=…)` for `scripts/`
   (requires approval in ask mode; **blocked while quarantined**).

Also available: `skill_search` (rank by query), composition hints (related skills).

## Security notes (0.10.33+)

- Imported packs start quarantined — `skill_run` refuses until Trust/Activate.
- ZIP import is Zip-Slip safe (paths must stay under extract root).
- Local HTTP API uses a Bearer token by default (desktop auto-loads it).

## Effort weight (hard-won knowledge)

When the agent tries many approaches (failed tools, recoveries, many tools,
long duration), we compute an **effort score** (0–1):

| Band | Typical signal |
|------|----------------|
| trivial / low | Clean 3-step path |
| medium | Some fails + switches |
| high (**hard-won**) | Several failed attempts + recoveries + diversity |

### Creation

- **Easy** traces: need high step success rate (~75%+) and/or clear patterns.
- **Hard-won** traces: may have lower step success (many fails *before* the
  working path) but **must still finish successfully overall**.
- Hard-won skills get tags `hard-won` / `high-effort` and metadata
  `effort_weight`, `effort_reasons`.
- Descriptions are **trigger-oriented** (“Use when…”) so ranking/activation works.

### Evolution (protect investment)

| Action | Easy skill | Hard-won skill |
|--------|------------|----------------|
| Promote to ACTIVE | n≥5, rate≥80%, ≥2 sessions | slightly easier n, same quality bar |
| Demote | 5 runs + rate&lt;50% or 3-fail streak | **higher** n and **longer** fail streak |
| Prune (zero success) | after ~3 hopeless runs | needs **more** failed reuses |
| Prune stale DISABLED | ~30 days | **~90 days**; prior successes extend hold |

So: five different ways to crack a problem → keep that skill, demand strong
evidence before demoting or deleting it.

### Merge

If a new trace proposes the same skill name, Remedy **merges** recovery notes and
tools into the existing skill (patch version bump) instead of spawning duplicates.

## Closed loop (runtime)

After multi-step successful tool turns, the agent may **auto-learn** a probation
skill (`LearningLoop.learn_from_tool_steps`). Skill activations and `skill_run`
results call `record_skill_feedback` + `auto_refine_skill`. Stats persist to
`~/.remedy/skill_stats.json` so promote/demote survives restarts.

## Ranking

`SkillRegistry.match_skills(query)` scores:

- status (ACTIVE &gt; VALIDATED &gt; DISCOVERED)
- description / name / tag token overlap
- effort weight (hard-won boost)
- success rate metadata
- workspace path hints
- session activation counts

## API surface

```python
from remedy.core.learning_loop import LearningLoop
from remedy.core.learning.lifecycle import compute_effort_score, SkillLifecyclePolicy
from remedy.skills.registry import SkillRegistry

reg = SkillRegistry()
reg.discover_defaults()
reg.match_skills("git recover", limit=5)
reg.skill_body("my-skill")  # progressive disclosure stage 2

loop = LearningLoop(skills_dir=..., memory=..., registry=reg)
skill = loop.learn_from_trace(trace)          # gates + probation write / merge
loop.record_skill_feedback(name, success=...)
loop.auto_refine_skill(skill)                 # promote / demote / deprecate
loop.prune_skill(skill, remove_files=False)
print(loop.last_lifecycle_decision)
```

### HTTP

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/skills` | List (optional `?q=` rank) |
| GET | `/api/skills/{name}` | Detail + body |
| POST | `/api/skills/{name}/status` | Set status (`active` / `disabled` / …) |
| POST | `/api/skills/{name}/feedback` | Success/fail → refine |
| POST | `/api/skills/export` | ZIP skill pack |
| POST | `/api/skills/import` | Import ZIP under **quarantine** |

## Desktop

**Skills panel**: status chips, hard-won badge, search, Activate / Disable / Trust
(quarantine), success/fail feedback buttons.

## Files

| Module | Role |
|--------|------|
| `core/learning/lifecycle.py` | Effort score + accept/promote/demote/prune policy |
| `core/learning/reflection.py` | Trace → candidate (+ failure protocol, trigger descriptions) |
| `core/learning/refiner.py` | Execution stats (durable JSON) + failure streaks |
| `core/learning_loop.py` | Orchestration + merge + disk write |
| `skills/registry.py` | Discover, rank, catalog, activate body |
| `skills/exporter.py` | Pack export + quarantine import |
| `skills/executor.py` | Script sandbox for `skill_run` |

## Tests

- `tests/test_skill_lifecycle.py` — effort bands, hard-won accept, prune resistance
- `tests/test_skills_system_v2.py` — ranking, durable stats, merge, quarantine, auto-learn
