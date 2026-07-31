"""Resolve local API Bearer token for live soak scripts.

`~/.remedy/auth/local_api_token` may be:
  - plain token string, or
  - DPAPI-wrapped JSON (not a valid Authorization header).

Falls back to loopback ``GET /api/auth/local-bootstrap``.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def resolve_local_api_token(
    *,
    home: Path | str | None = None,
    base: str | None = None,
) -> str:
    home_p = Path(
        home or os.environ.get("REMEDY_HOME") or (Path.home() / ".remedy")
    ).expanduser()
    path = home_p / "auth" / "local_api_token"
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw and not raw.lstrip().startswith("{"):
            # reject whitespace/newlines that break HTTP headers
            if "\n" not in raw and "\r" not in raw:
                return raw
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
    return tok
