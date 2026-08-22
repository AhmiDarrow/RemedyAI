# Juice and feel

Tune in this order. Each step is a number; change one, playtest, keep or
revert. Expose them as `@export` so the owner can tweak in the editor.
Starting values are for a 2D platformer at 60 Hz, 640×360; scale to taste.

## 1. Responsiveness (before anything visual)
- Poll input in `_physics_process`; no `await` on the input path.
- Acceleration: reach top speed in 4–6 frames; decelerate in 2–4.
  `velocity.x = move_toward(velocity.x, dir * speed, accel * delta)`.
- Jump: short hop vs full jump via variable height — cut `velocity.y` by
  half when the button is released while rising.
- Gravity higher while falling (×1.6) so jumps feel snappy.
- Turn-around: flip instantly, no momentum in the wrong direction.

## 2. Forgiveness
- Coyote time 0.08–0.12 s: can still jump briefly after leaving a ledge.
- Jump buffer 0.1 s: a press slightly before landing still jumps.
- Corner correction: nudge the body 2–4 px sideways when clipping a ceiling
  corner.
- Hitboxes: player hurtbox ~20% smaller than the sprite; enemy hitboxes
  slightly larger than theirs.

## 3. Impact
- Hit-stop: freeze `Engine.time_scale = 0.0` (or a local pause) for 2–4
  frames on hits, 6–8 on kills. Restore with a timer that ignores scale.
- Screen shake: trauma model — `trauma = clamp(trauma + 0.3, 0, 1)`,
  offset = `trauma*trauma * max_offset * random`, decays 1.5/s. Small
  shakes often beat one big shake.
- Knockback on both the hitter and the hit; tiny flash (white modulate)
  on the victim for 2 frames.

## 4. Squash and stretch
- Land: `scale = (1.25, 0.75)` → back to `(1,1)` in 0.12 s with
  `TRANS_BACK`. Jump: `(0.8, 1.2)`. Use a `Tween`; kill the previous one.
- Scale the sprite node, not the body, or collisions change.

## 5. Particles, sound, camera
- Dust puff on land and on direction change; 3–6 particles, 0.3 s.
- Sound on every state change: jump, land, hit, pickup, death, UI. Pitch
  randomise ±10% (`pitch_scale = randf_range(0.9, 1.1)`).
- Camera: `Camera2D` with `position_smoothing_enabled`, speed 5–8; look-
  ahead of 24–48 px in the move direction; a small drag margin; lock Y on
  flat ground if jumps bounce the camera.
- Trails/afterimages only for dashes; stop if readability suffers.

## 6. Readability check (the anti-juice)
After each pass, confirm the player can still tell: where they are, what
hurts, what to collect, where to go. If not, remove the last effect.

## Numbers table (copy into the GDD)
| Parameter | Start | Range |
|-----------|-------|-------|
| run speed | 160 px/s | 120–220 |
| accel frames | 5 | 3–8 |
| jump height | 3.5 tiles | 2.5–4.5 |
| fall gravity mult | 1.6 | 1.3–2.2 |
| coyote | 0.1 s | 0.06–0.15 |
| jump buffer | 0.1 s | 0.08–0.15 |
| hit-stop hit/kill | 3 / 7 frames | 2–10 |
| shake max offset | 6 px | 3–12 |
| land squash | 1.25×0.75 | mild–wild |
