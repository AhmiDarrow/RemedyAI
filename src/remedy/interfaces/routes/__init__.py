"""Route modules for the Remedy FastAPI app."""
from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.auth import register_auth_routes
from remedy.interfaces.routes.catalog import register_catalog_routes
from remedy.interfaces.routes.chat import register_chat_routes
from remedy.interfaces.routes.memory import register_memory_routes
from remedy.interfaces.routes.misc import register_misc_routes
from remedy.interfaces.routes.partner import register_partner_routes
from remedy.interfaces.routes.sessions import register_sessions_routes
from remedy.interfaces.routes.settings import register_settings_routes
from remedy.interfaces.routes.status import register_status_routes
from remedy.interfaces.routes.workspace import register_workspace_routes


def register_all_routes(
    app: FastAPI,
    *,
    runtime=None,
    gateway=None,
    memory=None,
) -> None:
    """Attach all HTTP routes to *app*."""
    kw = dict(runtime=runtime, gateway=gateway, memory=memory)
    register_status_routes(app, **kw)
    register_chat_routes(app, **kw)
    register_sessions_routes(app, **kw)
    register_catalog_routes(app, **kw)
    register_memory_routes(app, **kw)
    register_workspace_routes(app, **kw)
    register_settings_routes(app, **kw)
    register_auth_routes(app, **kw)
    register_partner_routes(app, **kw)
    register_misc_routes(app, **kw)
