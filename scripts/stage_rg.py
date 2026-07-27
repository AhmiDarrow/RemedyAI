#!/usr/bin/env python3
"""Download pinned ripgrep into ~/.remedy/bin and/or third_party/ripgrep/bin.

Usage:
  uv run python scripts/stage_rg.py
  uv run python scripts/stage_rg.py --also-repo-bin
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage ripgrep for Remedy")
    parser.add_argument(
        "--also-repo-bin",
        action="store_true",
        help="Copy installed rg into third_party/ripgrep/bin for packaging",
    )
    parser.add_argument(
        "--home",
        default="",
        help="Remedy home dir (default ~/.remedy)",
    )
    args = parser.parse_args()

    # Ensure package import when run from repo
    repo = Path(__file__).resolve().parents[1]
    src = repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from remedy.core.rg_binary import ensure_rg

    home = args.home.strip() or None
    info = ensure_rg(home, download=True)
    print(info)
    if not info.get("ok"):
        return 1

    if args.also_repo_bin and info.get("path"):
        dest_dir = repo / "third_party" / "ripgrep" / "bin"
        dest_dir.mkdir(parents=True, exist_ok=True)
        src_bin = Path(str(info["path"]))
        dest = dest_dir / src_bin.name
        shutil.copy2(src_bin, dest)
        print(f"copied -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
