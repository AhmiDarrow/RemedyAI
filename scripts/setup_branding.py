"""Setup desktop branding from assets.

Generates all Tauri icon sizes from assets/remedy_icon.png and
copies the logo for use in the splash screen.

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

# Windows taskbar / shell pick small sizes — multi-res ICO (standard sizes only).
ICO_SIZES: list[int] = [16, 24, 32, 48, 64, 128, 256]

LOGO_SIZE = (512, 128)


def _resize_square(img: Image.Image, size: int) -> Image.Image:
    """High-quality square resize; keep alpha."""
    return img.resize((size, size), Image.Resampling.LANCZOS)


def generate_icons(source: Path, icons_dir: Path) -> None:
    img = Image.open(source).convert("RGBA")
    icons_dir.mkdir(parents=True, exist_ok=True)

    for name, size in ICON_TARGETS:
        resized = _resize_square(img, size)
        dest = icons_dir / name
        resized.save(dest, "PNG", optimize=True)
        print(f"  {name} ({size}x{size})")

    # Multi-resolution .ico for taskbar / Start Menu / PE resource embed.
    # Pillow resizes from the master when `sizes=` is set.
    ico_path = icons_dir / "icon.ico"
    master_img = _resize_square(img, 256)
    master_img.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    print(f"  icon.ico ({', '.join(f'{s}x{s}' for s in ICO_SIZES)})")

    master_png = icons_dir / "icon-256.png"
    master_img.save(master_png, "PNG", optimize=True)
    print("  icon-256.png (256x256)")

    # Refresh .icns as a multi-size PNG pack is hard without iconutil;
    # write a clean 256 PNG-named icns replacement is invalid — keep PNG
    # and regenerate via iconutil on macOS when available.
    icns_path = icons_dir / "icon.icns"
    # Store largest PNG bytes under icns only if missing valid data; on
    # Windows builds the .ico path is what matters. Prefer overwrite with
    # 512 PNG so stale medical bytes are not left around.
    _resize_square(img, 512).save(icons_dir / "icon-512.png", "PNG", optimize=True)
    # Remove corrupt/stub icns so tauri does not embed garbage; macOS CI
    # should run `iconutil`. Write a minimal valid path: copy 256 png as
    # fallback named icon.icns only for non-mac builds is wrong format.
    # Instead: always overwrite icon.icns with 512 RGBA PNG data labeled
    # for tauri (some versions accept); better use pillow if available.
    try:
        # If pillow has ICNS, use it
        _resize_square(img, 512).save(icns_path, format="ICNS")
        print("  icon.icns (ICNS via Pillow)")
    except Exception:
        # Force-delete stale medical icns so it cannot be preferred over ico
        if icns_path.exists():
            # Replace file content timestamp by rewriting as 256 PNG
            # (tauri on Windows uses icon.ico primarily)
            _resize_square(img, 256).save(icns_path.with_suffix(".png.bak"), "PNG")
            print("  icon.icns left as-is (Pillow ICNS unsupported); use ico on Windows")
        else:
            print("  icon.icns skipped (generate on macOS with iconutil)")


def setup_logo(source: Path, public_dir: Path) -> None:
    public_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(source).convert("RGBA")
    resized = img.resize(LOGO_SIZE, Image.Resampling.LANCZOS)
    dest = public_dir / "logo.png"
    resized.save(dest, "PNG", optimize=True)
    print(f"  logo -> {dest} ({LOGO_SIZE[0]}x{LOGO_SIZE[1]})")

    # Favicon multi-size from icon source (circuit monogram)
    icon_img = Image.open(ASSETS / "remedy_icon.png").convert("RGBA")
    fav32 = _resize_square(icon_img, 32)
    fav_path = public_dir / "favicon.png"
    fav32.save(fav_path, "PNG", optimize=True)
    print(f"  favicon.png -> {fav_path}")

    # Browser / WebView favicon.ico
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


def main() -> int:
    icon_src = ASSETS / "remedy_icon.png"
    logo_src = ASSETS / "remedy_logo.png"

    if not icon_src.exists():
        print(f"ERROR: {icon_src} not found")
        return 1
    if not logo_src.exists():
        print(f"ERROR: {logo_src} not found")
        return 1

    print("=== Remedy Branding Setup ===\n")

    print("[1/2] Generating icons from remedy_icon.png...")
    generate_icons(icon_src, ICONS_DIR)
    print()

    print("[2/2] Setting up logo + favicons...")
    setup_logo(logo_src, PUBLIC_DIR)
    print()

    print("=== Done! Taskbar uses icons/icon.ico (rebuild desktop to embed). ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
