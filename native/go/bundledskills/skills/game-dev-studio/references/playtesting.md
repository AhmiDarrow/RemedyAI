# Playtesting

Two kinds, both every time gameplay changes: a scripted run you do, and a
human run the owner does. Neither replaces the other. Log both in
`docs/playtests.md` (date, commit/build, what changed, findings).

## 1. Scripted run (you)
Launch windowed: `godot_run(path, headless=False)` (auto-backgrounded;
note the pid). Then `game_playtest(pid, seconds=20, interval=2,
keys=["right:3", "space", "right:2", "space"], question="Did the player
move right and jump? Any error text? Is the HUD visible?")`. The key
script format follows the tool's doc; keep it short and deterministic.
Use `computer_screenshot` + `vision_decode` for spot checks between runs.

What to look for in screenshots and log:
- `SCRIPT ERROR`, `ERROR:`, `Condition ... is true` lines → fix first.
- Player visibly moved the way the keys said; camera followed.
- Nothing off-screen that should be on; UI readable at the target size.
- Restart works after a deliberate death (script a fall or a hazard).
- Frame time: if `Performance.get_monitor(Performance.TIME_PROCESS)` is
  printed by a diag script, spikes over the budget.

Be honest about limits: scripted input cannot judge feel, difficulty or
clarity. Say "scripted run: moved, jumped, no errors; feel unverified".

If no window can be observed (remote/CI), fall back to the headless smoke
and state that the playtest did not happen.

## 2. Owner run (3 minutes, three questions)
Ask the owner to play for three minutes without you talking. Then ask:
1. Where did you die or get stuck, and what did you think caused it?
2. What did you try that did nothing?
3. What did you want to do next that you could not?

Optional fourth only after milestone 2: "Rate feel 1–5 and say the one
thing you would change."

Do not explain the game first; confusion is data. Do not defend a design
during the report; write it down.

## 3. Ranking findings
Order: crash/stuck → the most frequent confusion → lose-condition
clarity → feel → content wishes. One fix per item; verify; re-run the
scripted playtest; ask for another owner run only when feel or rules
changed.

## 4. Log format (`docs/playtests.md`)
```markdown
## 2026-08-22 — build abc123 — after: coyote time 0.1s
Scripted: moved/jumped ok; no errors; restart ok.
Owner: died at spikes x3 (thought jump was higher); tried pressing down on
ladder (nothing); wanted to dash.
Actions: raise jump apex marker / add spike warning; ladder → cut list;
dash → cut list (revisit after level 2).
```

## 5. Larger tests (later milestones)
- Fresh-eyes test: someone who has never seen it, same three questions.
- Export test: play the exported build from a new folder, not the editor.
- Input parity: one full run on keyboard, one on gamepad.
- Session length: does a 15-minute run still hit the budget (leaks, spawn
  counts)?
