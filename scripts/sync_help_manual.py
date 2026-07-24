"""Keep docs/manual/*.md and desktop/src/help/articles/*.md in sync.

Canonical source: docs/manual/ (except README.md).
Desktop wiki bundles desktop/src/help/articles/ via Vite.

Usage:
  python scripts/sync_help_manual.py          # copy docs → desktop
  python scripts/sync_help_manual.py check    # exit 1 if drift
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "manual"
DST = ROOT / "desktop" / "src" / "help" / "articles"


def chapter_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.md") if p.name != "README.md")


def check() -> int:
    if not SRC.is_dir() or not DST.is_dir():
        print("manual or articles directory missing")
        return 1
    src_files = {p.name: p for p in chapter_files(SRC)}
    dst_files = {p.name: p for p in chapter_files(DST)}
    bad = 0
    for name in sorted(set(src_files) | set(dst_files)):
        if name not in src_files:
            print(f"  [BAD] only in desktop: {name}")
            bad += 1
            continue
        if name not in dst_files:
            print(f"  [BAD] only in docs: {name}")
            bad += 1
            continue
        a = src_files[name].read_text(encoding="utf-8")
        b = dst_files[name].read_text(encoding="utf-8")
        if a != b:
            print(f"  [BAD] drift: {name}")
            bad += 1
        else:
            print(f"  [OK ] {name}")
    if bad:
        print(f"\n{bad} mismatch(es). Run: python scripts/sync_help_manual.py")
        return 1
    print("\nHelp manual copies aligned.")
    return 0


def sync() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    for src in chapter_files(SRC):
        shutil.copy2(src, DST / src.name)
        print(f"  copied {src.name}")
    # Remove desktop-only chapters that no longer exist in docs
    src_names = {p.name for p in chapter_files(SRC)}
    for dst in chapter_files(DST):
        if dst.name not in src_names:
            dst.unlink()
            print(f"  removed stale {dst.name}")
    return check()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "--check"):
        raise SystemExit(check())
    raise SystemExit(sync())


if __name__ == "__main__":
    main()
