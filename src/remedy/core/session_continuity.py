"""Hard session isolation for continuity state on the shared BasicRuntime.

Desktop uses **one** agent process for many chat tabs. Live slots like
``_session_brief``, ``_partner_state``, and ``_work_roots`` must be rebound
whenever ``session_id`` changes — otherwise SecretFolder work bleeds into a
RemedyAI tab (stale artifacts, dual-stream, wrong habits).

Call ``bind_session_continuity`` at the start of every turn (via
``apply_session_workspace``).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

# Per-process caches so switching tabs restores the right brief/work roots
# without wiping the previous tab's in-memory continuity permanently.
_brief_by_session: dict[str, Any] = {}
_partner_by_session: dict[str, Any] = {}
_work_roots_by_session: dict[str, list[str]] = {}
_MAX_CACHED_SESSIONS = 48


def _trim_cache(cache: dict[str, Any]) -> None:
    if len(cache) <= _MAX_CACHED_SESSIONS:
        return
    # Drop arbitrary oldest-inserted keys (Py3.7+ insertion order)
    for k in list(cache.keys())[: len(cache) - _MAX_CACHED_SESSIONS]:
        cache.pop(k, None)


def _live_session_id(runtime: Any) -> str:
    """Read the process-live session id (not turn ContextVar)."""
    d = getattr(runtime, "__dict__", None) or {}
    if "_session_id_live" in d:
        return str(d.get("_session_id_live") or "").strip()
    return str(getattr(runtime, "_session_id", "") or "").strip()


def bind_session_continuity(runtime: Any, session_id: str | None) -> dict[str, Any]:
    """Rebind live continuity slots to *session_id*. Returns meta for logs/tests.

    Live mirrors are still updated (legacy tools / post-turn code). Concurrent
    streams freeze the rebound objects into turn ContextVars via ``begin_turn``.
    """
    sid = str(session_id or "").strip()
    prev = _live_session_id(runtime)
    meta: dict[str, Any] = {
        "session_id": sid,
        "previous_session_id": prev or None,
        "switched": bool(sid and prev and sid != prev),
        "cleared_orphan": False,
        "brief_bound": False,
        "partner_bound": False,
        "work_roots_bound": False,
    }

    # Stash outgoing tab state before switch (prefer live stores).
    def _brief_live() -> Any:
        d = getattr(runtime, "__dict__", None) or {}
        if "_session_brief_live" in d:
            return d.get("_session_brief_live")
        return getattr(runtime, "_session_brief", None)

    def _partner_live() -> Any:
        d = getattr(runtime, "__dict__", None) or {}
        if "_partner_state_live" in d:
            return d.get("_partner_state_live")
        return getattr(runtime, "_partner_state", None)

    def _roots_live() -> list[str]:
        d = getattr(runtime, "__dict__", None) or {}
        if "_work_roots_live" in d:
            return list(d.get("_work_roots_live") or [])
        return list(getattr(runtime, "_work_roots", None) or [])

    if prev and prev != sid:
        with suppress(Exception):
            brief = _brief_live()
            if brief is not None:
                bsid = str(getattr(brief, "session_id", "") or prev)
                if bsid:
                    _brief_by_session[bsid] = brief
        with suppress(Exception):
            partner = _partner_live()
            if partner is not None:
                psid = str(getattr(partner, "session_id", "") or prev)
                if psid:
                    _partner_by_session[psid] = partner
        with suppress(Exception):
            roots = _roots_live()
            if roots:
                _work_roots_by_session[prev] = roots
        with suppress(Exception):
            # Detach partner pointer so ensure_partner_state cannot return old
            runtime._partner_state = None
        meta["cleared_orphan"] = True
        _trim_cache(_brief_by_session)
        _trim_cache(_partner_by_session)
        _trim_cache(_work_roots_by_session)

    if sid:
        runtime._session_id = sid

    # --- Session Brief ---
    brief = _brief_live()
    brief_sid = str(getattr(brief, "session_id", "") or "") if brief is not None else ""
    if not sid:
        # Anonymous turn: keep brief only if it has no foreign session stamp
        if brief is not None and brief_sid and brief_sid not in ("", "default"):
            runtime._session_brief = None
            meta["brief_bound"] = True
    else:
        need_new = (
            brief is None
            or (brief_sid and brief_sid != sid)
            or (not brief_sid and prev and prev != sid)
        )
        if need_new:
            cached = _brief_by_session.get(sid)
            if cached is not None and str(getattr(cached, "session_id", "") or sid) in (
                sid,
                "",
            ):
                if not getattr(cached, "session_id", None):
                    with suppress(Exception):
                        cached.session_id = sid
                runtime._session_brief = cached
            else:
                from remedy.memory.harness.brief import SessionBrief

                runtime._session_brief = SessionBrief(session_id=sid)
            meta["brief_bound"] = True
        else:
            # Ensure stamp
            with suppress(Exception):
                if brief is not None and not brief_sid:
                    brief.session_id = sid

    # --- Partner State (always re-resolve by session id) ---
    with suppress(Exception):
        from remedy.memory.partner_state.state import ensure_partner_state

        # Force rebind path
        existing = _partner_live()
        if existing is not None:
            esid = str(getattr(existing, "session_id", "") or "")
            if sid and esid and esid != sid:
                runtime._partner_state = None
            elif sid and esid == sid:
                # Reuse stashed object if registry would recreate
                runtime._partner_state = existing
        cached_p = _partner_by_session.get(sid) if sid else None
        if cached_p is not None and getattr(runtime, "_partner_state", None) is None:
            runtime._partner_state = cached_p
        st = ensure_partner_state(runtime)
        # Double-check key
        if sid and str(getattr(st, "session_id", "") or "") not in (sid, f"anon-{id(runtime)}"):
            if str(st.session_id) != sid:
                runtime._partner_state = None
                st = ensure_partner_state(runtime)
        if sid and st is not None:
            _partner_by_session[sid] = st
        meta["partner_bound"] = True
        meta["partner_session_id"] = getattr(st, "session_id", None)

    # --- Work roots (session-scoped) ---
    with suppress(Exception):
        if sid:
            roots = list(_work_roots_by_session.get(sid) or [])
            runtime._work_roots = roots
        elif meta["switched"] or meta["cleared_orphan"]:
            runtime._work_roots = []
        meta["work_roots_bound"] = True
        meta["work_roots"] = list(getattr(runtime, "_work_roots", None) or [])

    # --- Prospective session-start flag is per live bind ---
    if meta["switched"] or meta["cleared_orphan"]:
        with suppress(Exception):
            runtime._prospective_session_fired = False
        # Turn scratch is process-global on the shared runtime — never carry
        # tab A's tool trail / stream accum into tab B's first turn.
        with suppress(Exception):
            for attr in (
                "_turn_tool_steps",
                "_last_tool_steps",
                "_pending_tool_results",
                "_stream_accum",
            ):
                if not hasattr(runtime, attr):
                    continue
                val = getattr(runtime, attr)
                if isinstance(val, list):
                    val.clear()
                elif isinstance(val, dict):
                    val.clear()
                elif isinstance(val, set):
                    val.clear()
                else:
                    setattr(runtime, attr, None if val is not None else val)
            # One-shot mid-turn flags must not bleed across tabs either
            for attr, default in (
                ("_mission_gate_nudge_done", False),
                ("_evidence_inject_eu", -1),
            ):
                if hasattr(runtime, attr):
                    setattr(runtime, attr, default)
            meta["turn_scratch_cleared"] = True

    if meta["switched"]:
        logger.info(
            "Session continuity rebound %s → %s (brief=%s partner=%s scratch=%s)",
            prev,
            sid,
            meta.get("brief_bound"),
            meta.get("partner_bound"),
            meta.get("turn_scratch_cleared"),
        )
    return meta


def drop_session_continuity_cache(session_id: str | None) -> None:
    """Drop cached brief/work-roots for a session (reset / delete)."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    _brief_by_session.pop(sid, None)
    _partner_by_session.pop(sid, None)
    _work_roots_by_session.pop(sid, None)


def clear_all_continuity_caches() -> None:
    """Test/helper: wipe in-process continuity caches."""
    _brief_by_session.clear()
    _partner_by_session.clear()
    _work_roots_by_session.clear()


def session_isolation_system_line(runtime: Any) -> str:
    """Hard system reminder: only this session's project/context is in play."""
    sid = ""
    with suppress(Exception):
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or "").strip()
    if not sid:
        sid = _live_session_id(runtime)
    proj = ""
    with suppress(Exception):
        proj = str(runtime.effective_project_path() or "")
    if not sid and not proj:
        return ""
    parts = [
        "[Session isolation — hard rule]",
        "Use ONLY this chat session's history, Session Brief, Partner State, and project.",
        "Do NOT continue work, paths, or assumptions from other chat tabs/sessions.",
    ]
    if sid:
        parts.append(f"session_id={sid}")
    if proj:
        parts.append(f"project={proj}")
    parts.append(
        "If a path is outside the project jail, fix scope or switch project — "
        "do not invent success from another repo."
    )
    return "\n".join(parts)
