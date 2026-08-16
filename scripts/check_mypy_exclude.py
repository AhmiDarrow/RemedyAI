"""Fail if the mypy exclude list grows.

The lock file is the maximum allow-list. ``pyproject.toml`` ``[tool.mypy]
exclude`` must be a subset. The lock itself may only lose lines versus
``origin/master`` (when that ref is available).

Win32-only modules stay on the lock until a Windows mypy job types them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "scripts" / "mypy_exclude.lock"
PYPROJECT = ROOT / "pyproject.toml"


def _paths_from_text(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line.replace("\\", "/"))
    return out


def _pyproject_exclude() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    raw = data.get("tool", {}).get("mypy", {}).get("exclude", [])
    if not isinstance(raw, list):
        raise SystemExit("pyproject.toml [tool.mypy] exclude must be a list")
    return [str(p).replace("\\", "/") for p in raw]


def _lock_paths() -> list[str]:
    if not LOCK.is_file():
        raise SystemExit(f"missing lock: {LOCK}")
    return _paths_from_text(LOCK.read_text(encoding="utf-8"))


def _origin_lock_paths() -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "show", "origin/master:scripts/mypy_exclude.lock"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _paths_from_text(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    current = _pyproject_exclude()
    allowed = set(_lock_paths())
    extras = [p for p in current if p not in allowed]
    if extras:
        print("mypy exclude grew beyond scripts/mypy_exclude.lock:")
        for p in extras:
            print(f"  + {p}")
        print("Type the module, or (owner only) shrink-never add to the lock.")
        return 1

    unused = sorted(allowed - set(current))
    if unused:
        print("lock still lists paths already typed in pyproject (shrink the lock):")
        for p in unused:
            print(f"  - {p}")
        return 1

    origin = _origin_lock_paths()
    if origin is not None:
        grew = sorted(set(_lock_paths()) - set(origin))
        if grew:
            print("scripts/mypy_exclude.lock may only shrink vs origin/master:")
            for p in grew:
                print(f"  + {p}")
            return 1

    print(f"ok: mypy exclude {len(current)} paths (lock-matched, not grown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
