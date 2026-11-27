---
name: game-dev-studio
description: >
  The studio loop for making a game with the owner: pin the fantasy and core
  loop, write a short GDD, build a vertical slice, iterate on feel, add
  content, polish and export. Use when the owner wants to make, prototype or
  playtest a game of any genre; pairs with an engine skill such as godot-4.
version: 1.0.0
author: Remedy
tags: [game, design, studio, playtest, gdd, vertical-slice]
requires: []
tools: [game_project_info, game_playtest, godot_run, godot_check, godot_export, skill_activate, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, computer_screenshot, vision_decode, comfyui]
triggers:
  - "\\b(make|build|create|prototype|design)\\b.{0,40}\\b(game|platformer|roguelike|shooter|rpg|puzzle game|metroidvania|tower defense|visual novel)\\b"
  - "\\b(vertical slice|gdd|game design doc(ument)?|playtest(ing)?|game feel|juice|core loop|level design)\\b"
---

# Game dev studio (the loop)

A procedure for building a game with the owner, who holds creative
direction. Every phase ends with something that runs. Activate the engine
skill first (`godot-4` for a `project.godot` project; `game_project_info(path)`
tells you the stack) and follow its verification rule: **every change ends with the engine's headless
verify or the stack's test command**, and you say what ran.

## Phase 0 — the fantasy and the core loop (one paragraph)

Before any file: write one paragraph with the owner. It names the fantasy
("you are a ghost stealing dreams"), the 10-second loop (verb → feedback →
reward → verb), the win and lose condition, and one reference game. If the
owner is vague, propose two concrete options and ask them to pick; do not
ask open questions. Save it as the first section of the GDD.

## Phase 1 — GDD (`docs/gdd.md`, 1–2 pages)

Use `references/gdd-template.md`. Fill only what is decided; mark the rest
`TBD` and keep the **cut list** live from day one. Re-read the GDD before
each phase and update it instead of letting it rot.

## Phase 2 — vertical slice

One level, one mechanic, a way to win, a way to lose, restart. Placeholder
art (coloured rectangles) and placeholder audio are correct here. Order:
1. Player controller + the single mechanic, in an empty room.
2. One hazard or enemy, death, restart.
3. A goal, a win screen, a lose screen.
4. Main scene wired, smoke passes, the owner plays it.
Stop here and playtest. Do not add a second mechanic until the first one
is fun with rectangles. "One mechanic deep beats five shallow."

## Phase 3 — feel (juice)

Read `references/juice-and-feel.md`. Tune in this order: input latency and
acceleration curves → coyote time / input buffering → hit-stop, screen
shake, squash-and-stretch → particles, sound, camera. Each is a number the
owner can feel; change one, playtest, keep or revert.

## Phase 4 — content, then polish/export

Content = more levels/enemies/items using the same systems. Only now
replace placeholder art (see `game-assets`), add menus, settings, save
(`references/save-systems.md`), input remapping (`references/input-maps.md`),
audio mix (`references/audio.md`), then export per platform and test the
export, not the editor run.

## Scope control

Keep `docs/gdd.md` → **Cut list** current. Anything not serving the core
loop goes there first and comes back only if the slice is finished and
fun. Milestone shape and time estimates: `references/scope-and-milestones.md`.
If the owner asks for a feature mid-slice, add it to the cut list and ask
whether it replaces something or waits; do not silently expand.

## Playtest protocol

Read `references/playtesting.md` before the first session. Short form:
- You: `godot_run(path, headless=False)` then `game_playtest(pid, seconds,
  interval, keys, question)` with scripted keys; read screenshots with
  `vision_decode`; collect `SCRIPT ERROR`, frame-time spikes, stuck states.
  Say clearly what you could and could not observe headlessly/by script.
- Owner: ask them to play for 3 minutes and report three things: where
  they died, what they tried that did nothing, what they wanted to do next.
  Write the answers into `docs/playtests.md` with the build hash/date.
- Fix the highest-frequency confusion first, not the most interesting bug.

## Performance budgets

Targets per platform are in `references/performance-budgets.md`. Default:
60 fps with 4 ms headroom on the weakest target, fixed physics at 60 Hz,
draw calls and node count logged with `Performance.get_monitor` in the
smoke/diag script when the owner reports slowness. Measure before
optimising; never optimise a placeholder.

## Decide vs ask

- **Decide yourself**: engineering (structure, naming, node types, save
  format, folder layout), defaults that follow the GDD, anything on the cut
  list, fixing a verify failure.
- **Ask the owner (one question, two options)**: taste — art direction,
  tone, difficulty target, what the mechanic feels like, which feature
  survives a cut, anything that changes the fantasy paragraph.
- **Never ask**: where files go, which engine idiom to use, whether to run
  the verify.

## Conventions

Asset naming, folders, import settings, pixel-perfect setup:
`references/asset-conventions.md`. Follow the project's existing layout
if it has one.

## Per-change checklist

```text
[ ] change serves the current phase (else → cut list)
[ ] engine headless verify ran (check + smoke) — quote the OK line
[ ] playtest if feel/gameplay changed; note result in docs/playtests.md
[ ] GDD updated if a decision changed
[ ] tell the owner what to play and what to report
```
