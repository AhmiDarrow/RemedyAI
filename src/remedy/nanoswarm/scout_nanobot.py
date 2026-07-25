"""Scout nanobot — cheap prep suggestions when intent looks like tool work.

Heuristics only (no network). Injects silent system notes so the frontier
model starts with high-signal first tools (list_dir, local_discover, git status)
instead of thrashing.
"""

from __future__ import annotations

import re
from typing import Any

_GIT_RE = re.compile(
    r"\b(git|commit|branch|pr\b|pull request|merge|rebase|diff|stage)\b", re.I
)
_BUILD_RE = re.compile(
    r"\b(build|test|lint|ci|pytest|cargo|npm|uv |compile|typecheck)\b", re.I
)
_FIND_RE = re.compile(
    r"\b(find|where is|locate|discover|installed|which |path to)\b", re.I
)
_IMAGE_RE = re.compile(r"\b(image|comfy|flux|generate.*(pic|image|art)|screenshot)\b", re.I)
_DEBUG_RE = re.compile(
    r"\b(error|fail|bug|stack|traceback|exception|broken|not work)\b", re.I
)


class ScoutNanobot:
    """Suggest first-wave tools for tool/plan intents."""

    def __init__(self) -> None:
        self.scouts_run = 0
        self.last: dict[str, Any] | None = None

    def scout(
        self,
        user_text: str = "",
        *,
        intent: str = "chat",
        project_path: str | None = None,
    ) -> dict[str, Any]:
        text = (user_text or "").strip()
        intent = (intent or "chat").lower()
        if intent not in ("tool", "plan", "skill") and len(text) < 12:
            out = {
                "bot": "scout",
                "active": False,
                "suggest_tools": [],
                "system_hint": "",
            }
            self.last = out
            return out

        tools: list[str] = []
        tips: list[str] = []

        if _FIND_RE.search(text) or "comfy" in text.lower():
            tools.extend(["local_discover", "list_dir"])
            tips.append("Use local_discover before hunting installs on disk.")
        if _GIT_RE.search(text):
            tools.append("bash_exec")  # git status — model chooses
            tips.append("Start with git status / branch, not recursive file dumps.")
        if _BUILD_RE.search(text):
            tools.extend(["list_dir", "bash_exec"])
            tips.append("Detect project root + package manager before long builds.")
        if _IMAGE_RE.search(text):
            tools.append("comfyui")
            tips.append("Prefer comfyui tool (status/locate/generate) over manual paths.")
        if _DEBUG_RE.search(text):
            tools.extend(["list_dir", "file_read"])
            tips.append("Read the failing file and recent stderr before rewriting.")
        if intent in ("tool", "plan") and not tools:
            tools.extend(["list_dir", "file_read"])
            tips.append("Orient with list_dir on project root, then targeted reads.")

        # Dedupe preserve order
        seen: set[str] = set()
        uniq = []
        for t in tools:
            if t not in seen:
                seen.add(t)
                uniq.append(t)

        hint = ""
        if uniq or tips:
            parts = []
            if uniq:
                parts.append("First tools to consider: " + ", ".join(uniq[:6]))
            if tips:
                parts.append(tips[0])
            if project_path:
                parts.append(f"Project: {project_path}")
            hint = "[Continuity/Scout] " + " · ".join(parts)

        out = {
            "bot": "scout",
            "active": bool(hint),
            "suggest_tools": uniq[:8],
            "tips": tips[:4],
            "system_hint": hint,
            "intent": intent,
        }
        self.scouts_run += 1
        self.last = out
        return out

    def status(self) -> dict[str, Any]:
        return {
            "bot": "scout",
            "scouts_run": self.scouts_run,
            "last_tools": (self.last or {}).get("suggest_tools") or [],
            "last_active": bool((self.last or {}).get("active")),
        }
