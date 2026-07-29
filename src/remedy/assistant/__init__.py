"""Personal assistant layer (additive): linked accounts, budget/debts, briefs.

Separate from computer-use execution. Calendar uses official Google OAuth;
money tools are local organization only — not financial advice.
"""

from __future__ import annotations

from remedy.assistant.disclaimer import (
    MONEY_DISCLAIMER_FULL,
    MONEY_DISCLAIMER_SHORT,
    PRIVACY_AI_FULL,
    PRIVACY_AI_SHORT,
    privacy_bundle,
)
from remedy.assistant.store import (
    AssistantStore,
    get_assistant_store,
    reset_assistant_store,
)

__all__ = [
    "AssistantStore",
    "get_assistant_store",
    "reset_assistant_store",
    "MONEY_DISCLAIMER_SHORT",
    "MONEY_DISCLAIMER_FULL",
    "PRIVACY_AI_SHORT",
    "PRIVACY_AI_FULL",
    "privacy_bundle",
]
