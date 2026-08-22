"""Sprite-sheet post-processing for generated game art (Pillow only, no network).

Subcommands: slice, pack, quantize, alpha-key, downscale.
Paths must be inside the current working tree unless --allow-outside is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ALLOW_OUTSIDE = False
FORCE = False


def _check_path(p: Path) -> Path:
    p = p.expanduser().resolve()
    if not ALLOW_OUTSIDE:
        cwd = Path.cwd().resolve()
        if cwd != p and cwd not in p.parents:
            sys.exit(f"refused: {p} is outside the working tree (use --allow-outside)")
    return p


def _in(path: str) -> Image.Image:
    p = _check_path(Path(path))
    if not p.is_file():
        sys.exit(f"not a file: {p}")
    return Image.open(p).convert("RGBA")


def _out(path: str) -> Path:
    p = _check_path(Path(path))
    if p.exists() and not FORCE:
        sys.exit(f"exists: {p} (use --force to overwrite)")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _outdir(path: str) -> Path:
    p = _check_path(Path(path))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hex(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    if len(s) != 6:
        sys.exit(f"bad colour {s!r}; want RRGGBB or auto")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


# ---------------------------------------------------------------- subcommands


def cmd_alpha_key(a: argparse.Namespace) -> None:
    img = _in(a.input)
    key = img.getpixel((0, 0))[:3] if a.color == "auto" else _hex(a.color)
    tol = a.tolerance
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, al = px[x, y]
            d = max(abs(r - key[0]), abs(g - key[1]), abs(b - key[2]))
            if d <= tol:
                px[x, y] = (r, g, b, 0)
            elif not a.no_despill and d <= tol * 2:
                # partial edge: fade alpha, pull colour away from the key
                f = (d - tol) / tol
                r2 = int(r * f + (255 - key[0]) * (1 - f) * 0.5 + r * (1 - f) * 0.5)
                g2 = int(g * f + (255 - key[1]) * (1 - f) * 0.5 + g * (1 - f) * 0.5)
                b2 = int(b * f + (255 - key[2]) * (1 - f) * 0.5 + b * (1 - f) * 0.5)
                px[x, y] = (r2, g2, b2, int(al * f))
    img.save(_out(a.output))
    print(f"alpha-key: key={key} tol={tol} -> {a.output}")


def cmd_downscale(a: argparse.Namespace) -> None:
    img = _in(a.input)
    w, h = img.size
    if a.factor:
        if w % a.factor or h % a.factor:
            sys.exit(f"{w}x{h} not divisible by {a.factor}; crop first")
        nw, nh = w // a.factor, h // a.factor
    elif a.width:
        nw, nh = a.width, max(1, round(h * a.width / w))
    elif a.height:
        nh, nw = a.height, max(1, round(w * a.height / h))
    else:
        sys.exit("give --factor, --width or --height")
    img.resize((nw, nh), Image.NEAREST).save(_out(a.output))
    print(f"downscale: {w}x{h} -> {nw}x{nh} (nearest) -> {a.output}")


def cmd_quantize(a: argparse.Namespace) -> None:
    img = _in(a.input)
    alpha = img.getchannel("A")
    rgb = img.convert("RGB")
    dither = Image.Dither.FLOYDSTEINBERG if a.dither else Image.Dither.NONE
    if a.palette:
        pal_src = _in(a.palette).convert("RGB")
        colors = sorted({c for _, c in pal_src.getcolors(1 << 24) or []})
        if not colors:
            sys.exit("palette image has no colours")
        colors = colors[:256]
        pal_img = Image.new("P", (1, 1))
        flat = [v for c in colors for v in c] + [0] * (768 - 3 * len(colors))
        pal_img.putpalette(flat)
        q = rgb.quantize(palette=pal_img, dither=dither)
        n = len(colors)
    else:
        q = rgb.quantize(colors=a.colors, method=Image.Quantize.MEDIANCUT, dither=dither)
        n = a.colors
    result = q.convert("RGBA")
    result.putalpha(alpha)
    result.save(_out(a.output))
    print(f"quantize: {n} colours -> {a.output}")


def cmd_slice(a: argparse.Namespace) -> None:
    img = _in(a.sheet)
    out = _outdir(a.outdir)
    w, h = img.size
    cw = (w - a.pad * (a.cols - 1)) // a.cols
    ch = (h - a.pad * (a.rows - 1)) // a.rows
    if cw <= 0 or ch <= 0:
        sys.exit("grid does not fit the sheet")
    i = 0
    for r in range(a.rows):
        for c in range(a.cols):
            x, y = c * (cw + a.pad), r * (ch + a.pad)
            frame = img.crop((x, y, x + cw, y + ch))
            if a.trim:
                box = frame.getbbox()
                frame = frame.crop(box) if box else frame
            dest = out / f"{a.prefix}_{i:02d}.png"
            if dest.exists() and not FORCE:
                sys.exit(f"exists: {dest} (use --force)")
            frame.save(dest)
            i += 1
    print(f"slice: {i} frames of {cw}x{ch} -> {out}")


def cmd_pack(a: argparse.Namespace) -> None:
    src = _check_path(Path(a.indir))
    files = sorted(p for p in src.glob("*.png") if p.is_file())
    if not files:
        sys.exit(f"no .png files in {src}")
    frames = [Image.open(p).convert("RGBA") for p in files]
    cw = max(f.width for f in frames)
    ch = max(f.height for f in frames)
    rows = (len(frames) + a.cols - 1) // a.cols
    sheet = Image.new(
        "RGBA", (a.cols * cw + a.pad * (a.cols - 1), rows * ch + a.pad * (rows - 1)), (0, 0, 0, 0)
    )
    for i, f in enumerate(frames):
        c, r = i % a.cols, i // a.cols
        x = c * (cw + a.pad) + (cw - f.width) // 2
        y = r * (ch + a.pad) + (ch - f.height if a.align == "bottom" else (ch - f.height) // 2)
        sheet.paste(f, (x, y), f)
    sheet.save(_out(a.output))
    print(f"pack: {len(frames)} frames, cell {cw}x{ch}, grid {a.cols}x{rows} -> {a.output}")


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--allow-outside", action="store_true", help="allow paths outside the working tree")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("alpha-key", help="turn a background colour transparent")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--color", default="auto", help="RRGGBB or auto (top-left pixel)")
    s.add_argument("--tolerance", type=int, default=40)
    s.add_argument("--no-despill", action="store_true")
    s.set_defaults(fn=cmd_alpha_key)

    s = sub.add_parser("downscale", help="nearest-neighbour shrink")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--factor", type=int)
    s.add_argument("--width", type=int)
    s.add_argument("--height", type=int)
    s.set_defaults(fn=cmd_downscale)

    s = sub.add_parser("quantize", help="reduce to N colours or a palette image")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--colors", type=int, default=32)
    s.add_argument("--palette", help="PNG whose colours become the palette")
    s.add_argument("--dither", action="store_true")
    s.set_defaults(fn=cmd_quantize)

    s = sub.add_parser("slice", help="split a grid sheet into frames")
    s.add_argument("sheet")
    s.add_argument("outdir")
    s.add_argument("--cols", type=int, required=True)
    s.add_argument("--rows", type=int, required=True)
    s.add_argument("--pad", type=int, default=0)
    s.add_argument("--prefix", default="frame")
    s.add_argument("--trim", action="store_true")
    s.set_defaults(fn=cmd_slice)

    s = sub.add_parser("pack", help="pack frames into a grid sheet")
    s.add_argument("indir")
    s.add_argument("output")
    s.add_argument("--cols", type=int, required=True)
    s.add_argument("--pad", type=int, default=0)
    s.add_argument("--align", choices=["bottom", "center"], default="bottom")
    s.set_defaults(fn=cmd_pack)
    return p


def main(argv: list[str] | None = None) -> None:
    global ALLOW_OUTSIDE, FORCE
    args = build_parser().parse_args(argv)
    ALLOW_OUTSIDE = args.allow_outside
    FORCE = args.force
    args.fn(args)


if __name__ == "__main__":
    main()
