# Godot import settings for generated art

## Project-wide pixel art defaults
`project.godot`:
```ini
[rendering]
textures/canvas_textures/default_texture_filter=0   # 0 nearest, 1 linear
2d/snap/snap_2d_transforms_to_pixel=true
2d/snap/snap_2d_vertices_to_pixel=true

[display]
window/stretch/mode="canvas_items"
window/stretch/aspect="keep"
window/stretch/scale_mode="integer"
```
Per node: `CanvasItem.texture_filter = TEXTURE_FILTER_NEAREST` (inherits
from parent by default). HD art: leave filter linear, keep snap off.

## Texture `.import` sidecar (pixel art)
```ini
[params]
compress/mode=0            # Lossless
compress/high_quality=false
mipmaps/generate=false
process/fix_alpha_border=true
process/premult_alpha=false
detect_3d/compress_to=0    # never auto-convert to VRAM compressed
```
Set via Import dock → Preset "2D Pixel" → "Set as Default for Texture2D"
so new PNGs inherit it. `fix_alpha_border` bleeds edge colour into
transparent pixels, removing dark halos when filtered — harmless with
nearest. After writing PNGs from outside the editor, run `godot_import`
(`--import`) so the sidecar and `.godot/` cache exist before a headless
run loads them.

## Sprites
- Single frame: `Sprite2D.texture`; `centered=true` default; set
  `offset` so feet sit on the origin for characters.
- Grid sheet: `Sprite2D.hframes/vframes` + `frame`, or
  `AnimatedSprite2D` with a `SpriteFrames` resource: Animations panel →
  "Add frames from sprite sheet" → set columns/rows → select frames →
  name the animation, set FPS (8–12 for pixel art), loop on/off.
- `region_enabled` + `region_rect` for `AtlasTexture`-style cropping.
- Frames packed with `--pad 1` avoid bleeding when any filtering is on.

## 9-slice UI
`NinePatchRect`: `texture`, then `patch_margin_left/top/right/bottom` =
border width in source pixels; `axis_stretch_horizontal/vertical` =
`Stretch` (plain border) or `Tile` (patterned). For themed controls, a
`StyleBoxTexture` with `texture_margin_*` and `expand_margin_*` assigned
to `Panel`/`Button` styles in a `Theme`.

## Tiles
`TileSet` resource → Atlas source: drag the sheet, set `texture_region_size`
to the tile size, enable "Use Texture Padding" (default) when filtering
is on. Add physics layer(s) and paint collision polygons per tile;
terrains for autotiling. Place with `TileMapLayer` nodes (4.3+; one per
depth) or `TileMap` layers on older 4.x.

## Backgrounds
`Sprite2D` or `TextureRect` under `ParallaxBackground`/`Parallax2D`.
Large HD backgrounds may use `compress/mode=2` (VRAM compressed); pixel
backgrounds stay lossless.

## Fonts for UI text
Import the `.ttf`; for pixel fonts: Import dock → `antialiasing=0`,
`hinting=0`, `subpixel_positioning=0`, use the native size in the Theme.
Never generate text as pixels.

## Quick verify
After adding assets: `godot_import` → `godot_check` on touched scenes →
smoke. A texture that failed import loads as `null` and the smoke/diag
script's `load()` check catches it.
