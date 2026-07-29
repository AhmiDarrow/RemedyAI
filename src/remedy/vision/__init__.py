"""Local visual decoder for text-only chat models.

Install of llama-server + pinned SmolVLM2 2.2B (Apache 2.0). Decodes images to
structured text so the main agent can reason without native multimodal.
"""

from __future__ import annotations

from remedy.vision.capabilities import supports_vision
from remedy.vision.catalog import DEFAULT_MODEL_ID, VISION_MODELS, get_model_spec
from remedy.vision.service import (
    cancel_install,
    decode_for_turn,
    get_status,
    start_install,
    uninstall,
)

__all__ = [
    "DEFAULT_MODEL_ID",
    "VISION_MODELS",
    "cancel_install",
    "decode_for_turn",
    "get_model_spec",
    "get_status",
    "start_install",
    "supports_vision",
    "uninstall",
]
