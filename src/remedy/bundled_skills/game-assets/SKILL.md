---
name: game-assets
description: >
  Make 2D game art with local ComfyUI txt2img plus a Pillow post-process
  script: backgrounds, seamless tiles, 9-slice UI, key art, single sprites,
  then slice/pack/quantize/alpha-key/downscale and set Godot import flags.
  Use when the owner asks for sprites, tilesets, pixel art, backgrounds or
  UI art for a game.
version: 1.0.0
author: Remedy
tags: [game, assets, sprites, pixel-art, tileset, comfyui, pillow]
requires: [pillow]
tools: [comfyui, skill_run, bash_exec, file_read, file_write, list_dir, vision_decode, skill_activate]
triggers:
  - "\\b(sprite ?sheets?|tile ?sets?|tile ?map art|game assets?|pixel ?art|background art|ui sprites?|9-?slice|seamless tiles?)\\b"
---

# Game assets (ComfyUI + Pillow post-process)

Local txt2img via the `comfyui` tool (activate the `comfyui` skill if the
server is not up), then `scripts/sheet_tools.py` to turn raw generations
into engine-ready PNGs, then import settings in Godot.

## Honest scope — say this to the owner before generating

txt2img is **good** for: backgrounds and parallax layers, seamless tiles
(with "tileable" prompting + a post-check), UI panels/buttons that will be
9-sliced, key art / title screens, icons, and **single static sprites**.

txt2img is **poor** for multi-frame animation sheets with a consistent
character: frames drift in proportions, palette and style, and the
result looks worse than rectangles. Do not promise a run cycle. Offer
instead, in this order:
1. One generated idle frame + hand edits for 2–4 extra frames (you edit
   pixels with Pillow; the owner or an artist refines).
2. Placeholder rectangles/capsules with a colour per state until an
   artist; the gameplay does not wait.
3. Programmatic animation: `Tween` squash/stretch, rotation, flip,
   shader flash, `AnimatedSprite2D` of a single frame with offsets — it
   reads as motion at game speed.
If the owner still wants generated frames, generate one frame, then use
it as a reference for manual edits; never stitch unrelated generations.

## Workflow

1. **Spec first**: target resolution (e.g. 640×360 at 3×), sprite size
   (16/32/48 px), palette size (16/32 colours), transparency method, and
   the file name per `game-dev-studio` asset conventions. Write it down.
2. **Generate** with `comfyui` `action=generate`: prompts from
   `references/prompts-*.md`; sizes 512–1024 square (ask VRAM first); set
   `seed` to vary, keep the seed that works. Generate 2–4 candidates, show
   the markdown images, let the owner pick. For pixel art generate large,
   then downscale; do not ask the model for "16×16".
3. **Post-process** with `skill_run` on `scripts/sheet_tools.py` (Pillow
   only, no network, refuses paths outside the current tree unless
   `--allow-outside`):

```text
python scripts/sheet_tools.py alpha-key in.png out.png --color auto --tolerance 40
python scripts/sheet_tools.py downscale in.png out.png --factor 8       # nearest
python scripts/sheet_tools.py quantize in.png out.png --colors 32
python scripts/sheet_tools.py slice sheet.png frames/ --cols 8 --rows 1 --prefix hero_run
python scripts/sheet_tools.py pack frames/ sheet.png --cols 8 --pad 1
```
Order for a generated sprite: alpha-key → downscale → quantize. For a
tile: downscale → quantize → check wrap (`references/postprocess.md`).
`--color auto` keys the top-left pixel's colour; use `--color ff00ff` for
magenta backgrounds you asked for in the prompt.

4. **Import** into Godot (`references/godot-import-settings.md`): pixel
   art → lossless, no mipmaps, `texture_filter` nearest (project-wide
   `default_texture_filter=0`); 9-slice → `NinePatchRect`/`StyleBoxTexture`
   margins; tiles → `TileSet` with the sheet grid.
5. **Verify**: `list_dir` the output, `vision_decode` one result, run the
   engine headless verify (godot-4) so `.import` regenerates cleanly, and
   show the owner the image in chat.

## Prompt rules

- State style once and concretely: "flat-shaded 2D game sprite, clean
  outline, solid magenta background, centred, no text, no watermark".
- Backgrounds: name layer and depth ("far parallax layer, low contrast").
- Tiles: "seamless tileable texture, top-down, even lighting, no border".
- UI: "flat UI panel, rounded corners, plain centre, uniform border"
  so 9-slice margins have nothing inside them.
- Negative prompt every time: `text, watermark, signature, blurry,
  photo, 3d render, multiple objects, cropped`.
- Never prompt for a copyrighted character or a named living artist's
  style; suggest a descriptive style instead.

## Limits you must state

- Palette and style drift between runs; one seed ≠ one character.
- Transparent output is not native; alpha-key is an approximation — halos
  need `--tolerance` tuning or a manual clean-up.
- Seamlessness is not guaranteed by the word "tileable"; check with the
  wrap test in `references/postprocess.md`.
- Generated text in UI is garbage; render text with a font in-engine.
