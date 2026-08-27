"""Per-session LLM binding helpers (provider + model stay paired)."""

from __future__ import annotations

from typing import Any

from remedy.interfaces.config import infer_provider_from_model, normalize_llm_settings


def _normalize_pair(
    provider: str | None, model: str | None
) -> tuple[str | None, str | None]:
    """Snap garbage / cross-wired model ids to a valid provider+model pair."""
    p = (provider or "").strip().lower() or None
    m = (model or "").strip() or None
    if not p and not m:
        return None, None
    try:
        np, nm, _ = normalize_llm_settings(p, m, None)
        return (np or p), (nm or m)
    except Exception:
        return p, m


def resolve_session_llm_bind(
    *,
    session: Any | None,
    req_provider: str | None,
    req_model: str | None,
    use_live_rmb: bool = True,
) -> tuple[str | None, str | None]:
    """Return (provider, model) for this chat turn.

    Rules (sticky per-session bind — critical for multi-tab multi-provider):
    1. If the client sends **both** provider and model → use them (explicit switch).
    2. Else if the session row has a provider bind → use session provider +
       (req model if same family / provided with provider, else session model).
    3. Else if only model is known → infer provider from model id.
    4. Else (None, model or None) → global config fills in via sync.

    Never returns a model without a provider when inference is possible.
    Always normalizes closed-catalog garbage (e.g. not-a-real-model-zzz → default).
    """
    req_p = (req_provider or "").strip().lower() or None
    req_m = (req_model or "").strip() or None
    sess_p = None
    sess_m = None
    if session is not None:
        sess_p = (getattr(session, "llm_provider", None) or "").strip().lower() or None
        sess_m = (getattr(session, "model", None) or "").strip() or None

    def _rmb_live_stem() -> str | None:
        """Currently loaded GGUF stem — source of truth when provider is RMB."""
        try:
            from pathlib import Path

            from remedy.runtime.rmb.config import merged_state_cached

            st = merged_state_cached()
            mp = str(st.get("model_path") or "").strip()
            if mp:
                return Path(mp).stem
            mid = str(st.get("model_id") or "").strip()
            return mid or None
        except Exception:
            return None

    def _addr(p: str | None, m: str | None) -> tuple[str | None, str | None]:
        # In-flight address may use the loaded GGUF; persist must not.
        if use_live_rmb and (p or "").lower() == "rmb":
            live = _rmb_live_stem()
            if live:
                return p, live
        return p, m

    # Explicit client pair wins (status bar / picker just set both).
    if req_p and req_m:
        p, m = _normalize_pair(req_p, req_m)
        return _addr(p, m)

    # Sticky session pair — do not let a lone model string (stale UI / global
    # picker) override a stored provider+model (Grok tab while Settings is DeepSeek).
    if sess_p and sess_m:
        if req_p and req_p != sess_p:
            # Explicit provider change (status bar) without full pair.
            mid = req_m or sess_m
            p, m = _normalize_pair(req_p, mid)
            return _addr(p, m)
        if req_m and not req_p:
            owner = infer_provider_from_model(req_m)
            if owner is None or owner == sess_p:
                p, m = _normalize_pair(sess_p, req_m)
                return _addr(p, m)
            # Foreign model id without provider → ignore; keep sticky bind.
            p, m = _normalize_pair(sess_p, sess_m)
            return _addr(p, m)
        p, m = _normalize_pair(sess_p, sess_m)
        return _addr(p, m)

    # Session has provider only
    if sess_p:
        mid = req_m or sess_m
        return _normalize_pair(sess_p, mid)

    # Model only (session or request)
    mid = req_m or sess_m
    if mid:
        prov = req_p or infer_provider_from_model(mid)
        return _normalize_pair(prov, mid)

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
    p, m = _normalize_pair(p, m)
    if p:
        out["llm_provider"] = p
    if m:
        out["model"] = m
    return out
