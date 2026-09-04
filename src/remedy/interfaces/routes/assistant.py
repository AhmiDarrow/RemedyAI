"""TestClient-only: personal assistant status.

Go ``remedy-runtime`` owns production ``:7400``; this registrar is for pytest.
``/api/assistant/google*`` is Go-owned (not registered here).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from remedy.interfaces.api_support import load_config


def _home_from_config(cfg: dict[str, Any] | None = None) -> Path | None:
    cfg = cfg if cfg is not None else load_config()
    home = cfg.get("home_dir") if isinstance(cfg, dict) else None
    return Path(home).expanduser() if home else None


def register_assistant_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register /api/assistant/status (Google OAuth lives on Go httpapi)."""
    _ = (runtime, gateway, memory)

    @app.get("/api/assistant/status")
    async def assistant_status():
        from remedy.assistant.google_oauth import public_status as google_status
        from remedy.assistant.store import get_assistant_store

        home = _home_from_config()
        store = get_assistant_store(home)
        return {
            "assistant": store.public_status(),
            "google": google_status(home),
        }
