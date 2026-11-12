#!/usr/bin/env python3
"""Run the Windows CI pytest surface listed in tests/windows_ci_paths.txt.

The full suite already runs on Linux 3.12 and 3.13. Public Windows CI is
limited to path/shell/secret-store, spawn/jail/security, FFI/ABI, first-run
bootstrap, and tests that are skipped on non-Windows.

Extra pytest args are forwarded after the path list (default: -q --tb=short).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_PATH = ROOT / "tests" / "windows_ci_paths.txt"


def load_paths() -> list[str]:
    if not LIST_PATH.is_file():
        raise SystemExit(f"missing path list: {LIST_PATH}")
    paths: list[str] = []
    seen: set[str] = set()
    for raw in LIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line in seen:
            raise SystemExit(f"duplicate path in {LIST_PATH}: {line}")
        seen.add(line)
        candidate = ROOT / line
        if not candidate.is_file():
            raise SystemExit(f"path list entry does not exist: {line}")
        paths.append(line)
    if not paths:
        raise SystemExit(f"{LIST_PATH} has no test paths")
    return paths


def main(argv: list[str]) -> int:
    import pytest

    extras = [a for a in argv[1:] if a != "--"]
    if not extras:
        extras = ["-q", "--tb=short"]
    return pytest.main([*extras, *load_paths()])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
