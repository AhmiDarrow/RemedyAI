"""Personal assistant routes — Google OAuth + status (Phase 1 Calendar)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from remedy.interfaces.api_support import load_config

logger = logging.getLogger(__name__)


def _home_from_config(cfg: dict[str, Any] | None = None) -> Path | None:
    cfg = cfg if cfg is not None else load_config()
    home = cfg.get("home_dir") if isinstance(cfg, dict) else None
    return Path(home).expanduser() if home else None


class GoogleAppConfigRequest(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    clear_secret: bool = False


class GoogleOAuthStartRequest(BaseModel):
    """Optional redirect override (must match Google Cloud console)."""

    redirect_uri: str | None = Field(default=None, max_length=512)


def register_assistant_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register /api/assistant/* endpoints."""

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

    @app.get("/api/assistant/google")
    async def google_status():
        from remedy.assistant.google_oauth import public_status

        return public_status(_home_from_config())

    @app.put("/api/assistant/google/app")
    async def google_save_app(req: GoogleAppConfigRequest):
        from remedy.assistant.google_oauth import load_app_config, save_app_config

        home = _home_from_config()
        secret = req.client_secret
        if req.clear_secret:
            secret = ""
        elif secret is None:
            # Leave existing secret
            cur = load_app_config(home)
            secret = cur.client_secret
        cfg = save_app_config(
            client_id=req.client_id if req.client_id is not None else load_app_config(home).client_id,
            client_secret=secret,
            redirect_uri=req.redirect_uri,
            home=home,
        )
        return {"status": "ok", "app": cfg.to_public()}

    @app.post("/api/assistant/google/oauth/start")
    async def google_oauth_start(req: GoogleOAuthStartRequest | None = None):
        from remedy.assistant.google_oauth import start_oauth

        home = _home_from_config()
        try:
            return start_oauth(
                home=home,
                redirect_uri=(req.redirect_uri if req else None),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.exception("google oauth start")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/assistant/google/oauth/status")
    async def google_oauth_poll(state: str = Query(..., min_length=4)):
        from remedy.assistant.google_oauth import load_tokens, pending_status

        home = _home_from_config()
        st = pending_status(state)
        tokens = load_tokens(home)
        return {
            **st,
            "credentials": tokens.to_public(),
        }

    @app.get("/api/assistant/google/callback")
    async def google_oauth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        """Browser redirect target after Google consent (no API bearer)."""
        if error:
            msg = error_description or error
            return HTMLResponse(
                _html_page("Google sign-in failed", f"<p>{_esc(msg)}</p><p>Close this tab and try again in Settings.</p>"),
                status_code=400,
            )
        if not code or not state:
            return HTMLResponse(
                _html_page("Missing code", "<p>OAuth callback incomplete. Start Connect again from Settings.</p>"),
                status_code=400,
            )
        from remedy.assistant.google_oauth import complete_oauth

        home = _home_from_config()
        try:
            tokens = complete_oauth(code=code, state=state, home=home)
        except Exception as e:
            logger.warning("google oauth callback: %s", e)
            return HTMLResponse(
                _html_page("Could not finish Google sign-in", f"<p>{_esc(str(e))}</p>"),
                status_code=400,
            )
        email = tokens.email or "connected"
        return HTMLResponse(
            _html_page(
                "Google connected",
                f"<p>Signed in as <strong>{_esc(email)}</strong>.</p>"
                "<p>You can close this tab and return to Remedy → Settings → Personal assistant.</p>",
            )
        )

    @app.delete("/api/assistant/google")
    async def google_disconnect():
        from remedy.assistant.google_oauth import disconnect, public_status

        home = _home_from_config()
        disconnect(home)
        return {"status": "ok", "google": public_status(home)}


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>{_esc(title)} — Remedy</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0f1419;color:#e7ecf1;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{max-width:28rem;padding:1.5rem;border:1px solid #2a3540;border-radius:12px;background:#1a222c}}
h1{{font-size:1.15rem;margin:0 0 .75rem}} p{{line-height:1.45;color:#a8b3bf;font-size:.9rem}}
strong{{color:#e7ecf1}}
</style></head><body><div class="card"><h1>{_esc(title)}</h1>{body}</div></body></html>"""
