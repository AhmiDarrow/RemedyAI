"""Chat-model vision capability detection."""

from __future__ import annotations

from typing import Any

# Fallback only: what the provider's endpoint reports, then the owner's
# per-model setting (vision.chat_model_vision), win over this. Family words
# only — never version numbers, which go stale as providers ship new models.
_VISION_HINTS = (
    "vision",
    "-vl",
    "vl-",
    "llava",
    "moondream",
    "pixtral",
    "internvl",
    "minicpm-v",
    "smolvlm",
    "multimodal",
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

    Priority: explicit config override → endpoint hint → family rules.
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
    if any(h in mid for h in _VISION_HINTS):
        return True
    # OpenAI chat models in the GPT line take images; reasoning-only and
    # audio/realtime variants are left to the endpoint or the owner's setting.
    if prov == "openai" and mid.startswith("gpt-") and not any(
        x in mid for x in ("audio", "realtime", "transcribe", "tts")
    ):
        return True

    default = _PROVIDER_DEFAULT_VISION.get(prov)
    if default is True:
        # Anthropic / Google defaults: assume vision unless known non-vision id
        return True
    if default is False:
        return False
    return False


def endpoint_vision_flag(provider: str | None, model: str | None) -> bool | None:
    """What the provider's own endpoint said about image input, if anything.

    OpenRouter / Mistral / xAI / Ollama ``/api/show`` / LM Studio describe
    modalities; that beats every name heuristic below.
    """
    try:
        from remedy.interfaces.model_discovery import live_known_models
    except Exception:
        return None
    row = live_known_models(provider).get((model or "").strip())
    if isinstance(row, dict) and isinstance(row.get("vision"), bool):
        return bool(row["vision"])
    return None


def resolve_supports_vision(
    provider: str | None,
    model: str | None,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    """Full resolve: config.toml override, then the endpoint's flag, then family rules."""
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

    hint = endpoint_vision_flag(provider, model)
    return supports_vision(provider, model, catalog_hint=hint, config_override=override)
