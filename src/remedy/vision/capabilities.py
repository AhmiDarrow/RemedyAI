"""Chat-model vision capability detection."""

from __future__ import annotations

from typing import Any

# Explicit non-vision (common text-only IDs that would otherwise false-positive).
_NON_VISION_EXACT = frozenset(
    {
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "codestral-latest",
        "codestral",
        "o1",
        "o1-mini",
        "o1-preview",
        "o3-mini",
        "o4-mini",
    }
)

# Substrings that strongly indicate multimodal vision support.
_VISION_HINTS = (
    "vision",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "claude-3",
    "claude-4",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    "gemini",
    "llava",
    "moondream",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen-vl",
    "qwen2.5vl",
    "qwen3-vl",
    "pixtral",
    "internvl",
    "minicpm-v",
    "smolvlm",
    "phi-3-vision",
    "phi-4-multimodal",
    "llama-3.2-vision",
    "llama3.2-vision",
    "grok-2-vision",
    "grok-vision",
    "grok-4",
    "grok-4.3",
    "grok-4.5",
    "grok-4.6",
)

# Providers whose catalog models are vision-capable by default (when unknown).
_PROVIDER_DEFAULT_VISION: dict[str, bool | None] = {
    "anthropic": True,  # modern Claude models accept images
    "google": True,
    "openai": None,  # depends on model
    "xai": None,
    "deepseek": False,
    "groq": None,
    "mistral": None,
    "ollama": None,
    "openrouter": None,
    "custom": None,
}


def supports_vision(
    provider: str | None,
    model: str | None,
    *,
    catalog_hint: bool | None = None,
    config_override: bool | None = None,
) -> bool:
    """Return True when the *chat* model should receive native image parts.

    Priority: explicit config override → catalog hint → heuristics.
    Conservative for unknown models (False → use visual decoder path when images present).
    """
    if config_override is not None:
        return bool(config_override)
    if catalog_hint is not None:
        return bool(catalog_hint)

    mid = (model or "").strip().lower()
    prov = (provider or "").strip().lower()

    if not mid:
        return False
    if mid in _NON_VISION_EXACT:
        return False
    if any(h in mid for h in _VISION_HINTS):
        return True

    # OpenAI GPT-4 family (non-o1) generally vision after 4o era
    if prov == "openai" and mid.startswith("gpt-4") and "o1" not in mid:
        return True

    default = _PROVIDER_DEFAULT_VISION.get(prov)
    if default is True:
        # Anthropic / Google defaults: assume vision unless known non-vision id
        return True
    if default is False:
        return False
    return False


def catalog_vision_flag(provider: str | None, model: str | None) -> bool | None:
    """Look up optional ``vision`` flag from PROVIDER_CATALOG model entries."""
    try:
        from remedy.interfaces.config import PROVIDER_CATALOG
    except Exception:
        return None
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    entry = PROVIDER_CATALOG.get(prov) or {}
    for m in entry.get("models") or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("id") or "") == mid and "vision" in m:
            return bool(m.get("vision"))
    return None


def resolve_supports_vision(
    provider: str | None,
    model: str | None,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    """Full resolve with config.toml overrides and catalog flags."""
    cfg = config or {}
    vision_raw = cfg.get("vision")
    vision_cfg: dict[str, Any] = vision_raw if isinstance(vision_raw, dict) else {}
    # Per-model map: vision.chat_model_vision = { "deepseek-chat": false }
    overrides = vision_cfg.get("chat_model_vision") or cfg.get("chat_model_vision")
    mid = (model or "").strip()
    override: bool | None = None
    if isinstance(overrides, dict) and mid in overrides:
        override = bool(overrides[mid])
    elif vision_cfg.get("force_decode") is True:
        override = False
    elif vision_cfg.get("force_native") is True:
        override = True

    hint = catalog_vision_flag(provider, model)
    return supports_vision(provider, model, catalog_hint=hint, config_override=override)
