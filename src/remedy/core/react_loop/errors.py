"""Fatal vs recoverable LLM API error classification for the ReAct loop."""

from __future__ import annotations

# Account/quota — retrying the same key cannot succeed this turn.
_BILLING_PHRASES = (
    "credit balance",
    "too low to access",
    "purchase credits",
    "plans & billing",
    "plans and billing",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "exceeded your quota",
    "billing hard limit",
    "out of credits",
    "no credits remaining",
    "payment required",
    "you have been billed",
    "spend limit",
)


def is_billing_llm_api_error(status: int, body: str) -> bool:
    """True when the host rejected the request for credits / quota / billing.

    Anthropic returns HTTP 400 ``invalid_request_error`` with
    ``credit balance is too low`` — that must not soft-retry as a generic 400.
    OpenAI-style ``insufficient_quota`` (often 429) and HTTP 402 are the same class.
    """
    if status == 402:
        return True
    low = (body or "").lower()
    return any(p in low for p in _BILLING_PHRASES)


def is_fatal_llm_api_error(status: int, body: str) -> bool:
    """True when retrying the same model/request cannot succeed.

    e.g. HTTP 404 model-not-found — soft-continue spam looks like a stuck agent.
    Do **not** treat generic ``invalid_request_error`` 400s as fatal (context
    length, tool schema, message format) — those need soft recovery, not a hard stop.
    Billing / quota 400s *are* fatal (see ``is_billing_llm_api_error``).
    """
    if status in (404, 410, 422):
        return True
    if is_billing_llm_api_error(status, body):
        return True
    low = (body or "").lower()
    if "only authorized for use with claude code" in low or "oauth authentication is currently not supported" in low:
        return True
    # Wrong model name for this host (e.g. grok id on DeepSeek API)
    if "supported api model" in low or "supported models are" in low:
        return True
    model_fatal_phrases = (
        "model_not_found",
        "invalid model",
        "unknown model",
        "model is not available",
        "no such model",
        "unsupported model",
        "does not exist",
        "not have access",
    )
    # Model-ish permanent errors only when the body is about models.
    if "model" in low and any(p in low for p in model_fatal_phrases):
        return True
    # OpenAI-style invalid_request_error is only fatal when clearly model-related.
    return bool("invalid_request_error" in low and "model" in low and any(p in low for p in ("model_not_found", "invalid model", "unknown model", "does not exist", "not found", "unsupported")))


def is_thinking_tool_choice_error(body: str) -> bool:
    """True when the host rejects tool_choice while thinking/reasoning is on.

    Class of provider mismatch (not a one-off model). Recover by turning
    thinking off and retrying the same tool-required request.
    """
    low = (body or "").lower()
    if "tool_choice" not in low:
        return False
    return any(
        p in low
        for p in (
            "thinking",
            "reasoning",
            "does not support this tool_choice",
            "thinking mode does not support",
        )
    )


# Back-compat alias used by older tests / imports
_is_fatal_llm_api_error = is_fatal_llm_api_error


def is_local_host_loading_error(
    status: int,
    body: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when a 502/503 is the *local* RMB host still loading weights.

    llama-server answers ``503 Loading model`` for the seconds after a
    (re)start; the loop waits for the host and retries the same body. A cloud
    provider's 503 can carry the same words ("Service temporarily
    unavailable") but is not our host — treating it as one woke RMB, unloaded
    the vision model and started a GGUF on an xAI outage. The binding decides.
    """
    if status not in (502, 503):
        return False
    low = (body or "").lower()
    if not (
        "loading model" in low or "unavailable" in low or "not ready" in low
    ):
        return False
    try:
        from remedy.core.local_agent_optimize import is_local_binding

        return bool(is_local_binding(provider, model, base_url))
    except Exception:
        return (provider or "").strip().lower() in ("rmb", "ollama", "llamacpp", "local")
