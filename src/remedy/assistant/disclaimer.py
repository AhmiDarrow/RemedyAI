"""Privacy, AI, and money disclosures for personal assistant.

Plain-language notices shown in Settings and returned by the API.
Not legal advice — product transparency for user trust.
"""

from __future__ import annotations

# ── Money (existing) ────────────────────────────────────────────────────────

MONEY_DISCLAIMER_SHORT = (
    "Budgeting and debt tracking help you organize numbers you enter — "
    "not personalized financial, tax, or legal advice."
)

MONEY_DISCLAIMER_FULL = (
    "Remedy’s budget, bill, and debt tools are for household organization only. "
    "You enter balances, rates, and categories; Remedy does simple math and reminders. "
    "This is not personalized financial, investment, tax, or legal advice, and Remedy "
    "is not a fiduciary or licensed advisor. For decisions about loans, investments, "
    "bankruptcy, or taxes, consult a qualified professional."
)

# ── AI + connected accounts (Gmail / Calendar / future mail) ────────────────

PRIVACY_AI_SHORT = (
    "Connected mail/calendar is handled on your PC. When chat uses a cloud AI "
    "provider, only tool results you trigger may be sent to that provider — not "
    "your OAuth tokens."
)

PRIVACY_AI_FULL = (
    "### How Remedy treats your account data\n\n"
    "**Local-first.** OAuth tokens for Google (and later Microsoft/Yahoo) are stored "
    "only under your user profile (`~/.remedy/auth/`). On Windows they are encrypted "
    "with DPAPI (your Windows user) and ACL-hardened. Tokens are never written to "
    "chat history, logs, or config.toml.\n\n"
    "**No Remedy cloud for mail/calendar.** Remedy does not operate a cloud mailbox. "
    "API calls go from this PC to Google (or other providers) and back.\n\n"
    "**You are talking to an AI.** When you chat with Remedy, the **language model "
    "provider you chose** (e.g. xAI, OpenAI, or a local Ollama model) receives the "
    "messages and tool results needed to answer. If a tool reads mail or calendar, "
    "snippets or fields returned by that tool can be included in the provider request "
    "for that turn. **OAuth tokens and client secrets are never sent to the model.**\n\n"
    "**Minimize by design.** Tools prefer short subject/from/snippet lists over full "
    "message bodies. Full body is only fetched when a read tool is used. Disconnect "
    "revokes/clears local tokens.\n\n"
    "**Your controls.** Connect/Disconnect in Settings; disable Personal assistant; "
    "choose a local model to keep inference on-device; approval modes for high-impact "
    "tools. Budget numbers you type stay local unless you ask the AI about them.\n\n"
    "**Not a guarantee against all risk.** A compromised Windows account, malware as "
    "your user, or a provider outage/policy change are outside Remedy’s control. We "
    "optimize for owner trust and least privilege on this machine."
)

PRIVACY_AI_CHECKBOX = (
    "I understand Remedy is an AI assistant: connected mail/calendar tokens stay on "
    "this PC; chat may send tool results (not tokens) to my chosen AI provider."
)

ACCOUNT_CONNECT_CHECKBOX = (
    "I allow Remedy to access this account via official OAuth for mail/calendar "
    "tools I use (I can Disconnect anytime)."
)

# Scopes we request — human readable for Settings
GOOGLE_SCOPE_PLAIN = (
    "Gmail: read messages and create drafts (no silent send). "
    "Calendar: view and create events. "
    "Basic profile email to label the account."
)


def privacy_bundle() -> dict[str, str]:
    """Public strings for Settings / GET status (no secrets)."""
    return {
        "privacy_ai_short": PRIVACY_AI_SHORT,
        "privacy_ai_full": PRIVACY_AI_FULL,
        "privacy_ai_checkbox": PRIVACY_AI_CHECKBOX,
        "account_connect_checkbox": ACCOUNT_CONNECT_CHECKBOX,
        "money_disclaimer_short": MONEY_DISCLAIMER_SHORT,
        "money_disclaimer_full": MONEY_DISCLAIMER_FULL,
        "google_scopes_plain": GOOGLE_SCOPE_PLAIN,
    }
