# Prompts: seamless tiles and tilesets

## What works
- Single seamless textures (grass, stone, sand, water, wood, metal) for
  top-down floors or repeating walls.
- A ground tile and a "top edge" variant for platformers, generated
  separately and hand-aligned.

## What does not work well
- A full autotile set (47-tile blob, 16-tile Wang) from one prompt. The
  model does not understand tile adjacency. Generate the base texture,
  then build edges/corners by editing in Pillow or in Godot's TileSet
  editor with the base tile plus a hand-drawn edge overlay.
- Tiles with objects in them (chests, doors): generate as single sprites
  and place them as scene objects instead.

## Recipe — seamless texture
```text
prompt: seamless tileable <material> texture, top-down, even flat
  lighting, no shadows, no border, no vignette, uniform pattern, 2D game
  tile, <style words>
negative: text, watermark, border, frame, vignette, shadow, perspective,
  seams, object, character
size: 512x512  steps: 20
```
Generate 3–4 seeds; pick the most uniform. Then:
```text
python scripts/sheet_tools.py downscale tile_raw.png tile_64.png --factor 8   # 512 → 64
python scripts/sheet_tools.py quantize tile_64.png tile_64q.png --colors 16
```
Tile sizes: 16 or 32 px for pixel art, 64–128 px for HD.

## Wrap test (mandatory)
A tileable image must match at its edges. Build a 2×2 repeat and look at
the centre cross:
```text
python scripts/sheet_tools.py pack tiles_dir/ wrap_test.png --cols 2 --pad 0
```
(put four copies of the tile in `tiles_dir/`) then `vision_decode` and
look for a visible seam. If there is one: try another seed, or crop a
centred region and accept a softer pattern, or offset by half and
clone-fix in an editor. Do not ship a tile that fails this.

## Recipe — platformer ground strip
Generate the fill texture (above), then:
- ask for "grass top edge on a <material> block, cross-section view,
  flat 2D, magenta background above the grass" at 512×512,
- `alpha-key --color ff00ff`, `downscale`, `quantize --palette fill.png`
  so the edge shares the fill's palette,
- in Godot, TileSet: terrain set with the fill as centre tile and the
  edge as top; corners are the fill with the edge overlaid — usually
  worth 10 minutes of pixel editing by the owner.

## Recipe — water / lava (animated)
Generate one seamless frame, then animate in-engine: a `ShaderMaterial`
scrolling UV or a `noise` distortion. Do not generate frames.

## Tileset sheet layout
Grid with no padding when `texture_filter` is nearest and no mipmaps;
with filtering on, add 1–2 px padding (`pack --pad 2`) or use the TileSet
"Use Texture Padding" option (on by default in 4.x).

## Checks
- Wrap test passed.
- Palette count matches the game's palette (quantize to a shared palette).
- No lighting direction baked in, or all tiles share the same one.
