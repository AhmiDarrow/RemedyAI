"""Inbound webhooks for WhatsApp, Teams, Google Chat (public URL messengers)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

_WEBHOOK_MAX_BYTES = 2 * 1024 * 1024


async def _read_body_capped(
    request: Request,
    *,
    max_bytes: int = _WEBHOOK_MAX_BYTES,
) -> bytes:
    """Reject oversized Content-Length and stream-cap before HMAC/JWT verify."""
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > max_bytes:
                raise HTTPException(413, "payload too large")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, "payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def register_webhook_routes(app: FastAPI, *, gateway=None, **_kw) -> None:
    # --- WhatsApp ---
    @app.get("/api/webhooks/whatsapp")
    async def whatsapp_verify(
        hub_mode: str = Query(default="", alias="hub.mode"),
        hub_verify_token: str = Query(default="", alias="hub.verify_token"),
        hub_challenge: str = Query(default="", alias="hub.challenge"),
    ):
        ch = _ch(gateway, "WHATSAPP")
        if ch is None:
            raise HTTPException(503, "WhatsApp channel not active")
        challenge = ch.verify_webhook_challenge(hub_mode, hub_verify_token, hub_challenge)
        if challenge is None:
            raise HTTPException(403, "verify failed")
        return PlainTextResponse(challenge)

    @app.post("/api/webhooks/whatsapp")
    async def whatsapp_events(request: Request):
        ch = _ch(gateway, "WHATSAPP")
        if ch is None:
            raise HTTPException(503, "WhatsApp channel not active")
        body = await _read_body_capped(request)
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not ch.verify_signature(body, sig):
            raise HTTPException(403, "bad signature")
        try:
            import json as _json

            data = _json.loads(body.decode("utf-8") or "{}")
        except Exception as exc:
            raise HTTPException(400, "invalid json") from exc
        n = await ch.handle_webhook_payload(data if isinstance(data, dict) else {})
        return {"ok": True, "handled": n}

    # --- Teams (Bot Framework) ---
    @app.post("/api/webhooks/teams")
    async def teams_activity(request: Request):
        ch = _ch(gateway, "TEAMS")
        if ch is None:
            raise HTTPException(503, "Teams channel not active")
        auth = request.headers.get("Authorization")
        if hasattr(ch, "verify_inbound_auth") and not ch.verify_inbound_auth(auth):
            raise HTTPException(401, "teams auth failed")
        try:
            import json as _json

            activity = _json.loads((await _read_body_capped(request)).decode("utf-8") or "{}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "invalid json") from exc
        if not isinstance(activity, dict):
            raise HTTPException(400, "invalid body")
        ok = await ch.handle_activity(activity)
        return JSONResponse({"ok": True, "handled": bool(ok)})

    # --- Google Chat ---
    @app.post("/api/webhooks/google_chat")
    async def google_chat_event(request: Request):
        ch = _ch(gateway, "GOOGLE_CHAT")
        if ch is None:
            raise HTTPException(503, "Google Chat channel not active")
        try:
            import json as _json

            data = _json.loads((await _read_body_capped(request)).decode("utf-8") or "{}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "invalid json") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid body")
        # Chat verification handshake — only for explicit platform verification
        # shapes. Do NOT skip auth merely because a MESSAGE body includes a
        # truthy ``challenge`` key (unauthenticated reachability / probe surface).
        etype = str(data.get("type") or data.get("eventType") or "").strip().upper()
        is_url_verification = etype in ("URL_VERIFICATION", "URL_VERIFICATION_EVENT")
        is_challenge_only = (
            not data.get("message")
            and data.get("challenge") is not None
            and etype in ("", "CHALLENGE", "URL_VERIFICATION", "URL_VERIFICATION_EVENT")
        )
        if is_url_verification or is_challenge_only:
            return {"challenge": data.get("challenge") or data.get("token") or "ok"}
        auth = request.headers.get("Authorization")
        if hasattr(ch, "verify_inbound_auth") and not ch.verify_inbound_auth(auth):
            raise HTTPException(401, "google_chat auth failed")
        ok = await ch.handle_event(data)
        return {"ok": True, "handled": bool(ok)}


def _ch(gateway, attr: str):
    if gateway is None:
        return None
    try:
        from remedy.models import ChannelKind

        kind = getattr(ChannelKind, attr)
        return gateway.get_channel(kind)
    except Exception:
        return None
