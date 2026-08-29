"""Route modules for the Remedy FastAPI app."""
from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.assistant import register_assistant_routes
from remedy.interfaces.routes.auth import register_auth_routes
from remedy.interfaces.routes.catalog import register_catalog_routes
from remedy.interfaces.routes.chat import register_chat_routes
from remedy.interfaces.routes.computer import register_computer_routes
from remedy.interfaces.routes.connect import register_connect_routes
from remedy.interfaces.routes.hive import register_hive_routes
from remedy.interfaces.routes.i18n import register_i18n_routes
from remedy.interfaces.routes.memory import register_memory_routes
from remedy.interfaces.routes.misc import register_misc_routes
from remedy.interfaces.routes.nanoswarm import register_nanoswarm_routes
from remedy.interfaces.routes.partner import register_partner_routes
from remedy.interfaces.routes.rmb import register_rmb_routes
from remedy.interfaces.routes.sessions import register_sessions_routes
from remedy.interfaces.routes.settings import register_settings_routes
from remedy.interfaces.routes.skills_library import register_skills_library_routes
from remedy.interfaces.routes.status import register_status_routes
from remedy.interfaces.routes.telephony import register_telephony_routes
from remedy.interfaces.routes.usage import register_usage_routes
from remedy.interfaces.routes.vision import register_vision_routes
from remedy.interfaces.routes.voice import register_voice_routes
from remedy.interfaces.routes.webhooks import register_webhook_routes
from remedy.interfaces.routes.workspace import register_workspace_routes


def register_all_routes(
    app: FastAPI,
    *,
    runtime=None,
    gateway=None,
    memory=None,
) -> None:
    """Attach all HTTP routes to *app*."""
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
    register_i18n_routes(app, **kw)
    register_auth_routes(app, **kw)
    register_assistant_routes(app, **kw)
    register_partner_routes(app, **kw)
    register_computer_routes(app, **kw)
    register_connect_routes(app, **kw)
    register_misc_routes(app, **kw)
    register_vision_routes(app, **kw)
    register_voice_routes(app, **kw)
    register_telephony_routes(app, **kw)
    register_rmb_routes(app, **kw)
    register_nanoswarm_routes(app, **kw)
    register_hive_routes(app, **kw)
    register_usage_routes(app, **kw)
    register_webhook_routes(app, **kw)
