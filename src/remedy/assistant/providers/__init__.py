"""Pluggable calendar/mail providers (Google, Microsoft, Yahoo).

Phase 0: interfaces only — no live OAuth yet.
"""

from __future__ import annotations

from remedy.assistant.providers.base import (
    CalendarEvent,
    CalendarProvider,
    MailMessage,
    MailProvider,
    ProviderRegistry,
    get_provider_registry,
)

__all__ = [
    "CalendarEvent",
    "CalendarProvider",
    "MailMessage",
    "MailProvider",
    "ProviderRegistry",
    "get_provider_registry",
]
