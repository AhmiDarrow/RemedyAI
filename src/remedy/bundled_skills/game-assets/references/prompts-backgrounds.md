# Prompts: backgrounds, parallax, key art

Backgrounds are the best use of txt2img in a game: large, single image,
no transparency, style drift between images is tolerable when each is a
different scene.

## Sizes
- Generate at 1024×576 (16:9) or 1024×512; upscale/downscale in post.
- For 640×360 pixel-art games: generate 1280×720, then
  `downscale --factor 2` and `quantize --colors 32`. For HD games keep
  full size, `quantize` only if a unified palette is wanted.
- Parallax layers: generate each layer separately at the same size; far
  layers need less detail and lower contrast.

## Recipe — single scene
```text
prompt: 2D side-scrolling game background, <setting>, <time of day>,
  <palette words>, painted flat shading, clean shapes, no characters,
  no text, wide shot, horizon at lower third
negative: text, watermark, signature, blurry, photo, 3d render, people,
  characters, frame, border
size: 1024x576  steps: 20  seed: <vary>
```
Examples of `<setting>`: "mossy ruined temple in a jungle", "neon city
rooftops at night with rain", "pastel desert with giant bones".

## Recipe — parallax set (three layers)
Same seed, same palette words, change only the depth clause:
- far: "distant mountains and sky only, very low contrast, soft haze"
- mid: "tree line and ruins silhouettes, medium contrast, no ground"
- near: "foreground rocks and grass along the bottom edge, high contrast,
  empty upper two thirds"
Alpha-key the near layer if it must overlay (ask for "solid magenta
background above the ground line" and use `alpha-key --color ff00ff`).
In Godot: `ParallaxBackground` + one `ParallaxLayer` per image with
`motion_scale` 0.2 / 0.5 / 0.9, or the 4.3+ `Parallax2D` node.

## Recipe — key art / title screen
```text
prompt: game key art, <hero description> facing <direction>, <setting>
  behind, dramatic lighting, painted illustration, clean composition,
  empty space at top for title, no text
negative: text, logo, watermark, signature, blurry, deformed hands,
  extra limbs
size: 1024x1024 (or 832x1216 portrait)  steps: 24
```
Leave room for the title; render the title with a font in-engine.

## Recipe — sky / gradient
Often better done procedurally (`GradientTexture2D`) than generated;
generate only when clouds or detail are wanted.

## Consistency across scenes
Fix a palette phrase ("teal shadows, warm amber light, desaturated
greens") and a style phrase, reuse them verbatim, and quantize all
backgrounds to the same palette PNG with `quantize --palette shared.png`.
That unifies look more than any prompt tweak.

## Checks
- No accidental characters or text. `vision_decode` and ask.
- Horizon and ground line at the same height across layers.
- Contrast low enough that sprites read on top; test with the player
  sprite placed over it before accepting.
