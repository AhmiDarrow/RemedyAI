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
- ``/api/chat`` and ``/api/chat/stream`` (legacy)
- ``/api/sessions/{id}/messages/stream`` and ``/api/sessions/{id}/steer``
  (Go CognitionTurnRunner / httpapi)
- ``/api/terminal*`` ConPTY SSE terminal (phone / web rails)
- ``/api/rmb/*`` local model host (start/stop/settings/use/HF)
- ``/api/voice/*`` speak / hear / install / settings
- ``/api/workspace``, ``/api/files*``, ``/api/media``, ``/api/scratch``
- ``/api/skills/library/*`` catalog / search / suggest / install / updates
- ``/api/memory/*`` search / facts / persona-wipe
- ``/api/skills*`` list / detail / status / packs / library / delete
- ``/api/partner/*``, ``/api/approvals*``, ``/api/plans*``
- ``/api/models``, ``/api/commands``, ``/api/agents``, session ``/command``
- ``/api/providers*`` catalog / connected / free / custom / probe / ollama
- ``/api/settings`` GET/PUT
- ``/api/diagnostics``, ``/api/coordination/presence``, ``/api/self-inject/rounds``
- ``/api/app/command``, ``/api/projects/scan``
  (ping/status/turn-active keep TestClient stubs only; misc keeps /dashboard)
"""
from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.misc import register_misc_routes
from remedy.interfaces.routes.sessions import register_sessions_routes
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
    # No register_auth/settings/catalog/memory/partner/i18n/usage/vision/
    # telephony/webhook/connect/nanoswarm/assistant/computer/hive/
    # session_events/chat/terminal/rmb/voice/workspace/skills_library or
    # sessions stream/steer — Go owns those (status keeps TestClient stubs;
    # misc keeps /dashboard only).
    register_misc_routes(app, **kw)
