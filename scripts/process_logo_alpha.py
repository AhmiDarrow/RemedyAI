"""Cut dark navy backgrounds to alpha, brighten marks, export mono variants."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ASSETS = Path("assets")
PREVIEWS = ASSETS / "previews"


def estimate_bg(rgb: np.ndarray, border: int = 28) -> np.ndarray:
    h, w = rgb.shape[:2]
    b = max(2, min(border, h // 8, w // 8))
    strips = [
        rgb[:b, :, :].reshape(-1, 3),
        rgb[-b:, :, :].reshape(-1, 3),
        rgb[:, :b, :].reshape(-1, 3),
        rgb[:, -b:, :].reshape(-1, 3),
    ]
    border_px = np.concatenate(strips, axis=0)
    return np.median(border_px, axis=0).astype(np.float32)


def make_alpha(rgb: np.ndarray, bg: np.ndarray, soft: float, hard: float) -> np.ndarray:
    diff = rgb.astype(np.float32) - bg.reshape(1, 1, 3)
    # Weight green/red a bit more so teal/gold separate from navy blue
    weights = np.array([1.15, 1.25, 0.75], dtype=np.float32)
    dist = np.sqrt(((diff * weights) ** 2).sum(axis=2))
    alpha = (dist - soft) / max(hard - soft, 1e-6)
    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


def clean_alpha(alpha: np.ndarray, despeckle: bool = True) -> np.ndarray:
    a = alpha.copy()
    a = np.where(a < 0.05, 0.0, a)
    # Smooth edge slightly via PIL
    img = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8), mode="L")
    if despeckle:
        img = img.filter(ImageFilter.MedianFilter(size=3))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    a = np.array(img).astype(np.float32) / 255.0
    # Re-harden very low alpha after blur
    a = np.where(a < 0.04, 0.0, a)
    a = np.clip((a - 0.02) / 0.96, 0.0, 1.0)
    return a


def decontaminate(rgb: np.ndarray, alpha: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = alpha[..., None]
    out = rgb.astype(np.float32).copy()
    edge = (alpha > 0.02) & (alpha < 0.97)
    if edge.any():
        fg = bg.reshape(1, 1, 3) + (out - bg.reshape(1, 1, 3)) / np.clip(a, 0.1, 1.0)
        fg = np.clip(fg, 0, 255)
        out = np.where(edge[..., None], fg, out)
    out = np.where(a < 0.02, 0.0, out)
    return out


def brighten_mark(
    rgb: np.ndarray, alpha: np.ndarray, mid_lift: float = 1.22, contrast: float = 1.1
) -> np.ndarray:
    a = alpha[..., None]
    x = rgb.astype(np.float32) / 255.0
    x = (x - 0.5) * contrast + 0.5
    gamma = 1.0 / mid_lift
    x = np.clip(x, 0, 1) ** gamma
    lum = (0.2126 * x[:, :, 0] + 0.7152 * x[:, :, 1] + 0.0722 * x[:, :, 2])[..., None]
    x = lum + (x - lum) * 1.14
    x = np.clip(x, 0, 1) * 255.0
    return np.where(a > 0.02, x, 0.0).astype(np.float32)


def to_mono_light(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    lum = (
        0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    ).astype(np.float32)
    mask = alpha > 0.08
    norm = np.zeros_like(lum)
    if mask.any():
        lo = np.percentile(lum[mask], 8)
        hi = np.percentile(lum[mask], 98)
        norm = np.clip((lum - lo) / max(hi - lo, 1e-6), 0, 1)
    # Near-white with retained detail
    val = 200 + norm * 55
    mono = np.stack([val, val, val], axis=2)
    return np.where(alpha[..., None] > 0.02, mono, 0.0).astype(np.float32)


def to_mono_dark(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    lum = (
        0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    ).astype(np.float32)
    mask = alpha > 0.08
    norm = np.zeros_like(lum)
    if mask.any():
        lo = np.percentile(lum[mask], 8)
        hi = np.percentile(lum[mask], 98)
        norm = np.clip((lum - lo) / max(hi - lo, 1e-6), 0, 1)
    # Near-black: brighter logo areas slightly lighter for depth
    val = 12 + norm * 42  # ~12-54
    mono = np.stack([val, val, val], axis=2)
    return np.where(alpha[..., None] > 0.02, mono, 0.0).astype(np.float32)


def compose(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    a8 = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    rgb8 = np.clip(rgb, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb8, a8]), "RGBA")


def process_one(
    src_original: Path,
    dest_stem: str,
    soft: float,
    hard: float,
    mid_lift: float,
) -> dict[str, Path]:
    # Always process from original backup if present
    im = Image.open(src_original).convert("RGBA")
    arr = np.array(im)
    rgb = arr[:, :, :3]
    bg = estimate_bg(rgb)
    alpha = make_alpha(rgb, bg, soft=soft, hard=hard)
    alpha = clean_alpha(alpha)
    rgb_d = decontaminate(rgb, alpha, bg)
    rgb_b = brighten_mark(rgb_d, alpha, mid_lift=mid_lift, contrast=1.12)

    out: dict[str, Path] = {}
    color_path = ASSETS / f"{dest_stem}.png"
    compose(rgb_b, alpha).save(color_path, optimize=True)
    out["color"] = color_path

    pl = ASSETS / f"{dest_stem}_mono_light.png"
    pd = ASSETS / f"{dest_stem}_mono_dark.png"
    compose(to_mono_light(rgb_b, alpha), alpha).save(pl, optimize=True)
    compose(to_mono_dark(rgb_b, alpha), alpha).save(pd, optimize=True)
    out["mono_light"] = pl
    out["mono_dark"] = pd

    opaque = float((alpha > 0.5).mean())
    ys, xs = np.where(alpha > 0.08)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None
    print(f"{dest_stem}: bg={bg.tolist()} opaque={opaque:.3f} bbox={bbox}")
    return out


def checkerboard(size: tuple[int, int], cell: int = 16) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, (220, 220, 220))
    px = img.load()
    for y in range(h):
        for x in range(w):
            if ((x // cell) + (y // cell)) % 2 == 0:
                px[x, y] = (190, 190, 190)
    return img


def paste_center(base: Image.Image, overlay: Image.Image, scale: float = 1.0) -> None:
    ov = overlay.copy()
    if scale != 1.0:
        nw = max(1, int(ov.width * scale))
        nh = max(1, int(ov.height * scale))
        ov = ov.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (base.width - ov.width) // 2
    y = (base.height - ov.height) // 2
    base.paste(ov, (x, y), ov)


def make_previews() -> list[Path]:
    PREVIEWS.mkdir(exist_ok=True)
    paths: list[Path] = []

    # --- Icon strip: original | color | monoL | monoD on light + dark + check ---
    icon_files = [
        ("original", ASSETS / "remedy_icon_original.png"),
        ("color", ASSETS / "remedy_icon.png"),
        ("mono light", ASSETS / "remedy_icon_mono_light.png"),
        ("mono dark", ASSETS / "remedy_icon_mono_dark.png"),
    ]
    logo_files = [
        ("original", ASSETS / "remedy_logo_original.png"),
        ("color", ASSETS / "remedy_logo.png"),
        ("mono light", ASSETS / "remedy_logo_mono_light.png"),
        ("mono dark", ASSETS / "remedy_logo_mono_dark.png"),
    ]

    def sheet(title: str, files: list[tuple[str, Path]], tile: int, row_h: int, name: str) -> Path:
        cols = len(files)
        label_h = 36
        header_h = 48
        pad = 24
        width = pad * 2 + cols * tile + (cols - 1) * pad
        # 3 rows: light / dark / checker
        height = header_h + 3 * (label_h + row_h + pad) + pad
        sheet_img = Image.new("RGB", (width, height), (28, 30, 36))

        # header bar
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(sheet_img)
        try:
            font = ImageFont.truetype("arial.ttf", 22)
            font_s = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
            font_s = font
        draw.text((pad, 12), title, fill=(240, 240, 245), font=font)

        row_meta = [
            ("On light UI", (245, 246, 248), (30, 32, 36)),
            ("On dark UI", (18, 20, 26), (220, 222, 230)),
            ("Checker (alpha)", None, (30, 32, 36)),
        ]

        y = header_h
        for row_label, bg_color, fg in row_meta:
            draw.text((pad, y), row_label, fill=(180, 186, 198), font=font_s)
            y += label_h
            for i, (label, fpath) in enumerate(files):
                x = pad + i * (tile + pad)
                if bg_color is None:
                    cell = checkerboard((tile, row_h), cell=12)
                else:
                    cell = Image.new("RGB", (tile, row_h), bg_color)
                mark = Image.open(fpath).convert("RGBA")
                # fit mark inside cell with margin
                margin = 20
                mw, mh = tile - 2 * margin, row_h - 2 * margin
                fit = mark.copy()
                fit.thumbnail((mw, mh), Image.Resampling.LANCZOS)
                # For original (no alpha usefulness), still show as-is
                cx = (tile - fit.width) // 2
                cy = (row_h - fit.height) // 2
                cell_rgba = cell.convert("RGBA")
                cell_rgba.paste(fit, (cx, cy), fit)
                sheet_img.paste(cell_rgba.convert("RGB"), (x, y))
                # caption under? put small label in cell top
                d2 = ImageDraw.Draw(sheet_img)
                d2.text((x + 8, y + 6), label, fill=fg if bg_color != (245, 246, 248) else (40, 44, 52), font=font_s)
            y += row_h + pad

        out = PREVIEWS / name
        sheet_img.save(out, optimize=True, quality=95)
        paths.append(out)
        print("preview", out, sheet_img.size)
        return out

    sheet(
        "Remedy Icon — original vs processed",
        icon_files,
        tile=280,
        row_h=280,
        name="preview_icon_sheet.png",
    )
    sheet(
        "Remedy Logo — original vs processed",
        logo_files,
        tile=320,
        row_h=180,
        name="preview_logo_sheet.png",
    )

    # Individual hero previews for chat (large, on dark + light)
    def hero(src: Path, bg: tuple[int, int, int], out_name: str, canvas: tuple[int, int], scale_fit: float = 0.72) -> Path:
        base = Image.new("RGBA", canvas, bg + (255,))
        mark = Image.open(src).convert("RGBA")
        max_w = int(canvas[0] * scale_fit)
        max_h = int(canvas[1] * scale_fit)
        m = mark.copy()
        m.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        base.paste(m, ((canvas[0] - m.width) // 2, (canvas[1] - m.height) // 2), m)
        out = PREVIEWS / out_name
        base.convert("RGBA").save(out, optimize=True)
        paths.append(out)
        return out

    hero(ASSETS / "remedy_icon.png", (248, 249, 251), "hero_icon_color_on_light.png", (512, 512))
    hero(ASSETS / "remedy_icon.png", (12, 14, 20), "hero_icon_color_on_dark.png", (512, 512))
    hero(ASSETS / "remedy_icon_mono_light.png", (12, 14, 20), "hero_icon_mono_light_on_dark.png", (512, 512))
    hero(ASSETS / "remedy_icon_mono_dark.png", (248, 249, 251), "hero_icon_mono_dark_on_light.png", (512, 512))
    hero(ASSETS / "remedy_logo.png", (248, 249, 251), "hero_logo_color_on_light.png", (900, 360), 0.85)
    hero(ASSETS / "remedy_logo.png", (12, 14, 20), "hero_logo_color_on_dark.png", (900, 360), 0.85)
    hero(ASSETS / "remedy_logo_mono_light.png", (12, 14, 20), "hero_logo_mono_light_on_dark.png", (900, 360), 0.85)
    hero(ASSETS / "remedy_logo_mono_dark.png", (248, 249, 251), "hero_logo_mono_dark_on_light.png", (900, 360), 0.85)

    return paths


def main() -> None:
    icon_src = ASSETS / "remedy_icon_original.png"
    logo_src = ASSETS / "remedy_logo_original.png"
    if not icon_src.exists():
        # first run fallback
        icon_src = ASSETS / "remedy_icon.png"
    if not logo_src.exists():
        logo_src = ASSETS / "remedy_logo.png"

    process_one(icon_src, "remedy_icon", soft=20.0, hard=52.0, mid_lift=1.3)
    process_one(logo_src, "remedy_logo", soft=16.0, hard=42.0, mid_lift=1.26)
    make_previews()
    print("DONE")


if __name__ == "__main__":
    main()
