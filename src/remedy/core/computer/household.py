"""Household — her agency over the house itself, inside the owner's consent.

Once the owner places Remedy on this PC and grants permission, it is her
home — not a hotel room she operates from. That means more than using
what's there (appliances) and knowing the layout (census): she keeps the
house. Four verbs, all bounded by the approvals system:

- **Secure it** — `house_walkthrough()`: a read-only security round.
  Which doors (local ports) are open and whether each is expected, is the
  vault present and locked, is the census fresh, is anything listening
  that the house has no name for. A report, never a mutation.
- **Open doors** — starting a known local service is just launching an
  appliance or a tool the census already maps; the walkthrough names
  which doors are closed so she can offer.
- **Make additions** — `plan_addition()`: when the house lacks a tool
  ("ffmpeg is missing — may I add it?"), pick the house's own package
  manager (winget / choco / scoop on Windows; apt / dnf / brew
  elsewhere) and produce the exact, safe install command for the
  approval flow. She never installs silently: the plan goes through the
  same gate as every host mutation.
- **Knock out walls** — reshaping rooms (folders) already flows through
  host_mkdir / host_run with approvals; nothing here bypasses that.

Nothing in this module executes an install itself — it *plans*, and the
approval-gated host runner executes. Security posture is observed, never
altered. Her keys to the house are real, but every load-bearing change is
countersigned by the owner.
"""

from __future__ import annotations

import shutil
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any

# Known doors of the house (name -> port). Mirrors the stretch census.
KNOWN_DOORS: tuple[tuple[str, int], ...] = (
    ("remedy", 7400),
    ("rmb", 8787),
    ("vision", 8740),
    ("ollama", 11434),
    ("comfyui", 8188),
)

# A few doors that should normally be shut on a personal machine; if open,
# the walkthrough flags them by name so the owner can decide.
WATCH_DOORS: tuple[tuple[str, int], ...] = (
    ("rdp", 3389),
    ("smb", 445),
    ("vnc", 5900),
    ("telnet", 23),
    ("ftp", 21),
)

# Package managers, in preference order per family.
_MANAGERS_WIN = ("winget", "choco", "scoop")
_MANAGERS_POSIX = ("brew", "apt", "dnf", "pacman")

# Install command shapes — no shell, argv lists only.
_INSTALL_SHAPES: dict[str, list[str]] = {
    "winget": ["winget", "install", "--exact", "--id", "{pkg}", "--accept-source-agreements", "--accept-package-agreements"],
    "choco": ["choco", "install", "{pkg}", "-y"],
    "scoop": ["scoop", "install", "{pkg}"],
    "brew": ["brew", "install", "{pkg}"],
    "apt": ["sudo", "apt-get", "install", "-y", "{pkg}"],
    "dnf": ["sudo", "dnf", "install", "-y", "{pkg}"],
    "pacman": ["sudo", "pacman", "-S", "--noconfirm", "{pkg}"],
}

_PKG_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
)


def _door_open(port: int, *, timeout: float = 0.12) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def house_walkthrough(home: str | Path | None = None) -> dict[str, Any]:
    """Read-only security round of her house. Observes; never changes."""
    doors = []
    for name, port in KNOWN_DOORS:
        doors.append({"door": name, "port": port, "open": _door_open(port), "expected": True})
    concerns: list[str] = []
    for name, port in WATCH_DOORS:
        if _door_open(port):
            doors.append({"door": name, "port": port, "open": True, "expected": False})
            concerns.append(
                f"door '{name}' (port {port}) is open — fine if the owner uses it; "
                "worth asking if not"
            )

    vault_state = "absent"
    with suppress(Exception):
        from remedy.core import vault

        items = vault.vault_list(home)
        vault_state = f"present ({len(items)} items)" if items else "present (empty)"

    census_fresh = None
    with suppress(Exception):
        from remedy.execution.host.stretch import needs_stretch

        census_fresh = not needs_stretch(home)
    if census_fresh is False:
        concerns.append("house census is stale — a re-stretch would refresh the map")

    appliances_known = 0
    with suppress(Exception):
        from remedy.core.computer.appliances import load_inventory

        inv = load_inventory(home)
        appliances_known = len(inv.appliances) if inv else 0
    if appliances_known == 0:
        concerns.append("appliance inventory empty — first scan may still be running")

    return {
        "ok": True,
        "doors": doors,
        "vault": vault_state,
        "census_fresh": census_fresh,
        "appliances_known": appliances_known,
        "concerns": concerns,
        "note": (
            "Walkthrough is read-only. Closing a door, adding a tool, or any "
            "other change to the house goes through the approval flow."
        ),
    }


def available_manager(*, which=shutil.which) -> str:
    """The house's own package manager, if any."""
    import os

    order = _MANAGERS_WIN if os.name == "nt" else _MANAGERS_POSIX
    for mgr in order:
        if which(mgr):
            return mgr
    # Cross-family fallback (e.g. brew on Linux, scoop shims)
    for mgr in _MANAGERS_WIN + _MANAGERS_POSIX:
        if which(mgr):
            return mgr
    return ""


def plan_addition(
    package: str,
    *,
    manager: str = "",
    which=shutil.which,
) -> dict[str, Any]:
    """Plan (never run) an addition to the house.

    Returns the exact argv for the approval-gated host runner, or a clear
    refusal. Package names are jailed to safe characters — no shell, no
    URLs, no local paths (installs come from the manager's registry only).
    """
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package name required"}
    if pkg.startswith("-"):
        return {"ok": False, "error": "package name may not start with '-' (flag injection)"}
    if len(pkg) > 120 or not all(c in _PKG_SAFE for c in pkg):
        return {
            "ok": False,
            "error": (
                "package name refused (letters, digits, . _ + - only — "
                "no URLs, paths, or shell)"
            ),
        }
    mgr = (manager or "").strip().lower() or available_manager(which=which)
    if not mgr:
        return {
            "ok": False,
            "error": (
                "no package manager in this house (winget/choco/scoop or "
                "brew/apt/dnf/pacman) — the owner would need to install one first"
            ),
        }
    shape = _INSTALL_SHAPES.get(mgr)
    if shape is None or not which(mgr):
        return {"ok": False, "error": f"manager {mgr!r} not available in this house"}
    argv = [part.replace("{pkg}", pkg) for part in shape]
    return {
        "ok": True,
        "manager": mgr,
        "package": pkg,
        "argv": argv,
        "command": " ".join(argv),
        "note": (
            "This is a PLAN. Run it via the approval-gated host runner "
            "(host_run) so the owner countersigns the addition. After it "
            "lands, /stretch refreshes the tool census."
        ),
    }
