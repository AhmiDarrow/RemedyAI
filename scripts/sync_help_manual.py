"""Keep docs/manual/*.md and the two help article copies in sync.

Canonical source: docs/manual/ (except README.md).
Copies: desktop/src/help/articles/ (web UI, bundled via Vite) and
desktop/remedyui/help/articles/ (RemedyUI desktop). All three stay identical.

Usage:
  python scripts/sync_help_manual.py          # copy docs -> both article dirs
  python scripts/sync_help_manual.py check    # exit 1 if drift
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "manual"
DESTS = (
    ROOT / "desktop" / "src" / "help" / "articles",
    ROOT / "desktop" / "remedyui" / "help" / "articles",
)


def chapter_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.md") if p.name != "README.md")


def _label(dst: Path) -> str:
    return dst.relative_to(ROOT).parts[1] if dst.is_relative_to(ROOT) else str(dst)


def check() -> int:
    if not SRC.is_dir() or not all(d.is_dir() for d in DESTS):
        print("manual or articles directory missing")
        return 1
    src_files = {p.name: p for p in chapter_files(SRC)}
    bad = 0
    for dst in DESTS:
        where = dst.relative_to(ROOT).as_posix()
        dst_files = {p.name: p for p in chapter_files(dst)}
        print(where)
        for name in sorted(set(src_files) | set(dst_files)):
            if name not in src_files:
                print(f"  [BAD] only in {where}: {name}")
                bad += 1
                continue
            if name not in dst_files:
                print(f"  [BAD] only in docs: {name} (missing from {where})")
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
    src_names = {p.name for p in chapter_files(SRC)}
    for dst_dir in DESTS:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in chapter_files(SRC):
            shutil.copy2(src, dst_dir / src.name)
            print(f"  copied {src.name} -> {dst_dir.relative_to(ROOT).as_posix()}")
        # Remove chapters that no longer exist in docs
        for dst in chapter_files(dst_dir):
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
