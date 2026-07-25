"""Local neural roles that share the single pinned Qwen model."""

from __future__ import annotations

from enum import StrEnum


class LocalRole(StrEnum):
    """Jobs on the shared llama-server — same GGUF, different prompts."""

    VISION = "vision"
    NANO = "nano"
    HELPER = "helper"  # reserved; same model, later product surface


def role_uses_mmproj(role: LocalRole | str) -> bool:
    """mmproj required only for multimodal image decode."""
    r = role.value if isinstance(role, LocalRole) else str(role)
    return r == LocalRole.VISION.value
