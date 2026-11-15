"""WebUI assets bundled in the pip wheel.

Release builds stage the built desktop frontend (``desktop/dist``) into
``remedy/webui/dist`` with ``scripts/stage_wheel_webui.py`` before ``uv build``.
``remedy serve`` hands that folder to remedy-runtime as
``REMEDY_BUNDLED_WEBUI_DIR``, the last place it looks for the browser UI, so an
explicit ``REMEDY_WEBUI_DIR`` or a live ``desktop/dist`` checkout still wins.
A source checkout has no ``dist`` folder here; that is expected.
"""

from __future__ import annotations

import json
from pathlib import Path

WEBUI_DIST_DIR = Path(__file__).resolve().parent / "dist"
MANIFEST_NAME = "remedy-webui.json"


def bundled_webui_dir() -> Path | None:
    """Return the bundled WebUI folder, or None when this install has none."""
    index = WEBUI_DIST_DIR / "index.html"
    if index.is_file() and (WEBUI_DIST_DIR / "assets").is_dir():
        return WEBUI_DIST_DIR
    return None


def bundled_webui_version() -> str | None:
    """Version recorded when the WebUI was staged, if any."""
    try:
        data = json.loads((WEBUI_DIST_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None
