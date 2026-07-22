"""Version synchronization script for Remedy AI.

Updates version numbers consistently across all package manifests:
- pyproject.toml          (Python package)
- desktop/package.json    (Node frontend)
- desktop/src-tauri/tauri.conf.json  (Tauri app)

Usage:
    python scripts/sync_version.py          # check current version
    python scripts/sync_version.py 0.9.1    # bump to specific version
    python scripts/sync_version.py patch    # bump patch version
    python scripts/sync_version.py minor    # bump minor version
    python scripts/sync_version.py major    # bump major version
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATHS = {
    "pyproject": ROOT / "pyproject.toml",
    "package": ROOT / "desktop" / "package.json",
    "tauri": ROOT / "desktop" / "src-tauri" / "tauri.conf.json",
    "latest_json": ROOT / "scripts" / "latest.json",
}


def _pyproject_version() -> str:
    text = PATHS["pyproject"].read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def _bump_pyproject(ver: str) -> None:
    text = PATHS["pyproject"].read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*)"[^"]*"', rf'\1"{ver}"', text, flags=re.MULTILINE)
    PATHS["pyproject"].write_text(text, encoding="utf-8")


def _bump_package_json(ver: str) -> None:
    data = json.loads(PATHS["package"].read_text(encoding="utf-8"))
    data["version"] = ver
    PATHS["package"].write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bump_tauri_conf(ver: str) -> None:
    text = PATHS["tauri"].read_text(encoding="utf-8")
    text = re.sub(r'"version":\s*"[^"]*"', f'"version": "{ver}"', text)
    PATHS["tauri"].write_text(text, encoding="utf-8")


def _bump_latest_json(ver: str) -> None:
    if not PATHS["latest_json"].exists():
        return
    data = json.loads(PATHS["latest_json"].read_text(encoding="utf-8"))
    data["version"] = ver
    data["pub_date"] = f"{sys.argv[1] if len(sys.argv) > 1 else ''}"
    PATHS["latest_json"].write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bump_version(current: str, target: str) -> str:
    if target in ("patch", "minor", "major"):
        parts = [int(x) for x in current.split(".")]
        if target == "major":
            parts = [parts[0] + 1, 0, 0]
        elif target == "minor":
            parts = [parts[0], parts[1] + 1, 0]
        else:
            parts = [parts[0], parts[1], parts[2] + 1]
        return ".".join(str(p) for p in parts)
    return target


def main():
    current = _pyproject_version()
    print(f"Current version: {current}")

    if len(sys.argv) < 2:
        print(f"  pyproject.toml   = {current}")
        pkg = json.loads(PATHS["package"].read_text(encoding="utf-8"))
        print(f"  package.json     = {pkg.get('version', '?')}")
        taur = PATHS["tauri"].read_text(encoding="utf-8")
        m = re.search(r'"version":\s*"([^"]*)"', taur)
        print(f"  tauri.conf.json  = {m.group(1) if m else '?'}")
        return

    new_ver = _bump_version(current, sys.argv[1])
    print(f"Bumping to: {new_ver}")

    _bump_pyproject(new_ver)
    print(f"  Updated pyproject.toml")

    _bump_package_json(new_ver)
    print(f"  Updated package.json")

    _bump_tauri_conf(new_ver)
    print(f"  Updated tauri.conf.json")

    _bump_latest_json(new_ver)
    print(f"  Updated scripts/latest.json")

    print(f"\nDone! Version bumped from {current} -> {new_ver}")


if __name__ == "__main__":
    main()
