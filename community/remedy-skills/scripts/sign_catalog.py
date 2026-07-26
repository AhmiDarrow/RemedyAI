#!/usr/bin/env python3
"""Sign catalog.json with Ed25519 (PyNaCl). Secret: base64 32-byte seed."""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parent.parent


def sign_bytes(message: bytes, secret_b64: str) -> str:
    raw = base64.b64decode(secret_b64.strip())
    if len(raw) != 32:
        raise SystemExit(f"Signing key must be 32 bytes, got {len(raw)}")
    sk = SigningKey(raw)
    sig = sk.sign(message).signature
    return base64.b64encode(sig).decode("ascii")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", type=Path, default=ROOT / "catalog.json")
    p.add_argument(
        "--secret-key",
        default=os.environ.get("REMEDY_SKILLS_SIGNING_KEY", ""),
    )
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()
    if not args.secret_key:
        print("Missing --secret-key / REMEDY_SKILLS_SIGNING_KEY", file=sys.stderr)
        raise SystemExit(2)
    data = args.catalog.read_bytes()
    sig_b64 = sign_bytes(data, args.secret_key)
    out = args.output or Path(str(args.catalog) + ".sig")
    out.write_text(sig_b64 + "\n", encoding="utf-8")
    sk = SigningKey(base64.b64decode(args.secret_key.strip()))
    pub = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    print(f"Signed {args.catalog} -> {out}")
    print(f"Public key (embed in client): {pub}")


if __name__ == "__main__":
    main()
