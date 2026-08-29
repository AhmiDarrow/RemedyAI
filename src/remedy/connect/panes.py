"""Phone pane flags for Grove Connect.

Approvals (including Stop / abort) stay on even if a stored payload tries to
turn them off. Preview and settings writes stay off until the owner opts in.
"""

from __future__ import annotations

from typing import Any

PANE_KEYS: tuple[str, ...] = (
    "live_ui",
    "chat",
    "approvals",
    "sessions",
    "rails",
    "computer_preview",
    "settings_write",
)

ALWAYS_ON: frozenset[str] = frozenset({"approvals"})

DEFAULT_PANES: dict[str, bool] = {
    "live_ui": True,
    "chat": True,
    "approvals": True,
    "sessions": True,
    "rails": True,
    "computer_preview": False,
    "settings_write": False,
}


def default_panes() -> dict[str, bool]:
    """Return a fresh copy of the shipping pane defaults."""
    return dict(DEFAULT_PANES)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disable", "disabled", ""):
        return False
    return bool(value)


def normalize_panes(raw: object | None) -> dict[str, bool]:
    """Merge *raw* onto defaults and force always-on flags."""
    out = default_panes()
    if isinstance(raw, dict):
        for key in PANE_KEYS:
            if key in raw and raw[key] is not None:
                out[key] = _as_bool(raw[key])
    for key in ALWAYS_ON:
        out[key] = True
    return out


def panes_from_config(config: dict[str, Any] | None) -> dict[str, bool]:
    """Read ``connect_panes`` from a config mapping (missing → defaults)."""
    if not isinstance(config, dict):
        return default_panes()
    return normalize_panes(config.get("connect_panes"))
