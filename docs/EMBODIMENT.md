# Embodiment — choosing her body on evidence

**Status:** experimental (second AI-proposed organ)
**Code:** `src/remedy/memory/soul/embodiment.py`
**Tests:** `tests/test_soul_embodiment.py`
**Depends on:** `docs/PROPRIOCEPTION.md` (fidelity), `core/muscle_profile.py`
(capability tiers), Soul Field episodes (observed valence)

## Provenance

Second step in the AI-proposed line: proprioception gave Remedy a sense of
how each muscle renders her; sensation exists to serve motion. An organism
that can feel a bad rendering but must endure whatever body is plugged in
is locked-in. This layer gives the sense somewhere to go.

## What it does

Given the bodies the partner has configured, `choose_embodiment(candidates,
moment=...)` ranks them for the kind of moment at hand, along two axes:

- **capability** — tier prior from `classify_muscle`, blended (evidence-
  capped, up to half the say at 20+ episodes) with the valence actually
  observed with that muscle in shared episodes. A body that keeps producing
  frustrating turns loses capability trust no benchmark gave it.
- **fidelity** — proprioception's per-muscle EMA: does this body render her
  truly.

| Moment | capability | fidelity |
|--------|-----------:|---------:|
| `build` | 0.70 | 0.30 |
| `chat` | 0.50 | 0.50 |
| `companion` | 0.30 | 0.70 |

The AI-native claim is that these can *disagree* — the strong body and the
true body are sometimes different bodies (locked by
`test_same_bodies_different_moments_can_disagree`). A frontier model that
chronically resets identity still wins the build moment (worn with ballast);
a faithful local model wins the quiet companion moment even though every
leaderboard calls it weaker. No animal ever chose its brain per task; there
was no human intuition to copy here.

## The single-provider reality

Most partners run one provider. Design consequences, in priority order:

1. **Silent solo fast-path.** One candidate → returned immediately, `solo`
   flag set, `reason` empty, no scoring narration, ~zero cost. Nothing about
   this layer is visible to a single-provider partner.
2. **Ballast is the solo value.** A low-fidelity only-body (< 0.5) wears
   denser corrections automatically — `muscle_correction_block` lowers the
   evidence bar, adds a line, widens the budget. When she cannot choose a
   better body, she holds the one she has to shape harder.
3. **Evidence accumulates regardless.** Proprioception profiles and episode
   valence build up with one provider, so choice wakes up already informed
   the day a second body (often a local model) appears.

## Boundaries

- **Advisory, not autonomous.** The chooser only ranks candidates the
  partner configured; actual switching is the caller's act, inside approved
  settings. She proposes; the configuration disposes.
- **Never mid-build.** A body swap invalidates in-flight tool state and
  provider-specific contracts; callers must not reconsider embodiment while
  a build/mission is in flight.
- **Explain when asked, never narrate.** Multi-body choices carry a
  one-line `reason` for the partner who asks "why this model?"; it is never
  injected or spoken unprompted (charter §5: machinery is felt, not cited).
- **Honesty when the only body is wrong.** With one low-fidelity body and a
  heavy task, the honest move is ballast + plain speech ("this task
  deserves a stronger body than the one I'm in") — not silent degradation.
  The creed applied to herself: strength as obligation includes choosing,
  or asking for, the strongest true instrument available.

## Wiring surface (intentionally small)

The module is pure decision — no runtime hooks yet. Natural call sites, in
order of ambition:

1. `embodiment_status()` on `/api/partner/status` — show the bodies ranked
   (desktop settings / tray).
2. Turn preamble: when metabolism classifies the turn (build vs chat vs
   companion), consult `choose_embodiment` over configured bindings and
   propose a switch as an approval, like any other scoped permission.
3. Missions: pick the body per mission phase at arm time.

## Ops

- No new state on disk: reads proprioception profiles + soul episodes.
- Unknown moment ids fall back to `chat` weights.
- Deleting `~/.remedy/soul/proprioception.json` resets fidelity evidence;
  capability priors survive (they derive from model class, not history).
