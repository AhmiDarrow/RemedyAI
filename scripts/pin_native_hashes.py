#!/usr/bin/env python3
"""Embed the release's runtime checksums in the wheel (PyPI build step).

``pip install remedy-ai`` downloads ``remedy-runtime`` / ``remedy_core`` on
first run. The wheel is the trust anchor: it must carry the expected sha256 of
every runtime asset of *its own* version, taken from the release build
artifacts, so a later swap of a GitHub release asset or of
``remedy-native-SHA256SUMS.txt`` is refused on the user's machine.

Release order (see docs/DESKTOP.md, "PyPI wheel"):

  1. The Desktop Release workflow publishes ``runtime-v<ver>`` with
     ``remedy-native-SHA256SUMS.txt`` (built and hashed in the same job).
  2. Pin, from that file, and verify every listed asset against it:
       uv run python scripts/pin_native_hashes.py --from-release --verify-assets
     or from a downloaded workflow artifact:
       uv run python scripts/pin_native_hashes.py --sums dist-native/remedy-native-SHA256SUMS.txt
  3. ``uv run python scripts/pin_native_hashes.py --check`` then ``uv build``.

Do not commit the filled-in ``native_pins.json``; the source tree stays
unpinned (a checkout builds its own runtime).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remedy.runtime import native_bootstrap as nb  # noqa: E402

PINS = ROOT / "src" / "remedy" / "runtime" / nb.PINS_NAME
REPO = "AhmiDarrow/RemedyAI"


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"]).strip()


def required_assets() -> list[str]:
    names: set[str] = set()
    for assets in nb._PLATFORM_ASSETS.values():
        names.add(assets.runtime_asset)
        names.add(assets.core_asset)
    return sorted(names)


def build_pins(version: str, sums: dict[str, str]) -> dict[str, object]:
    missing = [name for name in required_assets() if name not in sums]
    if missing:
        raise SystemExit(f"checksum file is missing runtime assets: {', '.join(missing)}")
    pinned = {name: sums[name] for name in required_assets()}
    return {
        "_note": f"Pinned from {nb.runtime_release_tag(version)} build artifacts.",
        "version": version,
        "release_tag": nb.runtime_release_tag(version),
        "sha256": pinned,
    }


def gh_download(tag: str, pattern: str, dest: Path) -> None:
    subprocess.run(
        ["gh", "release", "download", tag, "-R", REPO, "-p", pattern, "-D", str(dest), "--clobber"],
        check=True,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(version: str) -> int:
    try:
        raw = json.loads(PINS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pins: cannot read {PINS}: {exc}")
        return 1
    pins = nb.parse_pins(raw)
    if pins is None:
        print(f"pins: {PINS.name} is unpinned; run --from-release or --sums first")
        return 1
    if pins.version != version:
        print(f"pins: {PINS.name} pins {pins.version}, but pyproject is {version}")
        return 1
    missing = [name for name in required_assets() if name not in pins.sha256]
    if missing:
        print(f"pins: missing {', '.join(missing)}")
        return 1
    print(f"pins: OK, {len(pins.sha256)} runtime assets pinned for {version}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--version", default="", help="defaults to pyproject version")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--sums", type=Path, help="local remedy-native-SHA256SUMS.txt")
    src.add_argument("--from-release", action="store_true", help="download from runtime-v<ver>")
    parser.add_argument(
        "--verify-assets",
        action="store_true",
        help="with --from-release: download every pinned asset and re-hash it",
    )
    parser.add_argument("--check", action="store_true", help="verify the pins match pyproject")
    args = parser.parse_args(argv)
    version = (args.version or pyproject_version()).removeprefix("v")

    if args.check:
        return check(version)

    tag = nb.runtime_release_tag(version)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if args.sums:
            text = args.sums.read_text(encoding="utf-8")
        elif args.from_release:
            gh_download(tag, nb.SUMS_NAME, work)
            text = (work / nb.SUMS_NAME).read_text(encoding="utf-8")
        else:
            parser.error("give --sums FILE, --from-release, or --check")
        sums = nb.parse_sha256sums(text)
        pins = build_pins(version, sums)
        if args.verify_assets:
            if not args.from_release:
                parser.error("--verify-assets needs --from-release")
            for name, digest in pins["sha256"].items():  # type: ignore[union-attr]
                gh_download(tag, name, work)
                actual = sha256_file(work / name)
                if actual != digest:
                    raise SystemExit(f"{name}: release asset sha256 {actual} != {digest}")
                print(f"verified {name}")
    PINS.write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
    print(f"pins: wrote {PINS.relative_to(ROOT)} for {version} ({len(pins['sha256'])} assets)")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
