"""Route modules for the pytest / TestClient FastAPI surface.

Production ``:7400`` is owned by Go ``remedy-runtime``. These ``register_*``
helpers exist so ``create_app`` can exercise the Python route tree in-process.

Not registered here (Go owns production):
- ``/api/connect*`` Connect management
- ``/api/webhooks/*`` and ``/api/webhook/{source}`` messenger / CI inbound
- ``/api/i18n`` language catalogs
- ``/api/usage/*``, ``/api/continuity/dashboard``, ``/api/nanoswarm/*``
- ``/api/vision/*`` local visual decoder REST
- ``/api/telephony/*`` phone line status / terms / choose
- ``/api/assistant/*`` (google OAuth + status)
- ``/api/computer/*`` host bridge / jobs / capture / a11y
- ``/api/hive/*`` hive roster / spawn / assign / retire
- ``/api/events/sessions`` session SSE
- ``/api/chat`` and ``/api/chat/stream`` (legacy; sessions stream owns chat)
- ``/api/terminal*`` ConPTY SSE terminal (phone / web rails)
- ``/api/rmb/*`` local model host (start/stop/settings/use/HF)
- ``/api/voice/*`` speak / hear / install / settings
- ``/api/workspace``, ``/api/files*``, ``/api/media``, ``/api/scratch``
- ``/api/skills/library/*`` catalog / search / suggest / install / updates
  (and leftover Python-only ``update/{id}`` / ``submit``)
"""
from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.auth import register_auth_routes
from remedy.interfaces.routes.catalog import register_catalog_routes
from remedy.interfaces.routes.memory import register_memory_routes
from remedy.interfaces.routes.misc import register_misc_routes
from remedy.interfaces.routes.partner import register_partner_routes
from remedy.interfaces.routes.sessions import register_sessions_routes
from remedy.interfaces.routes.settings import register_settings_routes
from remedy.interfaces.routes.status import register_status_routes


def register_all_routes(
    app: FastAPI,
    *,
    runtime=None,
    gateway=None,
    memory=None,
) -> None:
    """Attach TestClient HTTP routes to *app* (not production :7400)."""
    kw = {"runtime": runtime, "gateway": gateway, "memory": memory}
    register_status_routes(app, **kw)
    register_sessions_routes(app, **kw)
    register_catalog_routes(app, **kw)
    register_memory_routes(app, **kw)
    register_settings_routes(app, **kw)
    register_auth_routes(app, **kw)
    register_partner_routes(app, **kw)
    # No register_i18n/usage/vision/telephony/webhook/connect/nanoswarm/assistant/
    # computer/hive/session_events/chat/terminal/rmb/voice/workspace/skills_library
    # — Go owns those.
    register_misc_routes(app, **kw)
