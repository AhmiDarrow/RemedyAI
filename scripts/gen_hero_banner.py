"""Generate assets/previews/hero_banner_win_linux.png — the flashy README hero.

Pure Pillow (no ComfyUI). Two-panel design:
  * Left  panel: flat #0c0e14 so the opaque hero_logo_color_on_dark.png pastes
    seamlessly (same brand background).
  * Right panel: subtle gradient + teal/gold glows + faint dot grid, with the
    multiplatform tagline, platform line, and chips.

Drawn at 2x then downscaled for crisp text and smooth gradients.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "previews" / "hero_logo_color_on_dark.png"
OUT = ROOT / "assets" / "previews" / "hero_banner_win_linux.png"

# --- palette (sampled from the logo) -----------------------------------
BG_DARK = (12, 14, 20)          # #0c0e14
BG_RIGHT_TOP = (16, 19, 28)     # #10131c
BG_RIGHT_BOT = (11, 12, 17)     # #0b0c11
TEAL = (0, 216, 192)            # #00d8c0
GOLD = (192, 168, 96)           # #c0a860
INK = (242, 245, 249)           # #f2f5f9
MUTED = (158, 167, 182)         # #9ea7b6
NEUTRAL_BORDER = (150, 160, 175)  # light gray, reads on dark bg
LOCAL_BG = (12, 14, 19)          # approx gradient color at the chip row

W, H = 1600, 400
SCALE = 2
W2, H2 = W * SCALE, H * SCALE

# font dir
FD = r"C:\Windows\Fonts"


def font(size: int, name: str = "seguisb.ttf") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(f"{FD}\\{name}", size * SCALE)


def blend(fg: tuple[int, int, int], bg: tuple[int, int, int], a: float) -> tuple[int, int, int]:
    """Alpha-blend fg over bg, returned as an opaque RGB (ImageDraw ignores
    the alpha channel on RGB canvases, so pre-blend here)."""
    return tuple(int(f * a + b * (1 - a)) for f, b in zip(fg, bg, strict=True))


def rounded_chip(
    draw: ImageDraw.ImageDraw,
    cx: int, cy: int,
    text: str,
    font_f: ImageFont.FreeTypeFont,
    border: tuple[int, int, int],
    fill: tuple[int, int, int],
) -> None:
    """Rounded-rect chip centered on (cx, cy). Returns nothing."""
    bbox = draw.textbbox((0, 0), text, font=font_f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 24 * SCALE, 10 * SCALE
    half_w = (tw + pad_x * 2) // 2
    half_h = (th + pad_y * 2) // 2
    x0, y0 = cx - half_w, cy - half_h
    x1, y1 = cx + half_w, cy + half_h
    radius = (y1 - y0) // 2
    draw.rounded_rectangle(
        [x0, y0, x1, y1], radius=radius,
        fill=fill, outline=border, width=round(2 * SCALE),
    )
    tx = cx - (bbox[0] + tw // 2)
    ty = cy - (bbox[1] + th // 2)
    draw.text((tx, ty), text, font=font_f, fill=INK)


def radial_glow(base: Image.Image, center: tuple[int, int], radius: int,
                color: tuple[int, int, int], alpha: float) -> None:
    """Composite a soft radial glow onto an RGB canvas."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(radius, 0, -4):
        a = int(alpha * 255 * (1 - i / radius) ** 2)
        d.ellipse(
            [center[0] - i, center[1] - i, center[0] + i, center[1] + i],
            fill=(*color, a),
        )
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 3))
    base.paste(Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB"), (0, 0))


def main() -> None:
    img = Image.new("RGB", (W2, H2), BG_DARK)

    # right panel vertical gradient
    d = ImageDraw.Draw(img)
    for y in range(H2):
        t = y / H2
        r = int(BG_RIGHT_TOP[0] + (BG_RIGHT_BOT[0] - BG_RIGHT_TOP[0]) * t)
        g = int(BG_RIGHT_TOP[1] + (BG_RIGHT_BOT[1] - BG_RIGHT_TOP[1]) * t)
        b = int(BG_RIGHT_TOP[2] + (BG_RIGHT_BOT[2] - BG_RIGHT_TOP[2]) * t)
        d.line([(720 * SCALE, y), (W2, y)], fill=(r, g, b))

    # faint dot grid on the right panel
    dot_color = (255, 255, 255)
    for x in range(760 * SCALE, W2, 34 * SCALE):
        for y in range(24 * SCALE, H2, 34 * SCALE):
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(*dot_color, 8))

    # glows (teal upper-left of the right panel, gold lower-right)
    radial_glow(img, (830 * SCALE, 130 * SCALE), 300 * SCALE, TEAL, 0.10)
    radial_glow(img, (1500 * SCALE, 330 * SCALE), 240 * SCALE, GOLD, 0.09)

    # divider between panels
    d.line([(720 * SCALE, 0), (720 * SCALE, H2)], fill=(255, 255, 255, 20), width=SCALE)

    # ---- left panel: the logo pasted on matching background -------------
    logo = Image.open(LOGO).convert("RGBA")
    logo_w = 560 * SCALE
    logo_h = int(logo.height * logo_w / logo.width)
    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
    lx = (720 * SCALE - logo_w) // 2
    ly = (H2 - logo_h) // 2
    img.paste(logo, (lx, ly), logo)

    # ---- right panel: text ----------------------------------------------
    TX = 790 * SCALE
    tag1 = font(46, "segoeuib.ttf")
    tag2 = font(46, "segoeuib.ttf")
    sub = font(24, "segoeui.ttf")
    chip = font(24, "seguisb.ttf")

    d.text((TX, 100 * SCALE), "Your personal AI partner", font=tag1, fill=INK)
    d.text((TX, 176 * SCALE), "Research. Design. Build. Act.", font=tag2, fill=TEAL)
    d.text((TX, 252 * SCALE), "your goals, tracked to done — one voice on your machine",
           font=sub, fill=MUTED)

    # chips row (fills are pre-blended so text keeps high contrast)
    chips = [
        ("Windows 11", TEAL, blend(TEAL, LOCAL_BG, 0.14)),
        ("Linux / WSLg", GOLD, blend(GOLD, LOCAL_BG, 0.14)),
        ("PyPI · CLI", NEUTRAL_BORDER, blend((150, 160, 175), LOCAL_BG, 0.12)),
    ]
    # measure total width to center the row
    total = 0
    widths = []
    for label, _b, _f in chips:
        bbox = d.textbbox((0, 0), label, font=chip)
        widths.append(bbox[2] - bbox[0] + 44 * SCALE)
        total += widths[-1]
    total += (len(chips) - 1) * 18 * SCALE
    cx = TX + total // 2
    cy = 322 * SCALE
    for (label, border, fill), w in zip(chips, widths, strict=True):
        rounded_chip(d, cx, cy, label, chip, border, fill)
        cx += w + 18 * SCALE

    # downscale for crispness
    banner = img.resize((W, H), Image.LANCZOS)
    banner.save(OUT, optimize=True)
    print(f"wrote {OUT}  {banner.size}")


if __name__ == "__main__":
    main()
