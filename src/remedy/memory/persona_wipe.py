"""Persona wipe — forget who the user is, keep chats / keys / skills.

Does **not** delete ``~/.remedy`` (that is uninstall / NSIS full wipe).
Does **not** delete chat transcripts or API keys.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from remedy.home import default_home

logger = logging.getLogger(__name__)

CONFIRM_PHRASE = "WIPE"


def _home(home: Path | str | None = None) -> Path:
    if home:
        return Path(home).expanduser()
    with contextlib.suppress(Exception):
        from remedy.interfaces.config import load_config

        h = (load_config() or {}).get("home_dir")
        if h:
            return Path(h).expanduser()
    return default_home()


def reset_soul_persona(home: Path | str | None = None) -> bool:
    """Reset rapport / episodes / pledges; keep partner name + gender."""
    from remedy.memory.soul.field import (
        SoulField,
        clear_soul_cache,
        load_soul_field,
        save_soul_field,
    )

    prev = load_soul_field(home)
    fresh = SoulField(
        identity_name=prev.identity_name or "Remedy",
        identity_gender=prev.identity_gender or "female",
        identity_vow=prev.identity_vow,
    )
    clear_soul_cache()
    save_soul_field(fresh, home)
    return True


def _wipe_partner_state_files(home: Path) -> int:
    from remedy.memory.partner_state.state import _registry, _registry_lock

    with _registry_lock:
        _registry.clear()
    n = 0
    root = home / "partner_state"
    if root.is_dir():
        for p in root.glob("*.json"):
            with contextlib.suppress(OSError):
                p.unlink()
                n += 1
    return n


def _wipe_life_goals(home: Path) -> bool:
    path = home / "life_goals.json"
    if path.is_file():
        path.unlink(missing_ok=True)
        return True
    return False


async def wipe_persona(
    memory: Any,
    *,
    home: Path | str | None = None,
    runtime: Any = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Erase Partner Memory / soul residue / life goals. Require confirm phrase."""
    phrase = str(confirm or "").strip().upper()
    if phrase != CONFIRM_PHRASE:
        raise ValueError(f'Type {CONFIRM_PHRASE} to confirm persona wipe')

    root = _home(home)
    stats: dict[str, Any] = {
        "ok": True,
        "profile_reset": False,
        "user_fact_entries": 0,
        "soul_reset": False,
        "partner_state_files": 0,
        "life_goals_removed": False,
    }

    if memory is not None:
        with contextlib.suppress(Exception):
            from remedy.memory.profile import UserProfile

            uid = "default"
            with contextlib.suppress(Exception):
                prev = await memory.get_or_create_profile()
                uid = str(getattr(prev, "user_id", None) or "default")
            await memory.save_user_profile(UserProfile(user_id=uid))
            stats["profile_reset"] = True
        with contextlib.suppress(Exception):
            n = await memory.delete_by_type("user_fact")
            stats["user_fact_entries"] = int(n or 0)

    with contextlib.suppress(Exception):
        stats["soul_reset"] = reset_soul_persona(root)

    with contextlib.suppress(Exception):
        stats["partner_state_files"] = _wipe_partner_state_files(root)

    with contextlib.suppress(Exception):
        stats["life_goals_removed"] = _wipe_life_goals(root)

    if runtime is not None:
        with contextlib.suppress(Exception):
            runtime._partner_state = None

    logger.info("persona wipe: %s", stats)
    return stats
