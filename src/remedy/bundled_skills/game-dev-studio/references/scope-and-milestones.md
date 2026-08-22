# Scope and milestones

## The rule
One mechanic deep beats five shallow. A slice with a single verb that feels
good is a game; five verbs that each half-work is a tech demo. Every extra
system multiplies bugs, tuning time and playtest confusion.

## Milestone shapes (solo/duo with an agent)

| Milestone | Rough effort | Exit criteria |
|-----------|--------------|---------------|
| 0 Loop on paper | 1 session | GDD §1–4 with numbers; owner said "yes, that" |
| 1 Slice | 2–6 sessions | one level, one mechanic, win/lose/restart, rectangles, smoke green, owner played twice |
| 2 Feel | 2–4 sessions | juice checklist done; owner says "it feels good" without prompting |
| 3 Content | open-ended | N units; save/load; menus; first real art; still 60 fps |
| 4 Ship | 1–3 sessions | release export on each target runs from a clean machine/folder |

A session = one focused block with the owner present for the playtest at
the end. If a milestone doubles its estimate, cut inside it; do not push
the date.

## What "finished slice" means
- Boots to the level in under 2 seconds.
- All inputs in GDD §3 do something visible within 100 ms.
- Dying and restarting takes under 1 second; no menu in between.
- A stranger can reach the goal in under 3 minutes with no explanation.
- Zero `SCRIPT ERROR` in a 3-minute run.
- No feature outside GDD §3–4 is in the build.

## Cutting
When something must go, cut in this order: meta progression → extra
enemies/items → secondary mechanics → visual polish → menus. Never cut:
restart, lose condition, the verify script, the save of the GDD.

Cut list entries must say *why* and *when to revisit*. "Later" is not a
reason; "after slice playtest #2 shows players want more verbs" is.

## Scope smells (act on the first one you see)
- A second player-facing verb before the first has been playtested.
- An inventory, crafting or dialogue system in a game whose loop is 10 s.
- Procedural generation before one hand-made level is fun.
- Multiplayer of any kind in milestone 1–2.
- "Just a small menu" that grows settings, profiles and achievements.
- Art generation before milestone 2 is done (rectangles are fine).
- Refactors that do not unblock a milestone item.

## Estimating honestly
Tell the owner the estimate and the confidence. Triple anything involving
export pipelines, save migration, gamepad + keyboard parity, or "make it
feel like <famous game>". Feel is iteration count, not code size.

## When the owner wants more
Acknowledge, write it into the cut list with the revisit condition, and
ask the one question: "Does this replace <current item> or wait for it?"
Then continue the current milestone.
