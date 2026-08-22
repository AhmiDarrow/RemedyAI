# Sprite sheets: what txt2img cannot do, and what to do instead

## The problem, stated plainly
A walk/run/attack cycle needs 4–12 frames of the **same** character with
the same proportions, palette, outline weight and lighting, differing
only in pose. txt2img has no memory between generations; a seed change
changes the character, and the same seed with a changed pose phrase
changes it too. "Sprite sheet, 8 frames" yields eight similar-but-different characters
at inconsistent sizes; cleaning that up takes longer than drawing the
frames. Say this to the owner before spending GPU time.

Img2img/ControlNet pose workflows reduce drift but are not pixel-exact
at sprite scale and need custom ComfyUI graphs beyond the built-in
`generate`. Treat them as an owner-driven experiment, not a pipeline.

## Option 1 — one frame + manual edits (small sprites, ≤ 48 px)
1. Generate the idle frame large (512–1024), magenta background,
   "single character, full body, side view, neutral pose, arms slightly
   away from body, feet visible".
2. `alpha-key` → `downscale --factor N` to the target size → `quantize
   --colors 16..32`. Save as `hero_idle.png`.
3. Derive frames by editing pixels (Pillow, or the owner in Aseprite):
   - idle bob: shift the top half up 1 px on frame 2 (2 frames).
   - run: copy idle, move legs — 4 frames is enough at 8–10 fps.
   - jump/fall: one frame each, legs tucked / legs spread.
   - hit: idle with `ImageOps.invert`-style white flash, or do it with
     `modulate` in-engine.
4. `pack frames/ hero.png --cols 4 --pad 1` → `SpriteFrames` in Godot.
A Pillow script that shifts regions per frame is ~20 lines; keep it in
the project's `tools/` so it can be re-run after retouching.

## Option 2 — placeholders until an artist
Coloured `ColorRect`/`Polygon2D` capsules, one colour per state, flip on
direction. Gameplay, feel and levels do not depend on art. Real sprites
drop in later without code changes if the scene already uses an
`AnimatedSprite2D` with the final animation names.

## Option 3 — programmatic animation from one frame
Works well for small sprites and UI:
- Run: `Tween` loop on `scale` (0.95/1.05 at 8 Hz) + 2 px bob + `flip_h`.
- Jump: stretch `(0.8, 1.2)`; land: squash `(1.25, 0.75)`.
- Attack: `rotation` swing 20° over 0.1 s plus a separate slash sprite.
- Hit: shader or `modulate = Color(10,10,10)` flash for 2 frames.
- Death: `modulate.a` → 0 with a spin, or particles.
Combined with sound and hit-stop, players read this as animation.

## If the owner insists on generated frames
Generate the idle frame; generate each pose with the **same seed** and
prompt, changing only the pose clause; quantize every frame to the idle
frame's palette (`quantize --palette hero_idle.png`); `pack --align
bottom`. Let the owner judge; expect to fall back to Option 1.

## Decision rule
Sprite ≤ 32 px and ≤ 6 frames → Option 1 or 3. Larger or longer →
Option 2 now, an artist later. Sprite work never blocks the slice.
