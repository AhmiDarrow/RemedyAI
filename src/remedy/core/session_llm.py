"""Per-session LLM binding helpers (provider + model stay paired)."""

from __future__ import annotations

from typing import Any

from remedy.interfaces.config import infer_provider_from_model


def resolve_session_llm_bind(
    *,
    session: Any | None,
    req_provider: str | None,
    req_model: str | None,
) -> tuple[str | None, str | None]:
    """Return (provider, model) for this chat turn.

    Rules (sticky per-session bind — critical for multi-tab multi-provider):
    1. If the client sends **both** provider and model → use them (explicit switch).
    2. Else if the session row has a provider bind → use session provider +
       (req model if same family / provided with provider, else session model).
    3. Else if only model is known → infer provider from model id.
    4. Else (None, model or None) → global config fills in via sync.

    Never returns a model without a provider when inference is possible.
    """
    req_p = (req_provider or "").strip().lower() or None
    req_m = (req_model or "").strip() or None
    sess_p = None
    sess_m = None
    if session is not None:
        sess_p = (getattr(session, "llm_provider", None) or "").strip().lower() or None
        sess_m = (getattr(session, "model", None) or "").strip() or None

    # Explicit client pair wins (status bar / picker just set both).
    if req_p and req_m:
        return req_p, req_m

    # Sticky session pair — do not let a lone model string (stale UI / global
    # picker) override a stored provider+model (Grok tab while Settings is DeepSeek).
    if sess_p and sess_m:
        if req_p and req_p != sess_p:
            # Explicit provider change (status bar) without full pair.
            mid = req_m or sess_m
            return req_p, mid
        if req_m and not req_p:
            owner = infer_provider_from_model(req_m)
            if owner is None or owner == sess_p:
                return sess_p, req_m
            # Foreign model id without provider → ignore; keep sticky bind.
            return sess_p, sess_m
        return sess_p, sess_m

    # Session has provider only
    if sess_p:
        mid = req_m or sess_m
        return sess_p, mid

    # Model only (session or request)
    mid = req_m or sess_m
    if mid:
        prov = req_p or infer_provider_from_model(mid)
        return prov, mid

    return req_p, None


def session_llm_update_fields(
    *,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Fields to persist on chat_sessions — always pair provider with model."""
    out: dict[str, Any] = {}
    p = (provider or "").strip().lower() or None
    m = (model or "").strip() or None
    if m and not p:
        p = infer_provider_from_model(m)
    if p:
        out["llm_provider"] = p
    if m:
        out["model"] = m
    return out
