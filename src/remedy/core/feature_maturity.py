"""Feature maturity gates — experimental organs stay opt-in and honest.

Stable product paths stay on. Experimental subsystems (Soul Field, advanced
Build OS frontiers, RMB-as-default muscle) require an explicit config flag so
owners know what they enabled and support surface stays clear.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

Maturity = Literal["stable", "advanced", "experimental"]

# Defaults: stable on, experimental off, advanced opt-in for heavy build tools.
_DEFAULTS: dict[str, bool] = {
    # Soul Field (personhood / dream / residual) — on by default so the
    # organism actually lives; owners can still set soul_field_enabled=false.
    "soul_field_enabled": True,
    # Build engine core (scout→verify) stays on when build intent is detected;
    # advanced A–H frontiers (mutants, gate tower, symbol patch, TDD-as-OS) opt-in.
    "build_os_advanced": False,
    # RMB local agent host in provider catalog / exclusive GPU path
    "rmb_enabled": True,
}


def _load_cfg() -> dict[str, Any]:
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        cfg = load_config()
        if isinstance(cfg, dict):
            return cfg
    return {}


def feature_enabled(name: str, *, cfg: dict[str, Any] | None = None) -> bool:
    """Return whether a maturity-gated feature is enabled."""
    key = str(name or "").strip()
    if not key:
        return False
    default = bool(_DEFAULTS.get(key, False))
    raw = cfg if isinstance(cfg, dict) else _load_cfg()
    if key not in raw:
        return default
    val = raw.get(key)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return bool(val)
    s = str(val if val is not None else "").strip().lower()
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disable", "disabled", ""):
        return False if s != "" or key in raw else default
    if s == "" and key in raw:
        return default
    return default


def soul_field_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return feature_enabled("soul_field_enabled", cfg=cfg)


def build_os_advanced_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return feature_enabled("build_os_advanced", cfg=cfg)


def rmb_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return feature_enabled("rmb_enabled", cfg=cfg)


def maturity_snapshot(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public settings / About surface for maturity flags."""
    raw = cfg if isinstance(cfg, dict) else _load_cfg()
    return {
        "soul_field_enabled": soul_field_enabled(raw),
        # Personhood is core product; flag remains opt-out for edge installs.
        "soul_field_maturity": "stable",
        "build_os_advanced": build_os_advanced_enabled(raw),
        "build_os_advanced_maturity": "advanced",
        "rmb_enabled": rmb_enabled(raw),
        "rmb_maturity": "advanced",
        "defaults": dict(_DEFAULTS),
    }
