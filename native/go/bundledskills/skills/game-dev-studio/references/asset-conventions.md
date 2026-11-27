# Asset conventions

Follow the existing project's layout if it has one. Otherwise:

## Folders
```text
assets/
  sprites/<feature>/     hero_idle.png, hero_run.png
  tiles/                 dungeon_tiles.png + dungeon_tiles.tres (TileSet)
  ui/                    panel_9slice.png, button_idle.png, icons/
  backgrounds/           forest_far.png, forest_near.png
  audio/sfx/  audio/music/
  fonts/
  shaders/
```
Feature-local art may sit next to its scene (`enemies/slime/slime.png`);
shared art lives under `assets/`. Do not keep source files (`.aseprite`,
`.psd`, `.kra`) where Godot imports them: put them in `art_src/` and add
it to the export `exclude_filter`, or keep them outside the project.

## Naming
- `snake_case`, ASCII, no spaces: `hero_run.png`, `sfx_jump.wav`.
- Sheets: `<subject>_<action>[_<frames>x<w>x<h>].png` when the grid is
  not obvious, e.g. `hero_run_8x32x32.png`.
- Tiles: `<set>_tiles.png`; UI: `ui_<widget>_<state>.png`.
- Never rename imported assets outside the editor without moving the
  `.import` sidecar too (see godot-4 pitfalls).

## Pixel art project settings
```ini
[display]
window/size/viewport_width=640
window/size/viewport_height=360
window/size/window_width_override=1920
window/size/window_height_override=1080
window/stretch/mode="canvas_items"     # or "viewport" for hard pixel snap
window/stretch/aspect="keep"
window/stretch/scale_mode="integer"    # 4.2+: whole-number scaling only

[rendering]
textures/canvas_textures/default_texture_filter=0   # nearest
2d/snap/snap_2d_transforms_to_pixel=true
2d/snap/snap_2d_vertices_to_pixel=true
```
Per-node override: `CanvasItem.texture_filter = TEXTURE_FILTER_NEAREST`.
Camera zoom must be integer. Sub-pixel movement is fine with
`canvas_items` stretch; use `viewport` stretch when you want true low-res
rendering (rotations look chunky, that is the point).

## Import settings (the `.import` sidecar)
Pixel art PNGs:
```ini
[params]
compress/mode=0          # lossless
mipmaps/generate=false
process/fix_alpha_border=true
detect_3d/compress_to=0
```
Set once via Import dock → Preset → "2D Pixel" then "Set as Default for
'Texture2D'". HD art: `compress/mode=0` or `2` (VRAM) for large
backgrounds; mipmaps on only for scaled-down drawing.

## Atlases and sheets
- `AtlasTexture` or `Sprite2D.hframes/vframes` + `frame` for grid sheets;
  `AnimatedSprite2D` + `SpriteFrames` for animations.
- Keep one sheet per character/set; pad frames by 1 px or enable
  `Sprite2D.region_filter_clip_enabled` to avoid bleeding when filtered.
- TileSet: tile size matches the sheet grid; physics layers named; use
  `TileMapLayer` nodes (4.3+) one per depth.
- Power-of-two sheet sizes are not required in Godot 4 but ≤ 2048² keeps
  web/mobile safe.

## Audio and fonts
WAV for short SFX, OGG for music; `sfx_<event>.wav`, `mus_<track>.ogg`
(details in audio.md). Pixel fonts: antialiasing and hinting off, native
pixel size.
