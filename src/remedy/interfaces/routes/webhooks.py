"""Inbound webhooks for messengers that need a public URL (WhatsApp, …)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def register_webhook_routes(app: FastAPI, *, gateway=None, **_kw) -> None:
    @app.get("/api/webhooks/whatsapp")
    async def whatsapp_verify(
        hub_mode: str = Query(default="", alias="hub.mode"),
        hub_verify_token: str = Query(default="", alias="hub.verify_token"),
        hub_challenge: str = Query(default="", alias="hub.challenge"),
    ):
        ch = _whatsapp(gateway)
        if ch is None:
            raise HTTPException(503, "WhatsApp channel not active")
        challenge = ch.verify_webhook_challenge(hub_mode, hub_verify_token, hub_challenge)
        if challenge is None:
            raise HTTPException(403, "verify failed")
        return PlainTextResponse(challenge)

    @app.post("/api/webhooks/whatsapp")
    async def whatsapp_events(request: Request):
        ch = _whatsapp(gateway)
        if ch is None:
            raise HTTPException(503, "WhatsApp channel not active")
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not ch.verify_signature(body, sig):
            raise HTTPException(403, "bad signature")
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(400, "invalid json") from exc
        n = await ch.handle_webhook_payload(data if isinstance(data, dict) else {})
        return {"ok": True, "handled": n}


def _whatsapp(gateway):
    if gateway is None:
        return None
    try:
        from remedy.models import ChannelKind

        return gateway.get_channel(ChannelKind.WHATSAPP)
    except Exception:
        return None
