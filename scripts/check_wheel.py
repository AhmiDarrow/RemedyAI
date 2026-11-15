#!/usr/bin/env python3
"""Refuse a remedy-ai wheel that would ship without its browser UI.

  uv run python scripts/check_wheel.py dist/remedy_ai-*.whl            # CI
  uv run python scripts/check_wheel.py --release dist/remedy_ai-*.whl  # PyPI upload

Always required: ``remedy/webui/dist/index.html``, a JavaScript bundle under
``remedy/webui/dist/assets/``, and a ``remedy-webui.json`` manifest whose
version matches the wheel and whose file hashes match the packed files.
``--release`` also requires runtime pins (``native_pins.json``) for the wheel's
own version, so the first-run runtime download has something to verify against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remedy.runtime import native_bootstrap as nb  # noqa: E402

WEBUI = "remedy/webui/dist/"
MANIFEST = WEBUI + "remedy-webui.json"
PINS = "remedy/runtime/" + nb.PINS_NAME
# Guard against an accidental node_modules / source-map dump in the wheel.
MAX_WHEEL_BYTES = 12_000_000


def wheel_version(path: Path) -> str:
    parts = path.name.split("-")
    if len(parts) < 3 or not path.name.endswith(".whl"):
        raise SystemExit(f"{path.name}: not a wheel file name")
    return parts[1]


def check(path: Path, *, release: bool) -> list[str]:
    errors: list[str] = []
    version = wheel_version(path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if WEBUI + "index.html" not in names:
            errors.append(f"missing {WEBUI}index.html (run scripts/stage_wheel_webui.py before uv build)")
        if not any(n.startswith(WEBUI + "assets/") and n.endswith(".js") for n in names):
            errors.append(f"no JavaScript bundle under {WEBUI}assets/")
        if any(n.endswith(".map") and n.startswith(WEBUI) for n in names):
            errors.append("source maps must not ship in the wheel")
        if MANIFEST not in names:
            errors.append(f"missing {MANIFEST}")
        else:
            manifest = json.loads(zf.read(MANIFEST))
            if manifest.get("version") != version:
                errors.append(f"WebUI was staged for {manifest.get('version')!r}, wheel is {version!r}")
            files = manifest.get("files") or {}
            if not files:
                errors.append("WebUI manifest lists no files")
            for rel, digest in files.items():
                name = WEBUI + rel
                if name not in names:
                    errors.append(f"manifest lists {rel} but the wheel lacks it")
                elif hashlib.sha256(zf.read(name)).hexdigest() != digest:
                    errors.append(f"{rel} does not match the manifest hash")
        if release:
            pins = nb.parse_pins(json.loads(zf.read(PINS))) if PINS in names else None
            if pins is None:
                errors.append(f"{PINS} has no runtime pins (run scripts/pin_native_hashes.py --from-release)")
            elif pins.version != version:
                errors.append(f"runtime pins are for {pins.version!r}, wheel is {version!r}")
    size = path.stat().st_size
    if size > MAX_WHEEL_BYTES:
        errors.append(f"wheel is {size / 1e6:.1f} MB, over the {MAX_WHEEL_BYTES / 1e6:.0f} MB guard")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wheels", nargs="+", type=Path)
    ap.add_argument("--release", action="store_true", help="also require runtime pins for this version")
    args = ap.parse_args(argv)
    failed = False
    for wheel in args.wheels:
        errors = check(wheel, release=args.release)
        size = wheel.stat().st_size / 1e6
        if errors:
            failed = True
            print(f"{wheel.name} ({size:.1f} MB): FAIL")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"{wheel.name} ({size:.1f} MB): OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
