"""Helper nanobot — reserved role on the same pinned Qwen (not a second model).

Product UI ships later; architecture and job kind are ready.
"""

from __future__ import annotations

from typing import Any


class HelperNanobot:
    """Future chat-sidekick jobs on shared llama-server (role=helper)."""

    def __init__(self) -> None:
        self.enabled = False  # product surface off until designed
        self.jobs_run = 0

    def status(self) -> dict[str, Any]:
        return {
            "bot": "helper",
            "enabled": self.enabled,
            "jobs_run": self.jobs_run,
            "role_model": "qwen2.5-vl-3b",
            "note": "Reserved — same bundled Qwen as vision/nano; UI later.",
        }

    def draft_help(self, prompt: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "Helper bot not enabled in this release"}
        return {"ok": False, "error": "Helper inference not enabled yet", "prompt": prompt[:200]}
