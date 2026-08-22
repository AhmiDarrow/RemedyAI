# Prompts: UI panels, buttons, icons, bars

UI art generates well when each piece is plain enough to 9-slice. The
centre must be flat and the border uniform; text is always rendered by
the engine, never generated.

## 9-slice panel
```text
prompt: flat 2D game UI panel, rounded rectangle, <material: wood /
  stone / metal / parchment / glass>, uniform decorative border, plain
  empty centre, centred on solid magenta background, no text, no icons,
  front view, even lighting
negative: text, letters, icons, buttons inside, watermark, perspective,
  shadow outside, gradient background
size: 512x512  steps: 20
```
Post: `alpha-key --color ff00ff --tolerance 40`, optionally `downscale`
for pixel UI, `quantize`. Then in Godot, `NinePatchRect` with
`patch_margin_*` equal to the border width (measure it on the PNG); or a
`StyleBoxTexture` with `texture_margin_*` for `Panel`/`Button` themes.
Set `axis_stretch_horizontal/vertical` to `Tile` when the border has a
repeating motif, `Stretch` when it is plain.

## Button with states
Generate **one** button, then derive states in post instead of asking for
four: normal = as generated; hover = brightened (+10%); pressed = darkened
and shifted 1–2 px down in-engine; disabled = desaturated. Do this with
Pillow in a few lines (`ImageEnhance.Brightness`, `ImageOps.grayscale`
blended) or with `modulate` at runtime — which needs no extra files.
```text
prompt: flat 2D game UI button, rounded, <material>, plain centre, subtle
  bevel, centred on solid magenta background, no text
```

## Icons
```text
prompt: single flat game icon of <object>, centred, bold silhouette,
  thick outline, solid magenta background, no text, symmetrical lighting
size: 512x512
```
Post: `alpha-key`, `downscale --factor 16` for 32 px icons, `quantize
--colors 16`. Generate a batch with the same style phrase and seed
neighbourhood for consistency; expect to discard a third.

## Bars and frames
Health/mana bars: generate only the frame ("empty ornate bar frame,
horizontal, hollow centre, magenta background"); fill is a
`TextureProgressBar` with a flat colour or a `ColorRect` behind the frame.
Generated fills do not stretch cleanly.

## Cursor
"game cursor arrow / pointing hand, bold outline, magenta background" at
512, downscale to 32, `Input.set_custom_mouse_cursor`.

## Theme wiring
Build one `Theme` resource: default font, `Panel` StyleBoxTexture,
`Button` normal/hover/pressed/disabled StyleBoxTextures, font colours.
Assign it on the root `Control`; all children inherit. Keep UI under a
`CanvasLayer` so camera zoom does not touch it.

## Checks
- Border width identical on all four sides (measure; the 9-slice needs it).
- Nothing in the centre region.
- Halo-free edges after alpha-key (`vision_decode` on a dark and a light
  background).
- Text is rendered by a font, legible at the target scale.
