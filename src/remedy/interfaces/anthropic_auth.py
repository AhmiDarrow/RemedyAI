"""Anthropic credential classification (Console API vs Claude Max / OAuth).

Remedy must not store or send Claude Code / Max OAuth tokens
(``sk-ant-oat…``). Those are only valid inside official Claude Code.
"""

from __future__ import annotations

from typing import Any

SUBSCRIPTION_OAUTH_PREFIXES = (
    "sk-ant-oat",
    "sk-ant-sid",
)

CONSOLE_API_PREFIX = "sk-ant-api"

SUBSCRIPTION_TOKEN_MESSAGE = (
    "That looks like a Claude Code / Max login token (sk-ant-oat…), "
    "not a Console API key. Anthropic only allows those tokens inside "
    "official Claude Code — pasting them here can lock the account.\n\n"
    "To use Claude in Remedy: paste a Console API key (sk-ant-api…) "
    "from https://console.anthropic.com/settings/keys and add API "
    "credits. A Claude Pro / Max plan does not pay for this API."
)


def classify_anthropic_secret(key: str | None) -> str:
    """Return console_api | oauth_subscription | session | empty | other."""
    k = (key or "").strip()
    if not k:
        return "empty"
    low = k.lower()
    if low.startswith(SUBSCRIPTION_OAUTH_PREFIXES) or low.startswith("sk-ant-oat"):
        return "oauth_subscription"
    if low.startswith("sk-ant-sid"):
        return "session"
    if low.startswith(CONSOLE_API_PREFIX):
        return "console_api"
    if low.startswith("sk-ant-"):
        return "other"
    return "other"


def is_subscription_oauth_token(key: str | None) -> bool:
    kind = classify_anthropic_secret(key)
    return kind in ("oauth_subscription", "session")


def reject_if_subscription_token(key: str | None) -> None:
    """Raise ValueError when the secret is a Max / Claude Code OAuth token."""
    if is_subscription_oauth_token(key):
        raise ValueError(SUBSCRIPTION_TOKEN_MESSAGE)


def public_anthropic_status(*, home=None) -> dict[str, Any]:
    """Booleans only — never the secret."""
    from remedy.interfaces.secret_store import get_provider_secret

    secret = ""
    with __import__("contextlib").suppress(Exception):
        secret = str(get_provider_secret("anthropic", home=home) or "")
    kind = classify_anthropic_secret(secret)
    return {
        "provider": "anthropic",
        "secret_kind": kind,
        "has_console_key": kind == "console_api",
        "has_subscription_token": kind in ("oauth_subscription", "session"),
        "connected_api": kind == "console_api",
    }
