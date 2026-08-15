"""Fatal LLM API errors must hard-stop (no soft-retry spam)."""

from __future__ import annotations

from remedy.core.agent_react_loop import _is_fatal_llm_api_error
from remedy.core.react_loop.errors import (
    is_billing_llm_api_error,
    is_thinking_tool_choice_error,
)
from remedy.core.react_loop.recovery import fatal_billing_error_message


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


def test_deepseek_force_tool_choice_stays_auto() -> None:
    """DeepSeek thinking models 400 on tool_choice=required — never send it."""
    from types import SimpleNamespace

    from remedy.core.providers import DeepSeekProvider
    from remedy.core.react_loop.build_request import build_step_request_body

    bind = SimpleNamespace(
        provider="deepseek",
        model="deepseek-chat",
        api_key="x",
        base_url="https://api.deepseek.com",
        adapter=lambda: DeepSeekProvider(),
    )
    runtime = SimpleNamespace(
        _force_tool_choice=True,
        _thinking_level="high",
        _tool_choice_required_blocked=False,
        _llm_max_output_tokens=256,
        _local_step_index=0,
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    body, _headers, _ep, _sse = build_step_request_body(
        runtime=runtime,
        bind=bind,
        adapter=DeepSeekProvider(),
        messages=[{"role": "user", "content": "read the file"}],
        step_tools=tools,
        step=0,
        user_message="read the file",
    )
    assert body.get("tools")
    assert body.get("tool_choice") == "auto"


def test_thinking_tool_choice_mismatch_is_recoverable() -> None:
    body = (
        '{"error":{"message":"Thinking mode does not support this tool_choice",'
        '"type":"invalid_request_error"}}'
    )
    assert is_thinking_tool_choice_error(body) is True
    assert _is_fatal_llm_api_error(400, body) is False
    assert is_thinking_tool_choice_error("invalid tools schema") is False


def test_generic_invalid_request_400_not_fatal() -> None:
    """Context / tools / messages 400s must soft-recover, not hard-stop."""
    body = (
        '{"error":{"type":"invalid_request_error","message":'
        '"This model\'s maximum context length is 128000 tokens."}}'
    )
    assert _is_fatal_llm_api_error(400, body) is False
    assert (
        _is_fatal_llm_api_error(
            400,
            '{"error":{"type":"invalid_request_error","message":"Invalid tools schema"}}',
        )
        is False
    )


_ANTHROPIC_CREDIT_BODY = (
    '{"type":"error","error":{"type":"invalid_request_error","message":'
    '"Your credit balance is too low to access the Anthropic API. '
    'Please go to Plans & Billing to upgrade or purchase credits."}}'
)


def test_anthropic_credit_balance_400_is_fatal() -> None:
    """Billing 400 must hard-stop — do not soft-retry 3× as a generic 400."""
    assert is_billing_llm_api_error(400, _ANTHROPIC_CREDIT_BODY) is True
    assert _is_fatal_llm_api_error(400, _ANTHROPIC_CREDIT_BODY) is True


def test_openai_insufficient_quota_is_fatal() -> None:
    body = '{"error":{"type":"insufficient_quota","message":"You exceeded your current quota."}}'
    assert is_billing_llm_api_error(429, body) is True
    assert _is_fatal_llm_api_error(429, body) is True


def test_http_402_is_billing_fatal() -> None:
    assert is_billing_llm_api_error(402, "payment required") is True
    assert _is_fatal_llm_api_error(402, "") is True


def test_billing_message_says_credits_not_api_key() -> None:
    msg = fatal_billing_error_message(
        status=400,
        safe_err="Your credit balance is too low",
        model_name="claude-opus-5",
        provider="anthropic",
    )
    low = msg.lower()
    assert "credits" in low
    assert "claude" in low and "max" in low
    assert "console.anthropic.com" in low
    assert "check model/api key" not in low
    generic = fatal_billing_error_message(
        status=429,
        safe_err="insufficient_quota",
        model_name="gpt-4o",
        provider="openai",
    )
    assert "console.anthropic.com" not in generic.lower()
