#!/usr/bin/env python3
"""Check downloaded GitHub release assets against remedy-native-SHA256SUMS.txt.

Used by Desktop Release's verify-native-assets job on macOS and Linux ARM
runners after the tag is published. Exit 0 only when every SUMS entry whose
name contains one of the given triples is present and the sha256 matches.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def parse_sums(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        digest, name = raw.split(maxsplit=1)
        if name.startswith("*"):
            name = name[1:]
        rows.append((digest.lower(), name))
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("triples", nargs="+")
    args = parser.parse_args(argv)
    sums_path = args.directory / "remedy-native-SHA256SUMS.txt"
    if not sums_path.is_file():
        print(f"missing {sums_path}", file=sys.stderr)
        return 1
    wanted = parse_sums(sums_path.read_text(encoding="utf-8"))
    rows = [
        (digest, name)
        for digest, name in wanted
        if any(triple in name for triple in args.triples)
    ]
    if not rows:
        print(f"no SUMS entries for triples {args.triples}", file=sys.stderr)
        return 1
    missing: list[str] = []
    mismatch: list[str] = []
    for digest, name in rows:
        path = args.directory / name
        if not path.is_file():
            missing.append(name)
            continue
        actual = sha256_file(path)
        if actual != digest:
            mismatch.append(f"{name}: {actual} != {digest}")
            continue
        print(f"ok {name}")
    if missing or mismatch:
        if missing:
            print("missing:", ", ".join(missing), file=sys.stderr)
        if mismatch:
            print("mismatch:", "; ".join(mismatch), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
