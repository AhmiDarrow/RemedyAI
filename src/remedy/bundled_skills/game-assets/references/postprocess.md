# Post-processing with scripts/sheet_tools.py

Pillow only, no network. If Pillow is missing:
`pip install "remedy-ai[game-assets]"`. Run via `skill_run` (`sheet_tools.py`) or
`python scripts/sheet_tools.py ...`. Paths must be inside the working
tree (`--allow-outside` overrides); outputs are overwritten only with
`--force`. Both are global flags, placed before the subcommand.

## Subcommands

### alpha-key — background colour → transparency
```text
sheet_tools.py alpha-key IN OUT [--color auto|RRGGBB] [--tolerance 0-255] [--no-despill]
```
`auto` samples the top-left pixel. Tolerance is a max per-channel
distance; 30–50 for generated images (backgrounds are never flat).
Despill pulls the key colour out of semi-transparent edge pixels. Check
on a light and a dark background; lower tolerance if the sprite loses
pixels, raise it if a halo remains; hand clean-up may still be needed.

### downscale — nearest-neighbour shrink for pixel art
```text
sheet_tools.py downscale IN OUT (--factor N | --width W | --height H)
```
Always nearest (`Image.NEAREST`), never bilinear: pixel art needs hard
edges. Factor must divide the source size exactly or the script errors;
crop first if needed. Downscale **before** quantize.

### quantize — reduce palette
```text
sheet_tools.py quantize IN OUT (--colors N | --palette PALETTE.png) [--dither]
```
Median-cut to N colours, alpha preserved. `--palette` remaps to the
colours of another PNG so every asset shares one palette. Dither is off
by default (noise looks bad at sprite scale).

### slice — grid → frames
```text
sheet_tools.py slice SHEET OUTDIR --cols C --rows R [--pad P] [--prefix NAME] [--trim]
```
Writes `NAME_00.png` …; cell size derives from the sheet. `--trim` crops
frames to their opaque bounds (sizes then differ; skip for engine sheets).

### pack — frames → grid sheet
```text
sheet_tools.py pack INDIR OUT --cols C [--pad P] [--align bottom|center]
```
Sorted by name; cells sized to the largest frame; `--align bottom` keeps
feet on one line. Prints the cell size for `hframes/vframes`.

## Processing order
| Asset | Order |
|-------|-------|
| Sprite (pixel) | alpha-key → downscale → quantize → pack |
| Sprite (HD) | alpha-key → (optional quantize) |
| Tile | downscale → quantize → wrap test |
| Background | downscale (if pixel) → quantize --palette shared |
| UI panel | alpha-key → downscale (if pixel) → quantize |
| Icons | alpha-key → downscale → quantize --palette ui.png |

## Wrap test for tiles
Four copies of the tile in a folder → `pack DIR wrap.png --cols 2 --pad
0` → `vision_decode` the result and look at the centre cross for seams.

## Common failures
- Magenta fringe: lower tolerance, keep despill on, or re-prompt with
  "solid flat magenta background, sharp edges".
- Lost thin lines after downscale: prompt "bold outline" or use a
  smaller factor.
- Muddy colours: more colours, and downscale before quantize.
- Uneven frames after pack: a stray larger frame; inspect sizes.
- Path refused: outside the working tree; move it or `--allow-outside`.
