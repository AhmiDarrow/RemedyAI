"""Reasoning effort and context settings (mirrors native/go/httpapi/generation_settings.go).

Top-level keys are the default for every model; ``model_settings."<model>"``
(or ``"<provider>/<model>"``) overrides them per model. ``0`` / ``""`` means
unset, which keeps each provider's built-in behavior.
"""

from __future__ import annotations

from typing import Any

REASONING_EFFORT = "reasoning_effort"
THINKING_BUDGET = "thinking_budget_tokens"
MAX_OUTPUT_TOKENS = "max_output_tokens"
CONTEXT_WINDOW = "context_window_tokens"
MODEL_SETTINGS = "model_settings"

GENERATION_KEYS = (REASONING_EFFORT, THINKING_BUDGET, MAX_OUTPUT_TOKENS, CONTEXT_WINDOW)
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
_EFFORT_ALIASES = {"": "", "default": "", "auto": "", "off": "none", "max": "xhigh", "maximum": "xhigh"}
LIMITS = {
    THINKING_BUDGET: (1024, 128_000),
    MAX_OUTPUT_TOKENS: (256, 256_000),
    CONTEXT_WINDOW: (4096, 2_000_000),
}


def normalize_effort(raw: Any) -> str:
    v = str(raw if raw is not None else "").strip().lower()
    if v in _EFFORT_ALIASES:
        return _EFFORT_ALIASES[v]
    if v in EFFORTS:
        return v
    raise ValueError(f"reasoning effort {raw!r} is not one of {', '.join(EFFORTS)}")


def _token_count(key: str, raw: Any) -> int:
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be a number")
    try:
        n = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc
    lo, hi = LIMITS[key]
    if n != 0 and not lo <= n <= hi:
        raise ValueError(f"{key} must be 0 (default) or between {lo} and {hi}, got {n}")
    return n


def _normalize_entry(entry: dict[str, Any], *, where: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    prefix = f"{where}: " if where else ""
    try:
        for k, v in entry.items():
            if k == REASONING_EFFORT:
                out[k] = normalize_effort(v)
            elif k in LIMITS:
                out[k] = _token_count(k, v)
    except ValueError as exc:
        raise ValueError(f"{prefix}{exc}") from exc
    return out


def _is_unset(entry: dict[str, Any]) -> bool:
    return not any(entry.get(k) for k in GENERATION_KEYS)


def normalize_generation_patch(patch: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Validate and normalize the reasoning/context keys of a settings patch in place.

    ``model_settings`` is merged into the stored table so changing one model
    keeps the others; an entry whose values are all unset is removed.
    """
    flat = {k: patch[k] for k in GENERATION_KEYS if k in patch and patch[k] is not None}
    patch.update(_normalize_entry(flat))
    if MODEL_SETTINGS not in patch or patch[MODEL_SETTINGS] is None:
        return
    table = patch[MODEL_SETTINGS]
    if not isinstance(table, dict):
        raise ValueError("model_settings must be a table of model id → settings")
    stored = cfg.get(MODEL_SETTINGS)
    merged: dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
    for model, entry in table.items():
        name = str(model).strip()
        if not name or not isinstance(entry, dict):
            raise ValueError(f"model_settings[{model!r}] must be a table")
        unknown = sorted(set(entry) - set(GENERATION_KEYS))
        if unknown:
            raise ValueError(f"model_settings[{model!r}]: unknown key {unknown[0]!r}")
        clean = _normalize_entry(entry, where=f"model_settings[{model!r}]")
        if _is_unset(clean):
            merged.pop(name, None)
        else:
            merged[name] = clean
    patch[MODEL_SETTINGS] = merged


def generation_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    """Public view for ``settings show`` and GET /api/settings."""
    try:
        flat = _normalize_entry({k: cfg.get(k) for k in GENERATION_KEYS})
    except ValueError:
        flat = {}
    table = cfg.get(MODEL_SETTINGS)
    return {
        REASONING_EFFORT: flat.get(REASONING_EFFORT, ""),
        THINKING_BUDGET: flat.get(THINKING_BUDGET, 0),
        MAX_OUTPUT_TOKENS: flat.get(MAX_OUTPUT_TOKENS, 0),
        CONTEXT_WINDOW: flat.get(CONTEXT_WINDOW, 0),
        MODEL_SETTINGS: dict(table) if isinstance(table, dict) else {},
    }
