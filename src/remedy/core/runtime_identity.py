"""Who is this process running as? One authority, intent-named predicates.

Frozen-vs-dev-vs-gateway guards used to be scattered raw ``sys.frozen`` /
``REMEDY_DESKTOP*`` checks that each answered a slightly different question
— which is exactly how a dev-checkout Desktop got treated as a packaged
install (self-inject silently skipped its restart). Ask the question you
mean:

- :func:`is_frozen_install` — the executable is a PyInstaller bundle; the
  source tree on disk is NOT the running code.
- :func:`is_desktop_sidecar` — serve was spawned by the Tauri desktop
  parent (dev checkout or frozen; lib.rs sets the env var either way).
- :func:`is_desktop_runtime` — any desktop-launched context (sidecar env,
  desktop env, or frozen). The old ``_is_packaged_runtime`` semantic.
- :func:`runs_this_checkout` — repo edits become live on restart.

A guard test (``tests/test_runtime_identity_guard.py``) keeps new raw
checks from creeping in outside this module.
"""

from __future__ import annotations

import os
import sys

_TRUTHY = ("1", "true", "yes", "on")


def _env_on(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def is_frozen_install() -> bool:
    """PyInstaller-frozen exe (packaged Desktop sidecar / installed CLI)."""
    return bool(getattr(sys, "frozen", False))


def is_desktop_sidecar() -> bool:
    """Serve spawned by the Tauri desktop parent — dev checkout OR frozen."""
    return _env_on("REMEDY_DESKTOP_SIDECAR")


def is_desktop_runtime() -> bool:
    """Any desktop-launched context: sidecar, desktop env, or frozen exe."""
    return is_desktop_sidecar() or _env_on("REMEDY_DESKTOP") or is_frozen_install()


def runs_this_checkout() -> bool:
    """The running code IS the source tree — a serve restart loads repo edits.

    True for dev-checkout Desktop, CLI-from-source, gateway-from-source and
    tests; False only for frozen installs.
    """
    return not is_frozen_install()
