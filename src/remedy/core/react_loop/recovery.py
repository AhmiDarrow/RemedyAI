"""Recovery / soft-fail user-facing messages for the ReAct loop."""

from __future__ import annotations


def fatal_model_error_message(
    *,
    status: int,
    safe_err: str,
    model_name: str,
    provider: str,
) -> str:
    """Hard-stop copy when the model/provider cannot succeed."""
    return (
        f"\n[LLM ERROR — HTTP {status}]\n"
        f"{safe_err[:500]}\n[END LLM ERROR]\n\n"
        f"**Cannot continue:** model `{model_name}` "
        f"(provider `{provider}`) is not available or this "
        f"account cannot use it.\n\n"
        "Pick a working model in the model picker / Settings "
        "(e.g. your previous Grok or DeepSeek id), then resend. "
        "This is not a tool-budget limit.\n"
    )


def repeated_provider_error_message(*, status: int, safe_err: str) -> str:
    return (
        f"\n[LLM ERROR — HTTP {status}]\n"
        f"{safe_err[:500]}\n[END LLM ERROR]\n\n"
        "Stopped after repeated provider errors. "
        "Check model/API key in Settings and try again "
        "(or say **continue** after switching models).\n"
    )


def soft_retry_notice(*, status: int, api_soft_failures: int, max_api_soft_failures: int) -> str:
    return (
        f"\n[LLM notice — HTTP {status}; "
        f"trying to finish from context "
        f"({api_soft_failures}/{max_api_soft_failures})]\n"
    )
