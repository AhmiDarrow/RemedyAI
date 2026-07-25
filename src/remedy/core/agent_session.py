"""Session workspace binding for a stream turn."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from remedy.core.workspace import is_unset_project_path


async def apply_session_workspace(runtime: Any, session_id: str | None) -> None:
    """Bind tools/cwd to the **session** project for this turn.

    Tree contract: a session under a project folder uses that path as the tool
    jail; a **No project** session gets unset → full access. Global Settings
    default is only used when there is no session row.
    """
    if session_id:
        runtime._session_id = session_id
    session_path: str | None = None
    has_session_row = False
    if session_id and runtime.memory is not None:
        with suppress(Exception):
            sess = await runtime.memory.get_chat_session(session_id)
            if sess is not None:
                has_session_row = True
                session_path = getattr(sess, "project_path", None)
    if has_session_row:
        # Session owns workspace for this turn (do not keep prior session raw).
        if session_path and not is_unset_project_path(session_path):
            runtime.set_project_path(session_path, as_default=False)
        else:
            # Explicit no-project → full access for this turn
            runtime.set_project_path(None, as_default=False)
        return
    # No session row: fall back to configured default project
    cfg_path = None
    if getattr(runtime, "config", None) is not None:
        cfg_path = getattr(runtime.config, "project_path", None)
    if is_unset_project_path(cfg_path):
        runtime.set_project_path(None, as_default=False)
    else:
        runtime.set_project_path(str(cfg_path), as_default=False)
