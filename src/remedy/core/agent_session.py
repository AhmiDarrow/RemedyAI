"""Session workspace binding for a stream turn."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import SecurityError
from remedy.core.workspace import is_forbidden_project_path, is_unset_project_path
from remedy.home import default_home


def refuse_jail_path(runtime: Any) -> Path:
    """Restricted project jail — leftover OS trees must not become full access."""
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    root = Path(home).expanduser() if home else default_home()
    refuse = root / "refused-project"
    with suppress(Exception):
        refuse.mkdir(parents=True, exist_ok=True)
    return refuse


def _bind_refuse_jail(runtime: Any) -> Path:
    refuse = refuse_jail_path(runtime)
    runtime.set_project_path(str(refuse), as_default=False)
    return refuse


async def _refuse_forbidden_project(
    runtime: Any,
    raw: str,
    *,
    session_id: str | None = None,
) -> None:
    refuse = _bind_refuse_jail(runtime)
    if session_id and getattr(runtime, "memory", None) is not None:
        with suppress(Exception):
            await runtime.memory.update_chat_session(
                session_id, project_path=str(refuse)
            )
    raise SecurityError(
        f"Project path is not allowed: {raw}. "
        "Pick a user folder, not an OS or program directory.",
        rule="forbidden_project",
        path=str(raw),
    )


async def apply_session_workspace(runtime: Any, session_id: str | None) -> None:
    """Bind tools/cwd + continuity state to the **session** for this turn.

    Tree contract: a session under a project folder uses that path as the tool
    jail; a **No project** session gets unset → full access. Global Settings
    default is only used when there is no session row.

    Continuity: rebind Session Brief / Partner State / work roots so another
    tab's project never injects stale facts into this turn.
    """
    # Continuity rebind first (uses previous runtime._session_id for stash)
    with suppress(Exception):
        from remedy.core.session_continuity import bind_session_continuity

        bind_session_continuity(runtime, session_id)

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
            if is_forbidden_project_path(session_path):
                await _refuse_forbidden_project(
                    runtime, str(session_path), session_id=session_id
                )
            runtime.set_project_path(session_path, as_default=False)
        else:
            # Explicit no-project → full access for this turn
            runtime.set_project_path(None, as_default=False)
        return
    # No session row: fall back to configured default project
    cfg_path = None
    if getattr(runtime, "config", None) is not None:
        cfg_path = getattr(runtime.config, "project_path", None)
    if is_forbidden_project_path(cfg_path):
        await _refuse_forbidden_project(runtime, str(cfg_path), session_id=None)
    if is_unset_project_path(cfg_path):
        runtime.set_project_path(None, as_default=False)
    else:
        runtime.set_project_path(str(cfg_path), as_default=False)
