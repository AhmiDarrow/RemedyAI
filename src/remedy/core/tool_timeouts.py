"""Per-tool wall-clock budgets for the dispatcher's ``asyncio.wait_for``.

Resolution order for a tool name:

1. ``handler._remedy_timeout`` — set on the coroutine function by its
   registrar. ``None``/``0`` means "no outer wrap" (the tool enforces its own
   limit); a positive float is seconds.
2. :data:`TOOL_TIMEOUTS` — built-in overrides for tools that already carry a
   longer internal timeout (shell, builds, jobs, browser actions). These are
   set *above* the tool's own maximum so the outer guard only catches a
   genuinely stuck handler and never clips a legitimate long run.
3. :data:`DEFAULT_TOOL_TIMEOUT_S` (120 s).
"""

from __future__ import annotations

from typing import Any

DEFAULT_TOOL_TIMEOUT_S = 120.0

#: Tools whose own timeouts exceed the default. ``None`` disables the outer
#: guard entirely (the tool streams or supervises its own process tree).
TOOL_TIMEOUTS: dict[str, float | None] = {
    # shell: timeout_seconds clamps to 5–600 inside the tool.
    "host_run": 660.0,
    "bash_exec": 660.0,
    "run_python_file": 660.0,
    # long supervised work: own budgets / sub-process trees.
    # build_drive owns its own budgets / sub-process trees, and now runs via
    # asyncio.to_thread so it never blocks the event loop — no outer guard.
    "build_drive": None,
    "build_parallel": None,
    "build_review_fix": None,
    "apply_patch": 300.0,
    "spread_run": None,
    "job_run": None,
    "skill_run": 900.0,
    "mission_start": None,
    "mission_verify": 900.0,
    "soul_dream": 600.0,
    "comfyui": 900.0,
    "vision_decode": 600.0,
    "web_fetch": 180.0,
    "web_search": 180.0,
    "local_discover": 300.0,
    "companion_design": 600.0,
    "game_playtest": 300.0,
    # Research / analysis: handlers also set _remedy_timeout (which may be
    # tighter). TOOL_TIMEOUTS is a name-only floor and must stay at or above
    # DEFAULT_TOOL_TIMEOUT_S so a lookup without the handler never clips a
    # legitimate run.
    "analysis_env": 240.0,
    "analysis_run": 1800.0,
    "data_profile": 600.0,
    "data_diff": 300.0,
    "lit_search": 180.0,
    "lit_fetch": 300.0,
    "cite_check": 300.0,
    "manuscript_build": 1800.0,
}

#: Name prefixes that get one shared budget.
PREFIX_TIMEOUTS: tuple[tuple[str, float | None], ...] = (
    ("computer_", 300.0),
    ("mcp_", 300.0),
    # godot_run clamps its own timeout to 600 s; check/export/import run longer batches.
    ("godot_", 900.0),
    ("lit_", 300.0),
    ("cite_", 300.0),
    ("stats_", 120.0),
    ("manuscript_", 1800.0),
)


def tool_timeout_for(name: str, registry: Any = None) -> float | None:
    """Seconds the dispatcher may wait for *name*, or ``None`` for no limit."""
    handler = None
    if registry is not None:
        try:
            handler = registry._handlers.get(name)
        except Exception:
            handler = None
    if handler is not None and hasattr(handler, "_remedy_timeout"):
        raw = handler._remedy_timeout
        if raw in (None, 0, False):
            return None
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            pass
    if name in TOOL_TIMEOUTS:
        return TOOL_TIMEOUTS[name]
    for prefix, secs in PREFIX_TIMEOUTS:
        if str(name).startswith(prefix):
            return secs
    return DEFAULT_TOOL_TIMEOUT_S
