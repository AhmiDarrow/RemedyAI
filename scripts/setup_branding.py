"""Setup desktop branding from assets.

Generates all Tauri icon sizes from assets/remedy_icon.png and
copies the logo for use in the splash screen.

Usage:
    python scripts/setup_branding.py
"""

from __future__ import annotations

import shutil
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

LOGO_SIZE = (512, 128)


def generate_icons(source: Path, icons_dir: Path) -> None:
    img = Image.open(source).convert("RGBA")
    icons_dir.mkdir(parents=True, exist_ok=True)

    for name, size in ICON_TARGETS:
        resized = img.resize((size, size), Image.LANCZOS)
        dest = icons_dir / name
        resized.save(dest, "PNG")
        print(f"  {name} ({size}x{size})")

    # Generate .ico (Windows only, but we include 256x256 + 48x48 + 32x32 + 16x16)
    ico_path = icons_dir / "icon.ico"
    ico_sizes = [(256, 256), (48, 48), (32, 32), (16, 16)]
    ico_frames = [img.resize(s, Image.LANCZOS) for s in ico_sizes]
    ico_frames[0].save(
        ico_path,
        format="ICO",
        sizes=[(s[0], s[1]) for s in ico_sizes],
    )
    print(f"  icon.ico ({', '.join(f'{w}x{h}' for w, h in ico_sizes)})")

    # Generate .icns stub (just a PNG - macOS only)
    icns_path = icons_dir / "icon.icns"
    if not icns_path.exists():
        img256 = img.resize((256, 256), Image.LANCZOS)
        icns_path.write_bytes(img256.tobytes())
        print(f"  icon.icns (stub)")


def setup_logo(source: Path, public_dir: Path) -> None:
    public_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(source).convert("RGBA")
    resized = img.resize(LOGO_SIZE, Image.LANCZOS)
    dest = public_dir / "logo.png"
    resized.save(dest, "PNG")
    print(f"  logo -> {dest} ({LOGO_SIZE[0]}x{LOGO_SIZE[1]})")

    # Favicon from icon source
    icon_img = Image.open(ASSETS / "remedy_icon.png").convert("RGBA")
    fav = icon_img.resize((32, 32), Image.LANCZOS)
    fav_path = public_dir / "favicon.png"
    fav.save(fav_path, "PNG")
    print(f"  favicon -> {fav_path}")


def main():
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

    print("[2/2] Setting up logo for splash screen...")
    setup_logo(logo_src, PUBLIC_DIR)
    print()

    print("=== Done! ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
