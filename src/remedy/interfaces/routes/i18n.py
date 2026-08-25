"""Language catalogs for the desktop / WebUI chrome."""

from __future__ import annotations

from fastapi import FastAPI, Header, Query


def register_i18n_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = runtime, gateway, memory

    @app.get("/api/i18n")
    async def get_i18n(
        lang: str | None = Query(default=None),
        hint: str | None = Query(default=None),
        accept_language: str | None = Header(default=None, alias="Accept-Language"),
    ):
        from remedy.i18n.catalog import catalog_payload
        from remedy.interfaces.api_support import load_config

        cfg = load_config()
        stored = lang if lang is not None else cfg.get("ui_language")
        os_hint = (hint or "").strip()
        if not os_hint and accept_language:
            os_hint = str(accept_language).split(",", 1)[0].split(";", 1)[0].strip()
        return catalog_payload(stored, hint=os_hint or None)
