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
"""
from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.auth import register_auth_routes
from remedy.interfaces.routes.catalog import register_catalog_routes
from remedy.interfaces.routes.chat import register_chat_routes
from remedy.interfaces.routes.computer import register_computer_routes
from remedy.interfaces.routes.hive import register_hive_routes
from remedy.interfaces.routes.memory import register_memory_routes
from remedy.interfaces.routes.misc import register_misc_routes
from remedy.interfaces.routes.partner import register_partner_routes
from remedy.interfaces.routes.rmb import register_rmb_routes
from remedy.interfaces.routes.sessions import register_sessions_routes
from remedy.interfaces.routes.settings import register_settings_routes
from remedy.interfaces.routes.skills_library import register_skills_library_routes
from remedy.interfaces.routes.status import register_status_routes
from remedy.interfaces.routes.terminal import register_terminal_routes
from remedy.interfaces.routes.voice import register_voice_routes
from remedy.interfaces.routes.workspace import register_workspace_routes


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
    register_chat_routes(app, **kw)
    register_sessions_routes(app, **kw)
    register_catalog_routes(app, **kw)
    register_memory_routes(app, **kw)
    # Library routes must register before any conflicting catch-alls; namespaced under /library
    register_skills_library_routes(app, **kw)
    register_workspace_routes(app, **kw)
    register_settings_routes(app, **kw)
    register_auth_routes(app, **kw)
    register_partner_routes(app, **kw)
    register_computer_routes(app, **kw)
    # No register_i18n/usage/vision/telephony/webhook/connect/nanoswarm/assistant —
    # Go remedy-runtime owns those.
    register_misc_routes(app, **kw)
    register_voice_routes(app, **kw)
    register_rmb_routes(app, **kw)
    register_hive_routes(app, **kw)
    register_terminal_routes(app)
