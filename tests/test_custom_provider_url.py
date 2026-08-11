"""Custom provider base_url must reflect the saved URL, not the 5001 placeholder."""

from __future__ import annotations

from remedy.interfaces.config import public_provider_catalog


def test_custom_placeholder_when_unsaved() -> None:
    cat = {p["id"]: p for p in public_provider_catalog({})}
    assert cat["custom"]["base_url"] == "http://127.0.0.1:5001/v1"


def test_custom_saved_url_wins() -> None:
    cat = {
        p["id"]: p
        for p in public_provider_catalog(
            {
                "llm_provider": "custom",
                "llm_base_url": "http://127.0.0.1:1234/v1",
            }
        )
    }
    assert cat["custom"]["base_url"] == "http://127.0.0.1:1234/v1"


def test_custom_url_not_leaked_to_other_provider() -> None:
    cat = {
        p["id"]: p
        for p in public_provider_catalog(
            {
                "llm_provider": "openai",
                "llm_base_url": "http://127.0.0.1:1234/v1",
            }
        )
    }
    # When the active provider is not custom, the custom card keeps its
    # placeholder default (do not leak another provider's URL into it).
    assert cat["custom"]["base_url"] == "http://127.0.0.1:5001/v1"
