"""Key-combo resolution — re-export tip shared ``desktop_common``."""

from __future__ import annotations

from typing import Any

from remedy.core.computer.desktop_common import resolve_key_combo as _resolve

__all__ = ["resolve_key_combo"]


def resolve_key_combo(key: str, *, vk_scan: Any = None) -> list[int]:
    return _resolve(key, vk_scan=vk_scan)
