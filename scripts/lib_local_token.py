"""Resolve local API Bearer token for live soak scripts.

`~/.remedy/auth/local_api_token` may be:
  - plain token string, or
  - DPAPI-wrapped JSON (not a valid Authorization header).

Prefer the product decoder (DPAPI), then plain file, then loopback
``GET /api/auth/local-bootstrap``.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Allow ``import remedy…`` when scripts run outside the package env."""
    repo = Path(__file__).resolve().parents[1]
    src = repo / "src"
    s = str(src)
    if src.is_dir() and s not in sys.path:
        sys.path.insert(0, s)


def resolve_local_api_token(
    *,
    home: Path | str | None = None,
    base: str | None = None,
) -> str:
    home_p = Path(
        home or os.environ.get("REMEDY_HOME") or (Path.home() / ".remedy")
    ).expanduser()

    # 1) Product path — DPAPI unseal + legacy plain + generate if missing
    try:
        _ensure_src_on_path()
        from remedy.interfaces.local_auth import ensure_local_api_token

        tok = ensure_local_api_token(home=home_p)
        if tok and "\n" not in tok and "\r" not in tok and not tok.lstrip().startswith("{"):
            return tok
    except Exception:
        pass

    # 2) Plaintext file only (reject JSON envelopes — they are sealed)
    path = home_p / "auth" / "local_api_token"
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw and not raw.lstrip().startswith("{"):
            if "\n" not in raw and "\r" not in raw:
                return raw

    # 3) Loopback HTTP bootstrap (when enabled on the running serve)
    api = (base or os.environ.get("REMEDY_API") or "http://127.0.0.1:7400").rstrip(
        "/"
    )
    req = urllib.request.Request(
        f"{api}/api/auth/local-bootstrap",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tok = str(data.get("token") or "").strip()
    if not tok:
        raise RuntimeError(f"local-bootstrap returned no token from {api}")
    if "\n" in tok or "\r" in tok or tok.lstrip().startswith("{"):
        raise RuntimeError(
            "local-bootstrap returned a non-header token (sealed or multiline)"
        )
    return tok
