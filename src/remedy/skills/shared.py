"""Process-wide shared SkillRegistry (avoid re-discover on every speculative prep)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from remedy.skills.registry import SkillRegistry

_lock = threading.Lock()
_registry: SkillRegistry | None = None
_home_key: str | None = None


def get_shared_registry(
    *,
    home_dir: str | Path | None = None,
    force_reload: bool = False,
) -> SkillRegistry:
    """Return a process-global SkillRegistry with defaults discovered once."""
    global _registry, _home_key
    key = str(Path(home_dir).expanduser()) if home_dir else ""
    with _lock:
        if _registry is not None and not force_reload and _home_key == key:
            return _registry
        reg = SkillRegistry()
        reg.discover_defaults(home_dir=home_dir)
        _registry = reg
        _home_key = key
        return reg


def invalidate_shared_registry() -> None:
    """Drop shared registry (skill import/archive/trust)."""
    global _registry, _home_key
    with _lock:
        _registry = None
        _home_key = None
    try:
        from remedy.nanoswarm import get_swarm
        from remedy.nanoswarm.events import SwarmEvent

        get_swarm().dispatch(SwarmEvent("skill_library_changed", {}))
    except Exception:
        pass


def skill_packs_path(home_dir: str | Path | None = None) -> Path:
    if home_dir:
        base = Path(home_dir).expanduser()
    else:
        try:
            from remedy.core.security import get_home_dir

            base = get_home_dir()
        except Exception:
            base = Path("~/.remedy").expanduser()
    return base / "skill_packs.json"


def load_skill_packs(home_dir: str | Path | None = None) -> dict[str, Any]:
    """{ packs: { name: { skills: [...], project_path?: str } }, enabled: [pack] }."""
    path = skill_packs_path(home_dir)
    if not path.is_file():
        return {"packs": {}, "enabled": []}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"packs": {}, "enabled": []}
        return {
            "packs": data.get("packs") if isinstance(data.get("packs"), dict) else {},
            "enabled": list(data.get("enabled") or []),
        }
    except Exception:
        return {"packs": {}, "enabled": []}


def save_skill_packs(data: dict[str, Any], home_dir: str | Path | None = None) -> Path:
    import json

    path = skill_packs_path(home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    invalidate_shared_registry()
    return path


def skills_in_enabled_packs(home_dir: str | Path | None = None) -> set[str] | None:
    """Return set of skill names allowed by enabled packs, or None if no pack filter."""
    data = load_skill_packs(home_dir)
    packs = data.get("packs") or {}
    enabled = list(data.get("enabled") or [])
    if not packs or not enabled:
        return None  # no filter — all non-archived
    names: set[str] = set()
    for ep in enabled:
        p = packs.get(ep) or {}
        for s in p.get("skills") or []:
            names.add(str(s))
    return names
