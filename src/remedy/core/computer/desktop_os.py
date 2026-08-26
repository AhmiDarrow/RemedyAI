"""OS desktop capture/input: Windows native, Linux xdotool/xdg-open."""

from __future__ import annotations

import sys
from types import ModuleType


def native() -> ModuleType:
    """Return the desktop module for this OS. Windows path is unchanged."""
    if sys.platform == "win32":
        from remedy.core.computer import desktop_win as win

        return win
    from remedy.core.computer import desktop_linux as linux

    return linux
