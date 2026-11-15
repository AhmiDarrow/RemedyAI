#!/usr/bin/env python3
"""Stage the built desktop frontend into the wheel (PyPI build step).

``pip install remedy-ai`` has no Node toolchain, so the browser UI that
``remedy serve`` hands out must ship inside the wheel. This copies
``desktop/dist`` (``cd desktop && npm ci && npm run build``) into
``src/remedy/webui/dist`` and records the version in ``remedy-webui.json``.
The staged folder is gitignored; ``uv build`` still packs it.

  uv run python scripts/stage_wheel_webui.py --build   # npm ci + npm run build first
  uv run python scripts/stage_wheel_webui.py           # reuse an existing desktop/dist
  uv run python scripts/stage_wheel_webui.py --clean   # remove the staged copy

Then ``uv build`` and ``scripts/check_wheel.py dist/*.whl``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
SOURCE = DESKTOP / "dist"
TARGET = ROOT / "src" / "remedy" / "webui" / "dist"
MANIFEST_NAME = "remedy-webui.json"
# Source maps are debug-only and would roughly double the wheel.
SKIP_SUFFIXES = (".map",)


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"]).strip()


def build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found: install Node 20+ to build the WebUI")
    subprocess.run([npm, "ci"], cwd=DESKTOP, check=True)
    subprocess.run([npm, "run", "build"], cwd=DESKTOP, check=True)


def validate_source(source: Path) -> None:
    if not (source / "index.html").is_file():
        raise SystemExit(f"{source}/index.html missing: build the frontend first (--build)")
    assets = source / "assets"
    if not assets.is_dir() or not any(assets.glob("*.js")):
        raise SystemExit(f"{assets} has no JavaScript bundle: build the frontend first")


def stage(source: Path, target: Path, version: str) -> dict[str, object]:
    validate_source(source)
    if target.exists():
        shutil.rmtree(target)
    files: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"refusing symlink in WebUI build: {path}")
        if not path.is_file() or path.name.endswith(SKIP_SUFFIXES):
            continue
        rel = path.relative_to(source).as_posix()
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest: dict[str, object] = {"version": version, "files": files}
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="run npm ci && npm run build in desktop/ first")
    ap.add_argument("--clean", action="store_true", help="remove the staged copy and exit")
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--target", type=Path, default=TARGET)
    args = ap.parse_args(argv)
    if args.clean:
        shutil.rmtree(args.target, ignore_errors=True)
        print(f"removed {args.target}")
        return 0
    if args.build:
        build_frontend()
    manifest = stage(args.source, args.target, pyproject_version())
    files = manifest["files"]
    assert isinstance(files, dict)
    size = sum((args.target / rel).stat().st_size for rel in files)
    print(f"staged WebUI {manifest['version']}: {len(files)} files, {size / 1e6:.1f} MB -> {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
