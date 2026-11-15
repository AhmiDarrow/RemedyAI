"""One-time browser links for the local WebUI token handout.

``/api/auth/local-bootstrap`` hands the API token only to a process of the
same OS user (checked from the TCP peer on Linux and Windows). Where that
check is not available (macOS), a browser needs a single-use link minted by
``POST /api/auth/bootstrap-link``. Minting requires the API token, which only
the owner's account can read, so ``remedy serve`` prints a link at start and
``remedy open`` makes a fresh one.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

LINK_PATH = "/api/auth/bootstrap-link"


def _token(home: Path | str | None) -> str:
    env = (os.environ.get("REMEDY_API_KEY") or "").strip()
    if env:
        return env
    from remedy.interfaces.local_auth import load_local_api_token

    return load_local_api_token(home)


def mint_bootstrap_link(base: str, token: str, *, timeout: float = 5.0) -> str:
    """Ask a running remedy-runtime for a one-time WebUI link ("" on failure)."""
    if not token:
        return ""
    req = urllib.request.Request(
        base.rstrip("/") + LINK_PATH,
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read(64 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""
    url = body.get("url") if isinstance(body, dict) else None
    return url if isinstance(url, str) and "?bootstrap=" in url else ""


def wait_for_link(
    base: str,
    home: Path | str | None,
    *,
    deadline_s: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll until the runtime answers and the token file exists, then mint."""
    end = clock() + deadline_s
    while clock() < end:
        token = _token(home)
        if token:
            link = mint_bootstrap_link(base, token, timeout=3.0)
            if link:
                return link
        sleep(0.5)
    return ""
