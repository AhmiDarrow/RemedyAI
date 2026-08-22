# GDD template (copy to docs/gdd.md)

Keep it under two pages. Anything longer is a wiki nobody reads. Update it
when a decision changes; date the change.

```markdown
# <Working title>

## 1. Fantasy (one paragraph)
You are <who> doing <what> because <why>. The 10-second loop: <verb> →
<feedback> → <reward> → <verb>. You win when <condition>; you lose when
<condition>. Closest reference: <game>, but <one difference>.

## 2. Pillars (max three)
- <Pillar>: one sentence on what it means in play.
- <Pillar>
- <Pillar>

## 3. Core mechanic (the one)
- Input: <keys/buttons>
- Rules: <3–6 bullets, numbers included: speed, jump height, cooldown>
- Feedback: <what the player sees/hears on success and failure>
- Why it is fun with placeholder art: <one sentence, or it is not yet>

## 4. Vertical slice definition
- One level: <size, duration ~2–3 min>
- Hazard/enemy: <one>
- Win: <goal>; Lose: <condition>; Restart: <key>
- Done when: smoke passes + owner plays it twice without confusion

## 5. Progression (after the slice)
- Content units: <level / room / wave / item> — how many for v1
- Difficulty ramp: <what changes between unit 1 and unit N>
- Meta: <none | score | unlocks> — keep none until the loop is fun

## 6. Look and sound (direction, not assets)
- Resolution / pixel scale: <e.g. 640×360, 3×>
- Palette / mood: <three words>
- Audio mood: <two words>; SFX priority: <jump, hit, pickup>

## 7. Platforms and budget
- Targets: <Windows / Web / ...> — performance budget per
  references/performance-budgets.md
- Controls: keyboard + gamepad; touch: <yes/no>

## 8. Milestones
| # | Name | Definition of done | Status |
|---|------|--------------------|--------|
| 0 | Loop on paper | this doc §1–4 agreed | |
| 1 | Slice | §4 met, playtest logged | |
| 2 | Feel | juice pass per references/juice-and-feel.md | |
| 3 | Content | N units, save/load, menus | |
| 4 | Ship | export tested on each target | |

## 9. Cut list (live)
| Idea | Cut because | Revisit when |
|------|-------------|--------------|
| <feature> | not core loop | slice is fun |

## 10. Open questions (owner taste)
- <question> — options: A / B
```

Rules for filling it:
- §1 and §3 must have numbers before code starts; the rest may be `TBD`.
- Every feature request from the owner lands in §9 or §8, never nowhere.
- §10 is the only place questions live; ask them as two-option choices.
