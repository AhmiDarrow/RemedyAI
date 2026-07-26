"""Setup desktop branding from alpha assets.

Generates all Tauri icon sizes from assets/remedy_icon.png (true RGBA alpha)
and copies the wordmark logo for splash / About / Setup / TitleBar.

Usage:
    python scripts/setup_branding.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS_DIR = ROOT / "desktop" / "src-tauri" / "icons"
PUBLIC_DIR = ROOT / "desktop" / "public"
DIST_DIR = ROOT / "desktop" / "dist"

ICON_TARGETS: list[tuple[str, int]] = [
    ("32x32.png", 32),
    ("128x128.png", 128),
    ("128x128@2x.png", 256),
    ("icon.png", 256),
    ("Square30x30Logo.png", 30),
    ("Square44x44Logo.png", 44),
    ("Square71x71Logo.png", 71),
    ("Square89x89Logo.png", 89),
    ("Square107x107Logo.png", 107),
    ("Square142x142Logo.png", 142),
    ("Square150x150Logo.png", 150),
    ("Square284x284Logo.png", 284),
    ("Square310x310Logo.png", 310),
    ("StoreLogo.png", 100),
]

ICO_SIZES: list[int] = [16, 24, 32, 48, 64, 128, 256]

# Wordmark target box (preserves aspect; alpha canvas).
LOGO_MAX = (640, 320)


def _resize_square(img: Image.Image, size: int) -> Image.Image:
    """High-quality square resize on transparent canvas; keep alpha."""
    src = img.convert("RGBA")
    src.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - src.width) // 2
    y = (size - src.height) // 2
    canvas.paste(src, (x, y), src)
    return canvas


def _fit_rgba(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Fit image into box preserving aspect ratio + alpha (no squash)."""
    src = img.convert("RGBA")
    src.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    return src


def _plate_icon(
    img: Image.Image,
    size: int,
    *,
    pad_frac: float = 0.10,
    accent: tuple[int, int, int, int] = (212, 175, 55, 255),
) -> Image.Image:
    """Circuit-R on a dark rounded plate — readable in the Windows tray/taskbar.

    Small sizes (≤48) use less padding + a gold rim so the monogram does not
    disappear into the system tray.
    """
    from PIL import ImageDraw, ImageEnhance, ImageFilter

    src = img.convert("RGBA")
    # At tray sizes, prefer higher-contrast mono-light strokes when available.
    if size <= 48:
        mono = ASSETS / "remedy_icon_mono_light.png"
        if mono.is_file():
            src = Image.open(mono).convert("RGBA")
        # Slightly thricker presence: boost contrast/brightness of strokes.
        src = ImageEnhance.Contrast(src).enhance(1.35)
        src = ImageEnhance.Brightness(src).enhance(1.15)
        pad_frac = min(pad_frac, 0.06)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    plate = Image.new("RGBA", (size, size), (14, 18, 28, 255))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = max(2, size // 5)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    plate.putalpha(mask)
    canvas.paste(plate, (0, 0), plate)

    # Gold rim for tray contrast on light taskbars.
    if size <= 64:
        rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rim)
        inset = max(1, size // 32)
        rd.rounded_rectangle(
            (inset, inset, size - 1 - inset, size - 1 - inset),
            radius=max(1, radius - inset),
            outline=accent,
            width=max(1, size // 24),
        )
        canvas = Image.alpha_composite(canvas, rim)

    inner = max(8, int(size * (1.0 - 2 * pad_frac)))
    glyph = _resize_square(src, inner)
    # Tiny soft glow under strokes so thin traces stay visible at 16–32px.
    if size <= 48:
        glow = glyph.filter(ImageFilter.GaussianBlur(radius=max(0.6, size / 40)))
        gx = (size - glow.width) // 2
        gy = (size - glow.height) // 2
        canvas.paste(glow, (gx, gy), glow)
    x = (size - glyph.width) // 2
    y = (size - glyph.height) // 2
    canvas.paste(glyph, (x, y), glyph)
    return canvas


def generate_icons(source: Path, icons_dir: Path) -> None:
    img = Image.open(source).convert("RGBA")
    icons_dir.mkdir(parents=True, exist_ok=True)

    for name, size in ICON_TARGETS:
        resized = _resize_square(img, size)
        dest = icons_dir / name
        resized.save(dest, "PNG", optimize=True)
        print(f"  {name} ({size}x{size})")

    # Multi-size ICO (Windows taskbar/Start need 16/32/48, not only 256).
    # Pillow: save from the largest square with a sizes= list (it downscales each).
    ico_path = icons_dir / "icon.ico"
    master_for_ico = _resize_square(img, max(ICO_SIZES))
    master_for_ico.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    try:
        check = Image.open(ico_path)
        n = getattr(check, "n_frames", 1)
        sizes_found: list[str] = []
        for i in range(n):
            check.seek(i)
            sizes_found.append(f"{check.size[0]}x{check.size[1]}")
        print(f"  icon.ico frames={n} ({', '.join(sizes_found)})")
        if n < 3:
            # Fallback: write a 256-only ICO that at least has full detail for Win11.
            _resize_square(img, 256).save(ico_path, format="ICO")
            print("  icon.ico fallback → single 256x256 (Pillow multi-size sparse)")
    except Exception as exc:
        print(f"  icon.ico written; verify failed: {exc}")

    master_img = _resize_square(img, 256)
    master_png = icons_dir / "icon-256.png"
    master_img.save(master_png, "PNG", optimize=True)
    print("  icon-256.png (256x256)")

    # Tray: bold plate (not template-tinted thin monogram).
    # Ship 32 + 64; conf points at 32, OS may pick @2x when available as icon.png plate.
    for tray_name, tray_size in (
        ("tray-icon.png", 32),
        ("tray-icon@2x.png", 64),
        ("tray-icon-48.png", 48),
    ):
        plate = _plate_icon(img, tray_size)
        plate.save(icons_dir / tray_name, "PNG", optimize=True)
        print(f"  {tray_name} ({tray_size}x{tray_size} plate)")

    # Optional larger plate for high-DPI tray / about
    _plate_icon(img, 256).save(icons_dir / "icon-plate-256.png", "PNG", optimize=True)
    print("  icon-plate-256.png (256x256 plate)")

    _resize_square(img, 512).save(icons_dir / "icon-512.png", "PNG", optimize=True)
    icns_path = icons_dir / "icon.icns"
    try:
        _resize_square(img, 512).save(icns_path, format="ICNS")
        print("  icon.icns (ICNS via Pillow)")
    except Exception:
        print("  icon.icns skipped (generate on macOS with iconutil)")

def setup_public_branding(icon_source: Path, logo_source: Path, public_dir: Path) -> None:
    """Write UI-facing public assets used by Vite/WebUI (logo, icon, favicons)."""
    public_dir.mkdir(parents=True, exist_ok=True)

    # Wordmark (splash, About, Setup, TitleBar) — preserve 2:1 aspect + alpha.
    logo_img = Image.open(logo_source).convert("RGBA")
    logo_resized = _fit_rgba(logo_img, LOGO_MAX[0], LOGO_MAX[1])
    logo_dest = public_dir / "logo.png"
    logo_resized.save(logo_dest, "PNG", optimize=True)
    print(f"  logo.png -> {logo_dest} ({logo_resized.size[0]}x{logo_resized.size[1]})")

    # Optional mono wordmarks for light/dark chrome (if masters exist).
    for mono_name, out_name in (
        ("remedy_logo_mono_light.png", "logo-mono-light.png"),
        ("remedy_logo_mono_dark.png", "logo-mono-dark.png"),
        ("remedy_icon_mono_light.png", "icon-mono-light.png"),
        ("remedy_icon_mono_dark.png", "icon-mono-dark.png"),
    ):
        mono_src = ASSETS / mono_name
        if mono_src.is_file():
            m = Image.open(mono_src).convert("RGBA")
            if "logo" in mono_name:
                m = _fit_rgba(m, LOGO_MAX[0], LOGO_MAX[1])
            else:
                m = _resize_square(m, 256)
            dest = public_dir / out_name
            m.save(dest, "PNG", optimize=True)
            print(f"  {out_name} -> {dest}")

    # Circuit-R monogram used by RemedyLogo, notifications, empty states.
    # True alpha (no baked black plate) so chat avatars composite on theme bg.
    icon_img = Image.open(icon_source).convert("RGBA")
    icon_dest = public_dir / "icon.png"
    _resize_square(icon_img, 256).save(icon_dest, "PNG", optimize=True)
    print(f"  icon.png -> {icon_dest} (256x256 alpha)")

    # Bold plate variant for UI spots that need higher contrast (optional).
    plate = _plate_icon(icon_img, 256)
    plate_dest = public_dir / "icon-plate.png"
    plate.save(plate_dest, "PNG", optimize=True)
    print(f"  icon-plate.png -> {plate_dest}")
    fav32 = _resize_square(icon_img, 32)
    fav_path = public_dir / "favicon.png"
    fav32.save(fav_path, "PNG", optimize=True)
    print(f"  favicon.png -> {fav_path}")

    fav_ico = public_dir / "favicon.ico"
    sizes = [16, 32, 48]
    frames = [_resize_square(icon_img, s) for s in sizes]
    frames[0].save(
        fav_ico,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"  favicon.ico -> {fav_ico}")

    # Replace stock Vite SVG with a transparent PNG-backed hint (browsers use favicon.png).
    fav_svg = public_dir / "favicon.svg"
    fav_svg.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">\n'
        '  <image href="favicon.png" width="64" height="64" preserveAspectRatio="xMidYMid meet"/>\n'
        "</svg>\n",
        encoding="utf-8",
    )
    print("  favicon.svg -> references favicon.png")


def sync_dist_branding(public_dir: Path, dist_dir: Path) -> None:
    """Copy public brand files into Vite dist when present (local install webui)."""
    if not dist_dir.is_dir():
        return
    for name in (
        "logo.png",
        "icon.png",
        "icon-plate.png",
        "favicon.png",
        "favicon.ico",
        "favicon.svg",
        "logo-mono-light.png",
        "logo-mono-dark.png",
        "icon-mono-light.png",
        "icon-mono-dark.png",
    ):
        src = public_dir / name
        if src.is_file():
            dest = dist_dir / name
            dest.write_bytes(src.read_bytes())
            print(f"  dist/{name}")


def main() -> int:
    icon_src = ASSETS / "remedy_icon.png"
    logo_src = ASSETS / "remedy_logo.png"

    if not icon_src.exists():
        print(f"ERROR: {icon_src} not found")
        return 1
    if not logo_src.exists():
        print(f"ERROR: {logo_src} not found")
        return 1

    # Sanity: masters must be true alpha (not baked navy BG).
    for label, path in (("icon", icon_src), ("logo", logo_src)):
        im = Image.open(path).convert("RGBA")
        a_min = im.getextrema()[3][0]
        if a_min > 10:
            print(
                f"WARNING: {path.name} may lack transparency "
                f"(alpha min={a_min}). Expected alpha masters."
            )
        else:
            print(f"OK {label} master alpha_min={a_min} size={im.size}")

    print("=== Remedy Branding Setup ===\n")

    print("[1/3] Generating Tauri icons from remedy_icon.png...")
    generate_icons(icon_src, ICONS_DIR)
    print()

    print("[2/3] Setting up public logo + icon + favicons...")
    setup_public_branding(icon_src, logo_src, PUBLIC_DIR)
    print()

    print("[3/3] Syncing desktop/dist when present...")
    sync_dist_branding(PUBLIC_DIR, DIST_DIR)
    print()

    print("=== Done! ===")
    print("  Masters: assets/remedy_{icon,logo}.png (+ mono variants)")
    print("  UI: desktop/public/{logo,icon,favicon}.*")
    print("  Shell: desktop/src-tauri/icons/icon.ico (rebuild desktop to embed tray/taskbar).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
