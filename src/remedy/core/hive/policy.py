"""Hive depth, caps, and mother-only tool denylist."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from remedy.policy.capabilities import Capability

# Depth: only the mother hires. Daughters cannot spawn.
_hive_depth: ContextVar[int] = ContextVar("remedy_hive_depth", default=0)
# Granted caps for this pulse, loaded from the daughter's journal.
_hive_granted: ContextVar[frozenset[Capability] | None] = ContextVar(
    "remedy_hive_granted", default=None
)

# What a daughter may hold. Mail / click / credential.use stay with the mother.
DAUGHTER_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.FS_READ,
        Capability.FS_WRITE,
        Capability.PROCESS_EXEC,
        Capability.NETWORK_READ,
        Capability.BROWSER_READ,
        Capability.COMPUTER_READ,
    }
)

DEFAULT_LIVE_PULSES = 2
MAX_LIVE_PULSES = 4
DEFAULT_POSTS = 2
MAX_POSTS = 4
MIN_PULSE_S = 30
DEFAULT_BUDGET_STEPS = 8
MAX_BUDGET_STEPS = 16

# Tools a daughter must never see — mother/owner surfaces.
MOTHER_ONLY_TOOLS = frozenset(
    {
        "hive_spawn",
        "hive_assign",
        "hive_collect",
        "hive_status",
        "hive_retire",
        "spread_run",
        "update_settings",
        "mail_send",
        "mail_reply",
        "computer_click",
        "computer_type",
        "computer_fill",
        "computer_act",
        "computer_key",
        "computer_press_hold",
        "computer_hotkey",
        "computer_drag",
        "computer_select",
        "computer_app",
        "computer_windows",
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_act",
        "session_export",
        "session_import",
    }
)

MOTHER_ONLY_PREFIXES = ("hive_", "mail_", "calendar_")


def hive_depth() -> int:
    return int(_hive_depth.get() or 0)


def set_hive_depth(depth: int):
    return _hive_depth.set(max(0, int(depth)))


def reset_hive_depth(token: Any) -> None:
    if token is not None:
        _hive_depth.reset(token)


def hive_granted_capabilities() -> frozenset[Capability] | None:
    return _hive_granted.get()


def set_hive_granted(caps: frozenset[Capability] | None):
    return _hive_granted.set(caps)


def reset_hive_granted(token: Any) -> None:
    if token is not None:
        _hive_granted.reset(token)


def parse_granted_caps(raw: object) -> frozenset[Capability]:
    """Journal list → Capability set; unknown strings dropped."""
    out: set[Capability] = set()
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    for item in raw:
        try:
            out.add(Capability(str(item)))
        except ValueError:
            continue
    return frozenset(out)


def is_mother_only_tool(name: str | None) -> bool:
    n = str(name or "").strip()
    if not n:
        return False
    if n in MOTHER_ONLY_TOOLS:
        return True
    return n.startswith(MOTHER_ONLY_PREFIXES)


def _tool_name(tool: dict[str, Any]) -> str:
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(tool.get("name") or "")


def filter_daughter_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strip mother-only tools from a daughter's OpenAI tool list."""
    out: list[dict[str, Any]] = []
    for t in tools or []:
        if is_mother_only_tool(_tool_name(t)):
            continue
        out.append(t)
    return out
