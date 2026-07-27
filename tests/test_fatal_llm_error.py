"""Fatal LLM API errors must hard-stop (no soft-retry spam)."""

from __future__ import annotations

from remedy.core.agent_react_loop import _is_fatal_llm_api_error


def test_404_model_not_found_is_fatal() -> None:
    body = (
        '{"code":"not-found","error":"The model deepseek-v4-flash does not exist '
        'or your team does not have access to it."}'
    )
    assert _is_fatal_llm_api_error(404, body) is True


def test_404_alone_is_fatal() -> None:
    assert _is_fatal_llm_api_error(404, "not found") is True


def test_transient_5xx_not_fatal() -> None:
    assert _is_fatal_llm_api_error(503, "service temporarily unavailable") is False
    assert _is_fatal_llm_api_error(500, "internal error") is False


def test_model_not_found_phrase_on_400() -> None:
    assert (
        _is_fatal_llm_api_error(
            400, '{"error":{"message":"model_not_found: invalid model xyz"}}'
        )
        is True
    )


def test_wrong_model_for_host_is_fatal() -> None:
    body = (
        '{"error":{"message":"The supported API model names are deepseek-v4-pro '
        'or deepseek-v4-flash, but you passed grok-4.5."}}'
    )
    assert _is_fatal_llm_api_error(400, body) is True
