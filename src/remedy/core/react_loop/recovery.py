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


def fatal_billing_error_message(
    *,
    status: int,
    safe_err: str,
    model_name: str,
    provider: str,
) -> str:
    """Hard-stop copy when the account is out of credits / quota."""
    header = (
        f"\n[LLM ERROR — HTTP {status}]\n"
        f"{safe_err[:500]}\n[END LLM ERROR]\n\n"
        f"**Cannot continue:** provider `{provider}` "
        f"(model `{model_name}`) rejected this request because "
        "the account is out of credits or over quota.\n\n"
    )
    if str(provider or "").strip().lower() == "anthropic":
        return (
            header
            + "This is the **Anthropic API** at console.anthropic.com "
            "(prepaid API credits). It is **not** your Claude Pro / Max "
            "/ Claude Code weekly usage limit — that screenshot is a "
            "different wallet and does not pay for this request.\n\n"
            "Add API credits at https://console.anthropic.com/settings/billing, "
            "or switch to a provider that still has quota "
            "(e.g. DeepSeek or xAI) in Settings, then resend.\n"
        )
    return (
        header
        + "This is billing, not a bad API key or a wrong model name. "
        "Add credits on that provider's Plans & Billing page, or "
        "switch to a provider that still has quota "
        "(e.g. DeepSeek or xAI) in Settings, then resend.\n"
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
