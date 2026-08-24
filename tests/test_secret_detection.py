"""looks_like_secret — the guard on every durable write.

Seventeen call sites depend on this: profile facts, soul updates, the epistemic
graph, /remember, the partner route, and post-turn auto-extraction. A miss is
not a one-off leak — the credential lands in memory that survives the session
and gets replayed into later prompts.

It used to miss the most likely secret of all. The pattern body was
``sk-[a-z0-9]{10,}``, which stops at the first hyphen, so a real Anthropic key
(``sk-ant-api03-…``) matched nothing at all. Every provider Remedy talks to is
spelled out below so that cannot happen quietly again.
"""

from __future__ import annotations

import pytest

from remedy.memory.partner_memory import looks_like_secret


# Assemble at runtime so GitHub push-protection does not treat the fixtures
# as live secrets. The concatenated values still match looks_like_secret.
def _tok(*parts: str) -> str:
    return "".join(parts)


CREDENTIALS = {
    "anthropic": _tok("sk-ant-", "api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKKLLLL"),
    "openai-project": _tok("sk-proj-", "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"),
    "openai-classic": _tok("sk-", "AAAABBBBCCCCDDDDEEEEFFFFGGGG"),
    "xai": _tok("xai-", "abcdefghijklmnop1234"),
    "github-pat": _tok("ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "github-oauth": _tok("gho_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "github-fine-grained": _tok("github_pat_", "11ABCDEFG0abcdefghijklmnop"),
    "gitlab": _tok("glpat-", "ABCDEFGHIJKLMNOPQRST"),
    "huggingface": _tok("hf_", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"),
    "slack-bot": _tok("xoxb-", "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
    "slack-app": _tok("xapp-", "1-A012345-1234567890-abcdef"),
    "google": _tok("AIza", "SyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"),
    "aws-access-key": _tok("AKIA", "IOSFODNN7EXAMPLE"),
    "aws-session-key": _tok("ASIA", "IOSFODNN7EXAMPLE"),
    "aws-secret": _tok("AWS_SECRET_ACCESS_KEY=", "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"),
    "stripe": _tok("sk_live_", "ABCDEFGHIJKLMNOPQRSTUVWX"),
    "sendgrid": _tok("SG.", "ABCDEFGHIJKLMNOPQRSTUV.WXYZ0123456789"),
    "openssh-private-key": _tok("-----BEGIN ", "OPENSSH PRIVATE KEY-----"),
    "rsa-private-key": _tok("-----BEGIN ", "RSA PRIVATE KEY-----"),
    "bearer-header": _tok("Authorization: ", "Bearer abc123def456"),
    "named-password": "password: hunter2-correct-horse-battery",
    "named-api-key": "my api_key is 12345",
}

#: Ordinary things a person actually says. A guard that eats these silently
#: throws away the memory Remedy was asked to keep.
NOT_CREDENTIALS = [
    "The parser is recursive descent",
    "She prefers dark mode and terse replies",
    "Remember to call the dentist on Tuesday",
    "The build uses skip-tests in CI",
    "asking for a passwordless login flow",
    "Use the sk-learn library for clustering",
    "The secret to a good sourdough is time",
    "ghost writing the release notes",
    "",
    "   ",
]


@pytest.mark.parametrize("provider", sorted(CREDENTIALS))
def test_a_real_credential_is_caught(provider):
    assert looks_like_secret(CREDENTIALS[provider]) is True


@pytest.mark.parametrize("provider", sorted(CREDENTIALS))
def test_a_credential_is_caught_inside_a_sentence(provider):
    """They arrive pasted mid-message, not on a line of their own."""
    text = f"here you go, use {CREDENTIALS[provider]} for the API"
    assert looks_like_secret(text) is True


@pytest.mark.parametrize("text", NOT_CREDENTIALS)
def test_ordinary_text_is_not_mistaken_for_a_credential(text):
    assert looks_like_secret(text) is False


def test_a_long_high_entropy_token_is_caught_without_a_known_prefix():
    """New providers appear faster than prefix lists get updated."""
    assert looks_like_secret("Zx9Qw3Er7Ty1Ui5Op8As2Df6Gh0Jk4Lz3Xc7Vb1Nm5" * 1) is True


def test_a_long_run_of_real_words_is_not_key_material():
    assert (
        looks_like_secret(
            "thisisareallylongsentencewithoutspacesbutstillmadeofwords"
        )
        is False
    )
