"""App control — Remedy driving her own interface (split-second, in-house).

The computer-use tools drive the *world* (browser, desktop apps). This bus
drives Remedy's *own* app: switch surface (Grove ⇄ Studio), open a goal,
focus the composer, open settings. These are things she should do inside
herself instantly, without asking the user to click.

Mechanism mirrors the browser ``ui_command`` bus: the agent enqueues a
command (via the ``app_control`` tool); the desktop/web client polls
``GET /api/app/command?take=1`` on a fast interval and dispatches it. A
tiny FIFO queue (single-user desktop) with a cap so nothing accumulates.

Commands are declarative and safe by construction — they only move the
owner's own UI around; nothing here spends money, touches files, or leaves
the app.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# Actions the client knows how to dispatch. Keep in lockstep with the
# frontend dispatcher (api/appControl.ts + App.tsx).
VALID_ACTIONS = frozenset(
    {
        "switch_surface",   # target: grove | studio | alongside | storyline | home
        "open_goal",        # goal_id: str
        "focus_composer",   # (no args)
        "open_settings",    # section?: str
        "open_panel",       # panel: overlay, rail, or settings
        "close_ui",         # close overlays / floating panels
        "new_session",      # (no args)
    }
)

# Grove/Studio are top-level surfaces. Alongside + Storyline live *inside*
# Grove — the owner talks about them as places she should just go.
VALID_SURFACE_TARGETS = frozenset(
    {"grove", "studio", "alongside", "storyline", "home"}
)
_SURFACE_ALIASES = {
    "home": "grove",
    "plots": "grove",
    "partner": "grove",
    "workbench": "studio",
    "code": "studio",
}

# Overlays + Studio rails + floating panels — one catalog for open_panel.
VALID_PANELS = frozenset(
    {
        "memory",
        "skills",
        "help",
        "diagnostics",
        "usage",
        "time_travel",
        "about",
        "settings",
        "browser",
        "terminal",
        "files",
        "scratch",
        "sessions",
    }
)
_PANEL_ALIASES = {
    "wiki": "help",
    "manual": "help",
    "f1": "help",
    "diag": "diagnostics",
    "logs": "diagnostics",
    "tokens": "usage",
    "cost": "usage",
    "undo": "time_travel",
    "timetravel": "time_travel",
    "file": "files",
    "explorer": "files",
    "term": "terminal",
    "shell": "terminal",
    "powershell": "terminal",
    "scratchpad": "scratch",
    "notepad": "scratch",
    "chat": "sessions",
    "chats": "sessions",
}

# Lockstep with desktop/src/utils/settingsSearch.ts SettingsSectionId.
VALID_SETTINGS_SECTIONS = frozenset(
    {
        "provider",
        "provider-catalog",
        "you-agent",
        "voice",
        "phone",
        "workspace",
        "access",
        "security-power",
        "privacy",
        "always-ready",
        "tool-process",
        "vision",
        "rmb",
        "memory-harness",
        "theme",
        "advanced",
        "help",
        "mcp",
        "channels",
        "assistant",
        "about",
        "license",
    }
)
_SECTION_ALIASES = {
    "model": "provider",
    "llm": "provider",
    "api": "provider",
    "key": "provider",
    "catalog": "provider-catalog",
    "you": "you-agent",
    "name": "you-agent",
    "persona": "you-agent",
    "speak": "voice",
    "hear": "voice",
    "mic": "voice",
    "telephony": "phone",
    "sip": "phone",
    "project": "workspace",
    "folder": "workspace",
    "jail": "access",
    "permissions": "access",
    "approval": "security-power",
    "approvals": "security-power",
    "web": "security-power",
    "startup": "always-ready",
    "tray": "always-ready",
    "process": "tool-process",
    "smolvlm": "vision",
    "local-vision": "vision",
    "local-model": "rmb",
    "local_model": "rmb",
    "harness": "memory-harness",
    "appearance": "theme",
    "color": "theme",
    "shortcuts": "help",
    "messengers": "channels",
    "telegram": "channels",
    "discord": "channels",
    "calendar": "assistant",
    "mail": "assistant",
    "gmail": "assistant",
    "version": "about",
    "updates": "about",
}

_PATCH_KEY_TO_SECTION = {
    "llm_provider": "provider",
    "llm_model": "provider",
    "llm_api_key": "provider",
    "llm_base_url": "provider",
    "approval_mode": "security-power",
    "thinking_level": "security-power",
    "user_name": "you-agent",
    "name": "you-agent",
    "agent_gender": "you-agent",
    "persona": "you-agent",
    "access_scope": "access",
    "project_path": "workspace",
    "vision_enabled": "vision",
    "sleev_enabled": "security-power",
}


def _norm_token(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_").replace("-", "_")


def normalize_surface_target(raw: str) -> str | None:
    """Map owner/model phrasing onto a switch_surface target, or None."""
    t = (raw or "").strip().lower().replace(" ", "_")
    t = _SURFACE_ALIASES.get(t, t)
    if t in VALID_SURFACE_TARGETS:
        return t
    return None


def normalize_panel(raw: str) -> str | None:
    """Map owner/model phrasing onto an open_panel destination."""
    t = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    t = _PANEL_ALIASES.get(t, t)
    if t in VALID_PANELS:
        return t
    return None


def normalize_settings_section(raw: str) -> str | None:
    """Map owner/model phrasing onto a Settings section id."""
    t = (raw or "").strip().lower()
    if t in VALID_SETTINGS_SECTIONS:
        return t
    key = _norm_token(t)
    aliased = _SECTION_ALIASES.get(key) or _SECTION_ALIASES.get(t)
    if aliased:
        return aliased
    dashed = key.replace("_", "-")
    if dashed in VALID_SETTINGS_SECTIONS:
        return dashed
    return None


def infer_settings_section(patch: dict[str, Any] | None) -> str | None:
    """Best Settings section to show after an update_settings patch."""
    if not patch:
        return None
    for key, section in _PATCH_KEY_TO_SECTION.items():
        if key in patch:
            return section
    return None


_MAX_QUEUE = 32
_MAX_AGE_S = 90.0


class _AppControlBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._q: deque[dict[str, Any]] = deque(maxlen=_MAX_QUEUE)
        self._seq = 0

    def enqueue(self, action: str, **params: Any) -> dict[str, Any]:
        act = (action or "").strip()
        if act not in VALID_ACTIONS:
            return {"ok": False, "error": f"unknown app action {act!r}"}
        with self._lock:
            self._seq += 1
            cmd = {
                "id": f"app-{self._seq}",
                "action": act,
                "params": {k: v for k, v in params.items() if v is not None},
                "ts": time.time(),
            }
            self._q.append(cmd)
        return {"ok": True, "command": cmd}

    def _prune(self) -> None:
        now = time.time()
        while self._q and (now - float(self._q[0].get("ts") or 0)) > _MAX_AGE_S:
            self._q.popleft()

    def peek(self) -> dict[str, Any] | None:
        with self._lock:
            self._prune()
            return dict(self._q[0]) if self._q else None

    def take(self) -> dict[str, Any] | None:
        with self._lock:
            self._prune()
            return dict(self._q.popleft()) if self._q else None

    def clear(self) -> None:
        with self._lock:
            self._q.clear()


_bus = _AppControlBus()


def app_control_bus() -> _AppControlBus:
    return _bus


def request_app_action(action: str, **params: Any) -> dict[str, Any]:
    """Enqueue one app-control command for the client to dispatch."""
    return _bus.enqueue(action, **params)
