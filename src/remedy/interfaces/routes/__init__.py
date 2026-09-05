"""Route modules for the pytest / TestClient FastAPI surface.

Production ``:7400`` is owned by Go ``remedy-runtime``. ``register_all_routes``
is intentionally empty — every former FastAPI twin has been deleted.

Not registered here (Go owns production, or dropped as TestClient-only):
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
- ``/api/sessions*`` CRUD / messages / llm / attachments / abort / explain
  (Go httpapi sessions + CognitionTurnRunner; no FastAPI twin)
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
- ``/api/ping``, ``/api/status``, ``/api/turn-active``, SPA ``/`` (webui)
- ``/api/notifications*``, ``/api/metrics``, ``/api/self-improve`` (no desktop
  callers; Python notify/metrics/self_inject modules remain for workers)
"""
from __future__ import annotations

from fastapi import FastAPI


def register_all_routes(
    app: FastAPI,
    *,
    runtime=None,
    gateway=None,
    memory=None,
) -> None:
    """No-op: Go owns production HTTP; TestClient has no FastAPI route twins."""
    _ = (app, runtime, gateway, memory)
