"""Per-session provider+model bind resolution."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.session_llm import resolve_session_llm_bind, session_llm_update_fields


def test_put_session_llm_route_is_registered():
    """Desktop PUT /sessions/{id}/llm must not 404 (handler was previously unregistered)."""
    from remedy.interfaces.api import create_app

    app = create_app(runtime=None, memory=None, api_key="")
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/sessions/{session_id}/llm" in paths


def test_explicit_req_pair_wins():
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(
        session=sess, req_provider="deepseek", req_model="deepseek-v4-flash"
    )
    assert p == "deepseek"
    assert m == "deepseek-v4-flash"


def test_sticky_session_ignores_model_only_foreign_id():
    """Global UI may send deepseek model while this tab is still Grok-bound."""
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(
        session=sess, req_provider=None, req_model="deepseek-v4-flash"
    )
    # Keep sticky Grok bind — do not cross-wire to DeepSeek from model alone.
    assert p == "xai"
    assert m == "grok-4.5"


def test_sticky_session_keeps_pair_when_no_req():
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(session=sess, req_provider=None, req_model=None)
    assert p == "xai"
    assert m == "grok-4.5"


def test_infer_provider_from_model_only():
    p, m = resolve_session_llm_bind(
        session=None, req_provider=None, req_model="deepseek-v4-flash"
    )
    assert p == "deepseek"
    assert m == "deepseek-v4-flash"


def test_session_llm_update_fields_pairs():
    f = session_llm_update_fields(provider=None, model="grok-4.5")
    assert f["llm_provider"] == "xai"
    assert f["model"] == "grok-4.5"
